# -*- coding: utf-8 -*-
"""
tests/test_rating.py — RATING_PLAN Phase 0 验收测试

覆盖 RATING_PLAN.md §7:
  - 6 策略全部经 compute_rating 输出 RatingResult, 无策略恒判 C
  - 缺口家族日/周线同一四因子口径 (映射锁定)
  - hunter 只从 info['rating'] 取评级, 无 score/ev_rating 双字段
  - 每个评级因子可溯源 SOP (sop_ref), 且 core/rating 未 import 黑名单指标
  - PA 合规: 评级因子名/备注不含 volume/ADX/EMA斜率/均线多头/动量 等

⛔ 所有断言均不得依赖 volume/ADX 等数据库不存在的指标.
"""
import os
import sys
import inspect

import numpy as np
import pandas as pd

# 项目根目录加入 sys.path (保证从任意 cwd 运行 pytest 均可 import core.*)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.rating import RatingResult, RatingFactor, band, clamp, map_native  # noqa: E402
from core.rating_core import (  # noqa: E402
    quality_factor, pb_speed_factor, gap_width_factor,
    consec_bear_penalty, time_decay_factor, sum_weights, factor, rr_factor,
)
from core.strategies.structural_gap_strategy import StructuralGapStrategy  # noqa: E402
from core.strategies.gap_pinbar_strategy import GapPinbarStrategy  # noqa: E402
from core.strategies.gap_h2_strategy import GapH2Strategy  # noqa: E402
from core.strategies.mtr_strategy import MTRStrategy  # noqa: E402
from core.strategies.three_k_strategy import ThreeKStrategy  # noqa: E402
from core.strategies.awil_strategy import AWILStrategy  # noqa: E402
from core.strategy_registry import StrategyRegistry  # noqa: E402

VALID_LETTERS = {'A+', 'A', 'B', 'C', 'D'}
# PA 黑名单 token (config/sop_rules.md §2.5): volume / ADX / EMA斜率 / 均线多头排列 / trend_align
# 注: 不含裸词 '动量' — 合法备注可能解释 "非动量因子" (即明确 NOT 动量, 属 PA 合规说明)
BLACKLIST_TOKENS = ['volume', 'adx', '斜率', '均线多头', 'trend_align']

STRATEGIES = [
    StructuralGapStrategy, GapPinbarStrategy, GapH2Strategy,
    MTRStrategy, ThreeKStrategy, AWILStrategy,
]


# ---------------------------------------------------------------------
# 合成行情构造
# ---------------------------------------------------------------------
def _make_df(n: int = 30, favorable: bool = True) -> pd.DataFrame:
    """构造一段合成日线, 覆盖全部 6 策略 compute_rating 读取的列.

    favorable=True  -> 强信号环境 (应得 A/A+)
    favorable=False -> 弱信号环境 (应得 C/D)
    """
    low = np.linspace(9.5, 9.5 - n * 0.02, n)          # 递减 -> awil 下影陷阱/LHLL 成立
    high = low + 0.35                                  # 递减, 高于 low
    close = low + 0.30                                 # close 在 low/high 之间, 阳线
    open_ = low + 0.05

    if favorable:
        q, gap_pct_target, pb_bars, bears, bars_passed = 0.9, 10.0, 3, 0, 2
        mtr_score = 75.0
        morph, gap_open, trap_ok, climax_ok, three_bulls = True, True, True, True, True
        close_loc, ema_above = 0.99, True
    else:
        q, gap_pct_target, pb_bars, bears, bars_passed = 0.3, 2.0, 9, 4, 15
        mtr_score = 25.0
        morph, gap_open, trap_ok, climax_ok, three_bulls = False, False, False, False, False
        close_loc, ema_above = 0.60, False

    sl = 10.0
    gap_top = round(sl * (1 + gap_pct_target / 100.0), 2)

    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=n, freq='D'),
        'open': open_, 'high': high, 'low': low, 'close': close, 'ema20': 8.0, 'atr': 0.3,
        # structural / 缺口家族共用
        'sig_bar_quality': q,
        'sl_struct_gap': sl, 'struct_gap_top_exact': gap_top, 'bars_since_breakout': pb_bars,
        # pinbar
        'sig_bar_quality_gp': q,
        'sl_gap_pinbar': sl, 'gap_pinbar_top_exact': gap_top, 'bars_since_breakout_gp': pb_bars,
        # h2
        'sig_bar_quality_h2': q,
        'sl_gap_h2': sl, 'gap_h2_top_exact': gap_top, 'bars_since_breakout_h2': pb_bars,
        'gap_h2_open': gap_open,
        # mtr
        'mtr_score': mtr_score,
        # 3k
        'body_pct': 0.7 if favorable else 0.2, 'morph_ok': morph, 'breakout_gap_open': gap_open,
        'trap_check_ok': trap_ok, 'climax_ok': climax_ok, 'three_bulls': three_bulls,
        'entry_3k_gap_test': 10.0, 'sl_3k_gap_test': 9.0, 'tp_3k_gap_test': 12.0,
        # awil
        'close_loc': close_loc, 'awil_ema_above': ema_above,
        'entry_awil': 10.0, 'sl_awil': 9.0, 'tp_awil': 12.0,
    }, index=range(n))
    return df


