# -*- coding: utf-8 -*-
"""
tools/rating_calibration_backtest.py — 评级体系校准回测 v2 (专业修订版)

方法学 (相对 v1 的关键修正):
  * 统一成本敏感退出模型 (core/backtest_engine.simulate_trade_unified):
      全策略同一 Buy-Stop 入场 + 初始止损 + 受控 2R 目标 + A股成本(印花税0.05%卖/佣金0.03%双边, 对齐 gap_h2_backtest.py 已验证基线)。
      跨策略可比, 净胜率/净EV 直接可用。
  * 缺口家族生命周期三过滤 (INVALIDATED/VOIDED/TIMEOUT): 等待期缺口被补/止盈先达/超时撤单, 不计入分母。
  * 训练/测试集切分 (防样本内过拟合):
      全历史信号按日期前60%为 TRAIN (推因子权重), 后40%为 TEST (验档位)。
      报告明确区分 [全样本/内样本] 与 [测试集/样本外] 两类结果。
  * 统计显著性: 每档附 Wilson 95% 置信区间; 标注 A+ 档是否显著高于整体基线。
  * 年度/regime 分层: 净胜率按自然年拆分, 检查市况稳定性。
  * 逐信号数据集 dump (calibration_signals.jsonl), 分析可复用, 无需重跑昂贵模拟。

用法:
  python tools/rating_calibration_backtest.py [--limit N] [--strategies s1,s2]
        [--weekly/--no-weekly] [--risk-mult 2.0] [--max-hold 60]
        [--analyze-only]            # 仅对已有 jsonl 重做分析(不重跑模拟)
        [--out config/rating_factors.json] [--report calibration_report_v2.txt]
        [--signals calibration_signals.jsonl]
"""
import os
import sys
import json
import argparse
import importlib
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)
from core.paths import ensure_importable
ensure_importable()

from core.backtest_engine import simulate_trade_unified, wilson_ci
from config import settings

# ============================================================
# 策略配置 (base, module, cls, tf, signal_col, signal_kind,
#           entry_col, sl_col, tp_col, risk_mult, max_hold)
# MTR 的 sl 用 min(sl_a, sl_b)
# ============================================================
_GAP_FAMILY = [
    ('structural_gap', 'core.strategies.structural_gap_strategy', 'StructuralGapStrategy',
     'signal_struct_gap_confirm', 'bool', 'entry_struct_gap', 'sl_struct_gap', 'tp_struct_gap', 2.0, 60),
    ('gap_pinbar', 'core.strategies.gap_pinbar_strategy', 'GapPinbarStrategy',
     'signal_gap_pinbar', 'bool', 'entry_gap_pinbar', 'sl_gap_pinbar', 'tp_gap_pinbar', 2.0, 60),
    ('gap_h2', 'core.strategies.gap_h2_strategy', 'GapH2Strategy',
     'signal_gap_h2', 'bool', 'entry_gap_h2', 'sl_gap_h2', 'tp_gap_h2', 2.0, 60),
]
_GAP_BASES = {b[0] for b in _GAP_FAMILY}  # 启用生命周期三过滤的策略
_DAILY_ONLY = [
    ('mtr', 'core.strategies.mtr_strategy', 'MTRStrategy',
     'mtr_stage', 'eq:SETUP_READY', 'mtr_entry_price', 'mtr_L1_price', 'mtr_TL_price', 2.0, 30),
    ('three_k', 'core.strategies.three_k_strategy', 'ThreeKStrategy',
     'signal_3k_gap_test', 'bool', 'entry_3k_gap_test', 'sl_3k_gap_test', 'tp_3k_gap_test', 2.0, 60),
    ('awil', 'core.strategies.awil_strategy', 'AWILStrategy',
     'signal_awil', 'bool', 'entry_awil', 'sl_awil', 'tp_awil', 2.0, 60),
]

