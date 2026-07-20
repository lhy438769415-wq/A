# tests/test_awil_strategy.py
"""
AWIL (Always In Long) 策略单元测试

覆盖场景:
  1. 完美信号触发
  2. 回调跌破 EMA20 → 无信号
  3. H2 K线不够强势 → 无信号
  4. 去重 (每个波段高点仅触发一次)
  5. SL / TP 计算正确性
  6. 数据不足安全返回
"""

import pytest
import pandas as pd
import numpy as np
from core.strategies.awil_strategy import AWILStrategy
from core.calculator import add_indicators


# =====================================================================
# 辅助函数: 构造测试数据
# =====================================================================

def _build_uptrend_df(n: int = 60) -> pd.DataFrame:
    """构造稳定上升趋势的基础 DataFrame。

    60 根 K 线, close 从 50 线性上升到 ~79.5。
    EMA20 约滞后 ~5 个点, 即 EMA20 ≈ 74-75 (远低于回调区间)。

    Args:
        n: K 线数量

    Returns:
        包含 date/open/high/low/close/volume 的 DataFrame
    """
    dates = [f'2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}' for i in range(n)]
    close = 50 + np.arange(n, dtype=float) * 0.5
    return pd.DataFrame({
        'date': dates,
        'open': close - 0.2,
        'high': close + 0.5,
        'low': close - 0.5,
        'close': close,
        'volume': np.full(n, 1_000_000),
    })


def _append_awil_pullback(df: pd.DataFrame) -> pd.DataFrame:
    """在上升趋势后追加 L1→H1→L2→H2 回调序列。

    设计要点:
      - 波段高点 bar (bar 60): high=81.0, 创40根新高
      - L1 (bar 62): LHLL, low=79.5 >> EMA20(≈75)
      - H1 (bar 63): HH, high=80.4 < 81.0 (不触发新 swing event)
      - L2 (bar 64): LHLL, low=79.2 >> EMA20
      - H2 (bar 65): HH + 强势阳线, close_loc = 0.98
    """
    pullback = pd.DataFrame([
        # Bar 60: swing high event (创 40 根新高)
        {'date': '2024-03-05', 'open': 80.0, 'high': 81.0,
         'low': 79.5, 'close': 80.5, 'volume': 1_200_000},
        # Bar 61: 正常过渡 (非 LHLL, 非 HH)
        {'date': '2024-03-06', 'open': 80.0, 'high': 80.5,
         'low': 79.8, 'close': 80.2, 'volume': 1_000_000},
        # Bar 62: L1 (LHLL: high<prev.high, low<prev.low)
        {'date': '2024-03-07', 'open': 80.0, 'high': 80.2,
         'low': 79.5, 'close': 79.8, 'volume': 1_100_000},
        # Bar 63: H1 (HH: high>prev.high)
        {'date': '2024-03-08', 'open': 79.9, 'high': 80.4,
         'low': 79.6, 'close': 80.1, 'volume': 1_000_000},
        # Bar 64: L2 (LHLL: high<prev.high, low<prev.low)
        {'date': '2024-03-11', 'open': 80.0, 'high': 80.1,
         'low': 79.2, 'close': 79.5, 'volume': 1_100_000},
        # Bar 65: H2 (HH + 强势阳线, close_loc = 0.98)
        # close_loc = (80.48-79.5)/(80.5-79.5) = 0.98
        {'date': '2024-03-12', 'open': 79.6, 'high': 80.5,
         'low': 79.5, 'close': 80.48, 'volume': 1_300_000},
    ])
    return pd.concat([df, pullback], ignore_index=True)


def _prepare_signal_df() -> pd.DataFrame:
    """构造完整的 AWIL 信号测试 DataFrame (含技术指标)。"""
    df = _build_uptrend_df(60)
    df = _append_awil_pullback(df)
    df = add_indicators(df)
    return df


# =====================================================================
# 测试用例
# =====================================================================