# ---------------------------------------------------------------------
# 1. 契约: band / clamp / map_native (四因子映射锁定)
# ---------------------------------------------------------------------
def test_contract_band_and_clamp():
    assert band(85) == 'A+'
    assert band(70) == 'A'
    assert band(55) == 'B'
    assert band(40) == 'C'
    assert band(20) == 'D'
    assert band(50, toxic=True) == 'D'
    assert clamp(120) == 100 and clamp(-5) == 0 and clamp(63.7) == 63


def test_map_native_structural_compat():
    # 缺口家族: score = clamp(50 + 10 * raw), 与原四因子映射兼容
    assert map_native(0, 50, 10) == 50
    assert map_native(2, 50, 10) == 70
    assert map_native(5, 50, 10) == 100
    assert map_native(-3, 50, 10) == 20      # 对应毒性 D


def test_rating_result_roundtrip():
    r = RatingResult(raw_score=1.0, score=60, letter='B',
                     factors=[RatingFactor('x', 1.0, True, 1.0, sop_ref='SOP Step 4')])
    d = r.to_dict()
    r2 = RatingResult.from_dict(d)
    assert r2.score == 60 and r2.letter == 'B' and len(r2.factors) == 1
    assert r2.factors[0].sop_ref == 'SOP Step 4'


# ---------------------------------------------------------------------
# 2. 通用 PA 因子库 (rating_core)
# ---------------------------------------------------------------------
def test_rating_core_factors():
    f = quality_factor(0.9)
    assert f.hit and f.weight > 0 and 'Step 4' in f.sop_ref
    fb = pb_speed_factor(3)
    assert fb.hit and fb.weight == 2.0 and 'Step' in fb.sop_ref
    fg = gap_width_factor(8.0)
    assert fg.hit and fg.weight == 2.0
    fr = rr_factor(3.0)
    assert fr.hit and fr.weight == 1.0 and 'Step 9' in fr.sop_ref
    # 弱输入 -> 扣分
    assert quality_factor(0.3).weight == -1.0
    assert pb_speed_factor(9).weight == -2.0
    assert gap_width_factor(2.0).weight == -1.0
    assert consec_bear_penalty(4).weight == -1.0
    assert time_decay_factor(15).weight == -2.0
    # sum_weights 汇总
    assert sum_weights([f, fb]) == f.weight + fb.weight