LIMIT_DAILY = 1500
LIMIT_WEEKLY = 800
TRAIN_RATIO = 0.60  # 前 60% 时间为训练集

# A股主要指数(沪深300/上证)年收益方向的近似牛熊标签, 用于 regime 分层。
# 标注为"近似", 可据具体基准人工校正。
YEAR_REGIME = {
    '2011': 'bear', '2012': 'bear', '2013': 'bull', '2014': 'bull', '2015': 'bull',
    '2016': 'bear', '2017': 'range', '2018': 'bear', '2019': 'bull', '2020': 'bull',
    '2021': 'bull', '2022': 'bear', '2023': 'range', '2024': 'bull', '2025': 'bull',
    '2026': 'range',
}
REGIME_CN = {'bull': '牛市', 'bear': '熊市', 'range': '震荡'}


def build_configs(include_weekly: bool, filter_names=None):
    cfgs = []
    for base, module, cls, scol, skind, ec, slc, tpc, rm, mh in _GAP_FAMILY + _DAILY_ONLY:
        if filter_names and base not in filter_names:
            continue
        cfgs.append(_make_cfg(base, module, cls, 'daily', scol, skind, ec, slc, tpc, rm, mh, LIMIT_DAILY))
    if include_weekly:
        for base, module, cls, scol, skind, ec, slc, tpc, rm, mh in _GAP_FAMILY:
            if filter_names and base not in filter_names:
                continue
            cfgs.append(_make_cfg(base, module, cls, 'weekly', scol, skind, ec, slc, tpc, rm, mh, LIMIT_WEEKLY))
    return cfgs


def _make_cfg(base, module, cls, tf, scol, skind, ec, slc, tpc, rm, mh, limit):
    return {
        'key': f"{base}_{tf}", 'base': base, 'module': module, 'cls': cls, 'tf': tf,
        'signal_col': scol, 'signal_kind': skind,
        'entry_col': ec, 'sl_col': slc, 'tp_col': tpc,
        'sl_a': slc, 'sl_b': tpc, 'risk_mult': rm, 'max_hold': mh,
        'limit': limit,
        'lifecycle_filters': base in _GAP_BASES,  # 缺口家族启用三过滤
    }