class TestAWILSignal:
    """AWIL 信号检测测试。"""

    def test_perfect_awil_signal(self):
        """完美 L1→H1→L2→H2 序列应触发信号。"""
        df = _prepare_signal_df()
        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        assert 'signal_awil' in result.columns
        signals = result[result['signal_awil'] == True]
        assert len(signals) >= 1, "应至少触发一个 AWIL 信号"

    def test_no_signal_below_ema(self):
        """回调中有 K 线 Low 跌破 EMA20 → 无信号。"""
        df = _build_uptrend_df(60)
        # 构造一个 low 极低的 pullback bar, 击穿 EMA20 (约75)
        pullback = pd.DataFrame([
            {'date': '2024-03-05', 'open': 80.0, 'high': 81.0,
             'low': 79.5, 'close': 80.5, 'volume': 1_200_000},
            {'date': '2024-03-06', 'open': 80.0, 'high': 80.5,
             'low': 79.8, 'close': 80.2, 'volume': 1_000_000},
            # L1: low 击穿 EMA20 (设为 73.0, 远低于 EMA20 ≈ 75)
            {'date': '2024-03-07', 'open': 80.0, 'high': 80.2,
             'low': 73.0, 'close': 79.8, 'volume': 1_100_000},
            {'date': '2024-03-08', 'open': 79.9, 'high': 80.4,
             'low': 79.6, 'close': 80.1, 'volume': 1_000_000},
            {'date': '2024-03-11', 'open': 80.0, 'high': 80.1,
             'low': 79.2, 'close': 79.5, 'volume': 1_100_000},
            {'date': '2024-03-12', 'open': 79.6, 'high': 80.5,
             'low': 79.5, 'close': 80.48, 'volume': 1_300_000},
        ])
        df = pd.concat([df, pullback], ignore_index=True)
        df = add_indicators(df)

        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        signals = result[result['signal_awil'] == True]
        assert len(signals) == 0, "Low 跌破 EMA20 时不应有信号"

    def test_no_signal_weak_h2_bar(self):
        """H2 K 线 close_loc < 0.98 (不够强势) → 无信号。"""
        df = _build_uptrend_df(60)
        pullback = pd.DataFrame([
            {'date': '2024-03-05', 'open': 80.0, 'high': 81.0,
             'low': 79.5, 'close': 80.5, 'volume': 1_200_000},
            {'date': '2024-03-06', 'open': 80.0, 'high': 80.5,
             'low': 79.8, 'close': 80.2, 'volume': 1_000_000},
            {'date': '2024-03-07', 'open': 80.0, 'high': 80.2,
             'low': 79.5, 'close': 79.8, 'volume': 1_100_000},
            {'date': '2024-03-08', 'open': 79.9, 'high': 80.4,
             'low': 79.6, 'close': 80.1, 'volume': 1_000_000},
            {'date': '2024-03-11', 'open': 80.0, 'high': 80.1,
             'low': 79.2, 'close': 79.5, 'volume': 1_100_000},
            # H2 bar: close 在中间位置, close_loc ≈ 0.5 < 0.98
            {'date': '2024-03-12', 'open': 79.6, 'high': 80.5,
             'low': 79.5, 'close': 80.0, 'volume': 1_300_000},
        ])
        df = pd.concat([df, pullback], ignore_index=True)
        df = add_indicators(df)

        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        signals = result[result['signal_awil'] == True]
        assert len(signals) == 0, "弱势 H2 不应触发信号"

    def test_dedup_per_swing(self):
        """每个波段高点仅触发一次信号 (去重)。"""
        df = _prepare_signal_df()
        # 在 H2 后再追加一根 HH 强势阳线
        extra = pd.DataFrame([{
            'date': '2024-03-13', 'open': 80.5, 'high': 81.0,
            'low': 80.4, 'close': 80.99, 'volume': 1_300_000,
        }])
        df = pd.concat([df, extra], ignore_index=True)
        df = add_indicators(df)

        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        signals = result[result['signal_awil'] == True]
        assert len(signals) <= 1, "每个波段高点最多触发一次信号"

    def test_sl_tp_calculation(self):
        """验证 SL = 回调最低点, TP = Entry + 2R。"""
        df = _prepare_signal_df()
        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        signals = result[result['signal_awil'] == True]
        if len(signals) == 0:
            pytest.skip("无信号, 跳过 SL/TP 测试")

        row = signals.iloc[-1]
        entry = row['entry_awil']
        sl = row['sl_awil']
        tp = row['tp_awil']

        assert not np.isnan(entry), "Entry 不应为 NaN"
        assert not np.isnan(sl), "SL 不应为 NaN"
        assert not np.isnan(tp), "TP 不应为 NaN"
        assert sl < entry, "SL 应低于 Entry"
        assert tp > entry, "TP 应高于 Entry"

        # 验证 TP = Entry + 2R
        r = entry - sl
        expected_tp = entry + 2 * r
        assert abs(tp - expected_tp) < 0.01, \
            f"TP={tp:.4f} 应等于 Entry+2R={expected_tp:.4f}"

    def test_insufficient_data(self):
        """数据不足时应安全返回, 不报错。"""
        df = _build_uptrend_df(20)  # 仅 20 根, 不足 SWING_LOOKBACK+10
        df = add_indicators(df)

        strat = AWILStrategy()
        result = strat.calculate_signals(df.copy())

        assert 'signal_awil' in result.columns
        assert not result['signal_awil'].any(), "数据不足时不应有信号"

    def test_metadata_completeness(self):
        """元数据声明应包含所有必要字段。"""
        meta = AWILStrategy.get_metadata()
        required_keys = [
            'display_name', 'sl_column', 'entry_column',
            'tp_columns', 'score_column', 'signal_column',
            'supported_timeframes',
        ]
        for key in required_keys:
            assert key in meta, f"元数据缺少字段: {key}"
        assert meta['signal_column'] == 'signal_awil'
        assert meta['display_name'] == 'AWIL趋势'


class TestAWILRegistration:
    """策略注册表集成测试。"""

    def test_registry_contains_awil(self):
        """AWIL 应已注册在策略注册表中。"""
        from core.strategy_registry import StrategyRegistry
        strategies = StrategyRegistry.list_strategies()
        assert "STRATEGY_AWIL" in strategies

    def test_registry_resolve_alias(self):
        """别名 'AWIL' 应解析到 AWILStrategy。"""
        from core.strategy_registry import StrategyRegistry
        strat = StrategyRegistry.get_strategy("AWIL")
        assert isinstance(strat, AWILStrategy)

    def test_registry_metadata(self):
        """通过注册表获取的元数据应与直接获取一致。"""
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata("STRATEGY_AWIL")
        assert meta['signal_column'] == 'signal_awil'