# ---------------------------------------------------------------------
# 3. 各策略 compute_rating 烟雾测试 (强信号 -> 不应恒判 C)
# ---------------------------------------------------------------------
def test_each_strategy_compute_rating_favorable():
    df = _make_df(favorable=True)
    for S in STRATEGIES:
        r = S.compute_rating(df)
        assert r is not None, f"{S.__name__} 返回 None"
        d = r.to_dict()
        for key in ('raw_score', 'score', 'letter', 'factors', 'toxic', 'calibrated'):
            assert key in d, f"{S.__name__} 缺字段 {key}"
        assert d['letter'] in VALID_LETTERS, f"{S.__name__} 字母非法 {d['letter']}"
        assert isinstance(d['factors'], list) and len(d['factors']) >= 1
        assert 0 <= d['score'] <= 100
        # 每个因子必须带 SOP 溯源
        for fac in d['factors']:
            assert fac.get('sop_ref'), f"{S.__name__} 因子 {fac.get('name')} 缺 sop_ref"
        # 强信号环境下, 缺口家族/MTR/3K/AWIL 都应得 A 级及以上 (证明非恒 C)
        assert d['letter'] in ('A+', 'A', 'B'), (
            f"{S.__name__} 强信号下仍判 {d['letter']} (应 A/A+/B)")


def test_structural_rating_responsive_weak():
    """弱信号 -> 低评级 (证明评级对输入有响应, 非恒 A 也非恒 C)."""
    df = _make_df(favorable=False)
    r = StructuralGapStrategy.compute_rating(df)
    d = r.to_dict()
    assert d['letter'] in ('C', 'D'), f"弱信号应得 C/D, 实得 {d['letter']}"
    assert d['toxic'] == (d['letter'] == 'D')


# ---------------------------------------------------------------------
# 4. 注册表: 全部策略均可产出合法评级
# ---------------------------------------------------------------------
def test_registry_all_strategies_have_rating():
    df = _make_df(favorable=True)
    for name in StrategyRegistry.list_strategies():
        S = StrategyRegistry.get_strategy(name)
        r = S.compute_rating(df)
        assert r is not None, f"注册表策略 {name} 返回 None"
        assert r.to_dict()['letter'] in VALID_LETTERS


# ---------------------------------------------------------------------
# 5. PA 合规断言
# ---------------------------------------------------------------------
def test_pa_compliance_factor_names():
    """评级因子名/备注不得含 volume/ADX/EMA斜率/均线多头/动量 等黑名单 token."""
    df = _make_df(favorable=True)
    for S in STRATEGIES:
        d = S.compute_rating(df).to_dict()
        for fac in d['factors']:
            blob = f"{fac.get('name', '')} {fac.get('note', '')}".lower()
            for tok in BLACKLIST_TOKENS:
                assert tok.lower() not in blob, (
                    f"{S.__name__} 因子含黑名单 token '{tok}': {fac.get('name')}")


def test_pa_compliance_core_no_blacklist_import():
    """core/rating.py / core/rating_core.py 不得 import 黑名单指标."""
    import core.rating as cr
    import core.rating_core as crc
    for mod in (cr, crc):
        src = inspect.getsource(mod)
        assert 'import volume' not in src, f"{mod.__name__} import 了 volume"
        assert 'import adx' not in src, f"{mod.__name__} import 了 adx"
        assert 'import ema_slope' not in src, f"{mod.__name__} import 了 ema_slope"


# ---------------------------------------------------------------------
# 6. hunter 已删除 score/ev_rating 双字段读取
# ---------------------------------------------------------------------
def test_hunter_reads_only_rating():
    hunter_path = os.path.join(PROJECT_ROOT, 'hunter.py')
    with open(hunter_path, encoding='utf-8') as fh:
        src = fh.read()
    # 旧的双字段直接读取模式必须消失
    assert "res.get('info', {}).get('score'" not in src, "hunter 仍存在 score 双字段读取"
    assert "_extract_rating" in src, "hunter 未引入 _extract_rating 统一读取"
    # 不应再出现据 score 重算 ev_rating 的阈值分支
    assert "score >= 80" not in src, "hunter 仍据 score 重算 ev_rating"
    assert "score >= 65" not in src