# ============================================================
# Worker
# ============================================================
def worker_stock(args):
    code, cfg, limit = args
    try:
        mod = importlib.import_module(cfg['module'])
        StratCls = getattr(mod, cfg['cls'])
        from core.data_provider import get_stock_data
        from core.calculator import add_indicators

        df = get_stock_data(code, limit=limit, timeframe=cfg['tf'])
        if df is None or len(df) < 100:
            return []
        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        df = df.sort_values('date').reset_index(drop=True)
        df = add_indicators(df)
        strat = StratCls()
        df = strat.calculate_signals(df)

        # 起始日期过滤 (计算指标/因子时用全历史, 仅截断信号与回测区间)
        sd = cfg.get('start_date', '')
        if sd:
            df = df[df['date'] >= sd].reset_index(drop=True)
            if len(df) < 50:
                return []

        scol = cfg['signal_col']
        if scol not in df.columns:
            return []
        if cfg['signal_kind'] == 'bool':
            raw_idx = [i for i, v in enumerate(df[scol]) if v]
        else:
            sval = cfg['signal_kind'].split(':', 1)[1]
            raw_idx = [i for i, v in enumerate(df[scol]) if str(v) == sval]

        # 信号级评级校准: 保留全部独立信号, 不做同日去重。
        # (去重会按"最优 R:R"保留, 而 R:R 本身是评级因子 → 循环偏差, 污染区分度公正性)
        sig_idx = sorted(raw_idx)

        need = [cfg['entry_col'], cfg['sl_col']]
        if cfg['base'] == 'mtr':
            need = [cfg['entry_col'], cfg['sl_a'], cfg['sl_b']]
        if not all(c in df.columns for c in need):
            return []

        records = []
        for idx in sig_idx:
            sub = df.iloc[:idx + 1]   # 切片 -> 评级与实盘信号时刻一致
            try:
                rr = StratCls.compute_rating(sub)
            except Exception:
                continue
            if rr is None:
                continue
            rdict = rr.to_dict()

            row = df.iloc[idx]
            if cfg['base'] == 'mtr':
                entry_ref = float(row[cfg['entry_col']])
                sl = float(min(row[cfg['sl_a']], row[cfg['sl_b']]))
                tp = None  # MTR 用 2R
            else:
                entry_ref = float(row[cfg['entry_col']])
                sl = float(row[cfg['sl_col']])
                tp = float(row[cfg['tp_col']]) if cfg['tp_col'] in row and pd.notna(row[cfg['tp_col']]) else None
            if not (entry_ref > 0 and sl > 0 and sl < entry_ref):
                continue

            out = simulate_trade_unified(df, idx, entry_ref, sl,
                                         risk_mult=cfg['risk_mult'],
                                         tp_price=tp,
                                         max_hold=cfg['max_hold'],
                                         lifecycle_filters=cfg.get('lifecycle_filters', False))

            date_val = str(row['date']) if 'date' in row else str(idx)
            factor_hits = {f['name']: bool(f.get('hit')) for f in rdict.get('factors', [])}

            records.append({
                'code': code,
                'date': date_val,
                'score': int(rdict['score']),
                'letter': rdict['letter'],
                'toxic': bool(rdict.get('toxic', False)),
                'factor_hits': factor_hits,
                'status': out['status'],
                'net_R': out['net_R'],
                'gross_R': out['gross_R'],
                'net_win': out['net_win'],
                'gross_win': out['gross_win'],
                'net_pct': out['net_pct'],
                'bars_held': out['bars_held'],
            })
        return records
    except Exception:
        return []


# ============================================================
# 聚合与统计
# ============================================================
def _band_table(records, key_field, key_values, win_field='net_win'):
    """对 records 按 key_field 分组算净胜率 + Wilson CI。
    win_field: 'net_win'(净,±1) 或 'gross_win'(bool)。"""
    rows = [r for r in records if r['status'] in ('WIN', 'LOSS')]
    out = {}
    for kv in key_values:
        sub = [r for r in rows if r[key_field] == kv]
        n = len(sub)
        if win_field == 'gross_win':
            k = sum(1 for r in sub if r['gross_win'])
        else:
            k = sum(1 for r in sub if r['net_win'] > 0)
        wr = (k / n * 100) if n else None
        lo, hi = wilson_ci(k, n)
        ev = round(float(np.mean([r['net_R'] for r in sub])), 3) if n else None
        out[kv] = {'win_rate': round(wr, 2) if wr is not None else None,
                   'n': n, 'wins': k, 'ci_lo': lo, 'ci_hi': hi, 'net_ev_R': ev}
    return out


def _calibrated_score(rec, weights):
    """按训练集推得的因子权重重算校准分 = sum(w_f * hit)。"""
    s = 0.0
    for nm, hit in rec['factor_hits'].items():
        w = weights.get(nm, 0.0)
        s += w if hit else 0.0
    return round(s, 3)


def _score_band(score, cuts):
    """cuts: 降序阈值列表 [(A+,t1),(A,t2),(B,t3),(C,t4)] -> 字母。"""
    for letter, t in cuts:
        if score >= t:
            return letter
    return 'D'


# ============================================================
# 后处理: 训练/测试 + CI + 年度
# ============================================================
def _compute_train_weights(train_recs):
    """从训练集信号推因子权重: w = sign(命中净胜率 - 未中净胜率) × 幅度缩放。"""
    fmap = {}
    for r in train_recs:
        for nm, hit in r['factor_hits'].items():
            d = fmap.setdefault(nm, {'h': [], 'm': []})
            (d['h'] if hit else d['m']).append(r['net_win'] > 0)
    train_weights = {}
    factor_stats = {}
    for nm, d in fmap.items():
        hw = (sum(d['h']) / len(d['h'])) if d['h'] else None
        mw = (sum(d['m']) / len(d['m'])) if d['m'] else None
        factor_stats[nm] = {'train_hit_wr': round(hw * 100, 2) if hw is not None else None,
                            'train_miss_wr': round(mw * 100, 2) if mw is not None else None,
                            'n_hit': len(d['h']), 'n_miss': len(d['m'])}
        if hw is not None and mw is not None:
            delta = (hw - mw) * 100  # 百分点差
            w = (1.0 if delta > 0 else (-1.0 if delta < 0 else 0.0)) * max(0.5, min(3.0, abs(delta) / 10.0))
            train_weights[nm] = round(w, 2)
        else:
            train_weights[nm] = 0.0
    return train_weights, factor_stats


def _compute_calib_cuts(train_recs, weights):
    """按训练集权重总分排序取分位数切点 (A+前10%, A前30%, B前55%, C前80%)。"""
    train_scores = sorted(_calibrated_score(r, weights) for r in train_recs)
    def _quant(q):
        if not train_scores:
            return 0.0
        i = int(len(train_scores) * q)
        return train_scores[min(i, len(train_scores) - 1)]
    return [('A+', _quant(0.90)), ('A', _quant(0.70)), ('B', _quant(0.45)), ('C', _quant(0.20))]


def analyze_annual_cv(records_all, key, risk_mult):
    """Walk-forward 年度交叉验证: 每个年份作为测试年, 用该年之前所有年份推因子权重+切点, 测该年。
    避免固定 60/40 把某一市况锁进测试集; 直接给出 A+ 档跨年稳定性。
    """
    resolved = [r for r in records_all if r['status'] in ('WIN', 'LOSS')]
    years = sorted(set(r['date'][:4] for r in resolved))
    folds = []
    tot_ak = 0
    tot_an = 0
    all_ev = []
    for y in years:
        train = [r for r in resolved if r['date'][:4] < y]
        test = [r for r in resolved if r['date'][:4] == y]
        if len(train) < 30 or not test:
            continue
        weights, _ = _compute_train_weights(train)
        cuts = _compute_calib_cuts(train, weights)
        for r in test:
            r['cv_letter'] = _score_band(_calibrated_score(r, weights), cuts)
        aplus = [r for r in test if r['cv_letter'] == 'A+']
        k = sum(1 for r in aplus if r['net_win'] > 0)
        n = len(aplus)
        lo, hi = wilson_ci(k, n)
        tot_ak += k
        tot_an += n
        all_ev.extend(r['net_R'] for r in aplus)
        folds.append({
            'year': y, 'regime': YEAR_REGIME.get(y, 'range'),
            'n_test': len(test), 'n_aplus': n,
            'aplus_wr': round(k / n * 100, 2) if n else None,
            'aplus_ci_lo': lo, 'aplus_ci_hi': hi,
            'aplus_ev': round(float(np.mean([r['net_R'] for r in aplus])), 3) if n else None,
        })
    if not folds:
        return None
    lo, hi = wilson_ci(tot_ak, tot_an)
    return {
        'n_folds': len(folds),
        'aplus_overall_wr': round(tot_ak / tot_an * 100, 2) if tot_an else None,
        'aplus_overall_n': tot_an,
        'aplus_overall_ci_lo': lo, 'aplus_overall_ci_hi': hi,
        'aplus_overall_ev': round(float(np.mean(all_ev)), 3) if all_ev else None,
        'folds': folds,
    }
def analyze(records_all, key, risk_mult):
    resolved = [r for r in records_all if r['status'] in ('WIN', 'LOSS')]
    # 日期切分
    dates = sorted(r['date'] for r in resolved)
    cut_idx = int(len(dates) * TRAIN_RATIO)
    cut_date = dates[cut_idx] if cut_idx < len(dates) else dates[-1]
    train = [r for r in resolved if r['date'] <= cut_date]
    test = [r for r in resolved if r['date'] > cut_date]

    # --- 训练集推因子权重 + 校准切点 (复用 helper, 与年度CV一致) ---
    train_weights, factor_stats = _compute_train_weights(train)
    calib_cuts = _compute_calib_cuts(train, train_weights)

    # 给 test 记录打校准字母
    for r in test:
        r['calib_letter'] = _score_band(_calibrated_score(r, train_weights), calib_cuts)

    # --- 原始 compute_rating 字母: 全样本 + 测试集(净) ---
    full_letter = _band_table(resolved, 'letter', ['A+', 'A', 'B', 'C', 'D'], 'net_win')
    test_letter = _band_table(test, 'letter', ['A+', 'A', 'B', 'C', 'D'], 'net_win')
    test_letter_gross = _band_table(test, 'letter', ['A+', 'A', 'B', 'C', 'D'], 'gross_win')
    test_calib = _band_table(test, 'calib_letter', ['A+', 'A', 'B', 'C', 'D'], 'net_win')

    # --- 年度分层 (全样本净胜率) ---
    year_map = {}
    for r in resolved:
        y = r['date'][:4]
        year_map.setdefault(y, []).append(r)
    year_strat = {}
    for y, rs in sorted(year_map.items()):
        k = sum(1 for r in rs if r['net_win'] > 0)
        n = len(rs)
        lo, hi = wilson_ci(k, n)
        year_strat[y] = {'win_rate': round(k / n * 100, 2), 'n': n, 'ci_lo': lo, 'ci_hi': hi,
                         'net_ev_R': round(float(np.mean([r['net_R'] for r in rs])), 3)}

    # 整体现金流
    overall_wr = (sum(1 for r in resolved if r['net_win'] > 0) / len(resolved) * 100) if resolved else 0
    overall_ev = round(float(np.mean([r['net_R'] for r in resolved])), 3) if resolved else None

    # --- 生命周期状态分布 (撤单类 INV/VOIDED/TIMEOUT 不计入分母, 但需展示占比) ---
    lc_counts = {}
    for r in records_all:
        lc_counts[r['status']] = lc_counts.get(r['status'], 0) + 1
    n_all = len(records_all)
    lifecycle_dist = {s: {'n': c, 'pct': round(c / n_all * 100, 2)} for s, c in lc_counts.items()}

    # --- regime 分层 (牛/熊/震荡, 基于 YEAR_REGIME 近似标签) ---
    regime_map = {}
    for r in resolved:
        ry = YEAR_REGIME.get(r['date'][:4], 'range')
        regime_map.setdefault(ry, []).append(r)
    regime_strat_net = {}
    for ry, rs in regime_map.items():
        k = sum(1 for r in rs if r['net_win'] > 0)
        n = len(rs)
        lo, hi = wilson_ci(k, n)
        regime_strat_net[ry] = {'win_rate': round(k / n * 100, 2) if n else None, 'n': n,
                                'ci_lo': lo, 'ci_hi': hi,
                                'net_ev_R': round(float(np.mean([r['net_R'] for r in rs])), 3) if n else None}

    return {
        'n_signals': len(records_all),
        'lifecycle_dist': lifecycle_dist,
        'regime_strat_net': regime_strat_net,
        'n_traded': len(resolved),
        'train_date_cut': cut_date,
        'n_train': len(train), 'n_test': len(test),
        'overall_net_win_rate': round(overall_wr, 2),
        'overall_net_ev_R': overall_ev,
        'risk_mult': risk_mult,
        'full_sample_letter_net': full_letter,
        'test_letter_net': test_letter,
        'test_letter_gross': test_letter_gross,
        'test_calibrated_net': test_calib,
        'calib_cuts': calib_cuts,
        'train_factor_weights': train_weights,
        'factor_stats_train': factor_stats,
        'year_strat_net': year_strat,
    }


# ============================================================
# 主流程
# ============================================================
def run_config(cfg, limit):
    from core.data_provider import get_stock_list
    codes = get_stock_list()
    if not codes:
        return []
    if limit and limit > 0:
        codes = codes[:limit]
    tasks = [(code, cfg, cfg['limit']) for code in codes]
    all_records = []
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as ex:
        futs = {ex.submit(worker_stock, t): t[0] for t in tasks}
        done = 0
        for fut in as_completed(futs):
            done += 1
            recs = fut.result()
            if recs:
                all_records.extend(recs)
            if done % 200 == 0:
                print(f"  [{cfg['key']}] 进度 {done}/{len(codes)} | 信号 {len(all_records)}", end='\r')
    print(f"\n  [{cfg['key']}] 完成: {len(codes)} 股, {len(all_records)} 信号")
    return all_records


def _render_report(result, report_path):
    L = []
    L.append("🦅 Brooks-AI 评级校准回测报告 v2.1 (成本敏感 / 训练-测试切分 / 显著性 / 多重比较校正 / regime·年度CV)")
    L.append(f"生成时间: {result['generated']}")
    if result.get('elapsed_sec') is not None:
        L.append(f"运行耗时: {result['elapsed_sec']:.1f}s")
    L.append(f"方法: 统一 Buy-Stop 入场 + 初始止损 + {result.get('risk_mult','2.0')}R 目标 + A股成本(印花税0.05%卖/佣金0.03%双边, 对齐已验证基线)")
    L.append(f"训练/测试切分: 前60%时间=TRAIN(推权重), 后40%=TEST(验档位); 切分日见各策略小节")
    nt = result.get('n_tests_approx')
    if nt:
        bonf = 0.05 / nt
        L.append(f"⚠ 多重比较: 本报告约 {nt} 个显著性检验 (各策略字母档×3视图 + 年度 + regime + CV)。"
                 f"family-wise α=0.05 下建议 Holm-Bonferroni 校正, 单个检验名义显著阈值≈{bonf:.4f}。")
    L.append("=" * 72)
    for key, agg in result['strategies'].items():
        L.append(f"\n### {key}")
        L.append(f"  训练/测试切分日: {agg.get('train_date_cut','-')}")
        L.append(f"  信号={agg['n_signals']} | 已结案={agg['n_traded']} | "
                 f"整体净胜率={agg['overall_net_win_rate']}% | 整体净EV={agg['overall_net_ev_R']}R")
        # 生命周期撤单占比 (INV/VOIDED/TIMEOUT 不计入分母, 但需展示)
        lc = agg.get('lifecycle_dist', {})
        lc_parts = []
        for s in ('INVALIDATED', 'VOIDED', 'TIMEOUT', 'HOLDING'):
            if s in lc:
                lc_parts.append(f"{s} {lc[s]['pct']}%({lc[s]['n']})")
        if lc_parts:
            L.append(f"  生命周期撤单占比: {' | '.join(lc_parts)}")
        # regime 分层
        L.append(f"  [regime 分层 净胜率 + CI + EV]")
        for ry in ('bull', 'bear', 'range'):
            if ry in agg.get('regime_strat_net', {}):
                rs = agg['regime_strat_net'][ry]
                L.append(f"    {REGIME_CN[ry]}: 净胜率 {rs['win_rate']}% (n={rs['n']}, CI {rs['ci_lo']}~{rs['ci_hi']}%, EV {rs['net_ev_R']}R)")
        L.append(f"  [测试集 样本外 · compute_rating 字母 · 净胜率 + Wilson95%CI]")
        for lv in ['A+', 'A', 'B', 'C', 'D']:
            b = agg['test_letter_net'].get(lv)
            if b and b['n']:
                L.append(f"    {lv}: 净胜率 {b['win_rate']}% (n={b['n']}, CI {b['ci_lo']}~{b['ci_hi']}%, EV {b['net_ev_R']}R)")
            else:
                L.append(f"    {lv}: 无样本")
        L.append(f"  [测试集 样本外 · 数据驱动校准字母 · 净胜率 + CI]  (切点={agg['calib_cuts']})")
        for lv in ['A+', 'A', 'B', 'C', 'D']:
            b = agg['test_calibrated_net'].get(lv)
            if b and b['n']:
                L.append(f"    {lv}: 净胜率 {b['win_rate']}% (n={b['n']}, CI {b['ci_lo']}~{b['ci_hi']}%, EV {b['net_ev_R']}R)")
            else:
                L.append(f"    {lv}: 无样本")
        L.append(f"  [全样本 内样本 · 仅参考]  compute_rating 字母净胜率:")
        for lv in ['A+', 'A', 'B', 'C', 'D']:
            b = agg['full_sample_letter_net'].get(lv)
            if b and b['n']:
                L.append(f"    {lv}: 净胜率 {b['win_rate']}% (n={b['n']}, EV {b['net_ev_R']}R)")
        L.append(f"  [年度分层 净胜率 + CI]")
        for y, ys in agg['year_strat_net'].items():
            L.append(f"    {y}: 净胜率 {ys['win_rate']}% (n={ys['n']}, CI {ys['ci_lo']}~{ys['ci_hi']}%, EV {ys['net_ev_R']}R)")
        L.append(f"  [训练集因子权重(数据驱动, 用于校准)]")
        for nm, w in sorted(agg['train_factor_weights'].items(), key=lambda kv: -abs(kv[1])):
            fs = agg['factor_stats_train'].get(nm, {})
            L.append(f"    {nm}: w={w} (命中{fs.get('train_hit_wr')}% / 未中{fs.get('train_miss_wr')}%, n_h={fs.get('n_hit')})")
        cv = agg.get('annual_cv')
        if cv:
            L.append(f"  [年度 Walk-Forward CV · A+ 档跨年稳定性]")
            L.append(f"    跨年汇总: A+净胜率 {cv['aplus_overall_wr']}% (n={cv['aplus_overall_n']}, "
                     f"CI {cv['aplus_overall_ci_lo']}~{cv['aplus_overall_ci_hi']}%, EV {cv['aplus_overall_ev']}R) | 折数={cv['n_folds']}")
            for f in cv['folds']:
                L.append(f"    {f['year']}({REGIME_CN.get(f['regime'],'')}): A+ n={f['n_aplus']} 净胜率 {f['aplus_wr']}% "
                         f"(CI {f['aplus_ci_lo']}~{f['aplus_ci_hi']}%, EV {f['aplus_ev']}R)")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description='评级校准回测 v2 (专业版)')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--strategies', type=str, default='')
    ap.add_argument('--weekly', dest='weekly', action='store_true')
    ap.add_argument('--no-weekly', dest='weekly', action='store_false')
    ap.set_defaults(weekly=True)
    ap.add_argument('--risk-mult', type=float, default=2.0)
    ap.add_argument('--max-hold', type=int, default=0, help='0=按策略默认')
    ap.add_argument('--start-date', type=str, default='', help='信号/回测区间起点 YYYY-MM-DD (指标仍用全历史)')
    ap.add_argument('--analyze-only', action='store_true', help='仅对已有 jsonl 重分析')
    ap.add_argument('--annual-cv', dest='annual_cv', action='store_true',
                    help='启用年度 Walk-Forward 交叉验证 (替代固定60/40, 检验评级跨年稳定性)')
    ap.add_argument('--no-annual-cv', dest='annual_cv', action='store_false')
    ap.set_defaults(annual_cv=False)
    ap.add_argument('--out', type=str, default=os.path.join(ROOT, 'config', 'rating_factors.json'))
    ap.add_argument('--report', type=str, default=os.path.join(ROOT, 'calibration_report_v2.txt'))
    ap.add_argument('--signals', type=str, default=os.path.join(ROOT, 'calibration_signals.jsonl'))
    args = ap.parse_args()
    import time
    _t0 = time.perf_counter()

    filter_names = [s.strip() for s in args.strategies.split(',') if s.strip()] or None
    cfgs = build_configs(args.weekly, filter_names)
    for cfg in cfgs:
        cfg['start_date'] = args.start_date
    print(f"🧪 评级校准回测 v2 | 配置数={len(cfgs)} | MAX_WORKERS={settings.MAX_WORKERS} | "
          f"R:R={args.risk_mult} | 成本=印花税0.05%卖/佣0.03%双边 | start-date={args.start_date or '全历史'}")

    result = {
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'methodology': {
            'model': 'unified Buy-Stop entry + initial stop + risk_mult R target',
            'risk_mult': args.risk_mult,
            'costs_bps': {'stamp_sell': 5.0, 'commission_both': 3.0, 'slippage_both': 0.0},
            'train_ratio': TRAIN_RATIO,
            'lookahead': 'rating computed on df.iloc[:idx+1]; exits use post-signal data only',
        },
        'strategies': {},
    }

    all_records_by_key = {}
    if not args.analyze_only:
        with open(args.signals, 'w', encoding='utf-8') as sf:
            for cfg in cfgs:
                print(f"\n▶ 运行 {cfg['key']} ...")
                if args.max_hold:
                    cfg['max_hold'] = args.max_hold
                cfg['risk_mult'] = args.risk_mult
                recs = run_config(cfg, args.limit)
                for r in recs:
                    r['_key'] = cfg['key']
                    sf.write(json.dumps(r, ensure_ascii=False) + '\n')
                all_records_by_key[cfg['key']] = recs
    else:
        by_key = {}
        with open(args.signals, 'r', encoding='utf-8') as sf:
            for line in sf:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                by_key.setdefault(r.get('_key'), []).append(r)
        for cfg in cfgs:
            all_records_by_key[cfg['key']] = by_key.get(cfg['key'], [])

    for cfg in cfgs:
        recs = all_records_by_key.get(cfg['key'], [])
        if not recs:
            continue
        agg = analyze(recs, cfg['key'], cfg['risk_mult'])
        if args.annual_cv:
            agg['annual_cv'] = analyze_annual_cv(recs, cfg['key'], cfg['risk_mult'])
        result['strategies'][cfg['key']] = agg
        print(f"  [{cfg['key']}] 整体净胜率={agg['overall_net_win_rate']}% | "
              f"测试集A+净胜率={agg['test_letter_net'].get('A+',{}).get('win_rate')}%")

    n_strat = len(result['strategies'])
    n_tests = n_strat * (3 * 5 + 15 + 3 + (15 if args.annual_cv else 0))
    result['n_tests_approx'] = n_tests
    result['elapsed_sec'] = time.perf_counter() - _t0
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    _render_report(result, args.report)

    print("\n" + "=" * 72)
    print(f"✅ 完成。JSON: {args.out} | 报告: {args.report} | 信号集: {args.signals}")
    print("\n[测试集样本外 · A+ 净胜率(含CI) 速览]")
    for key, agg in result['strategies'].items():
        a = agg['test_letter_net'].get('A+', {})
        if a.get('n'):
            print(f"  {'🔴' if (a['win_rate'] or 0) < 55 else '🟢'} {key}: A+净胜率 {a['win_rate']}% "
                  f"(n={a['n']}, CI {a['ci_lo']}~{a['ci_hi']}%)")
        else:
            print(f"  ⬜ {key}: A+ 无样本")


if __name__ == '__main__':
    main()
