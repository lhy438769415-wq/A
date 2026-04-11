# -*- coding: utf-8 -*-
"""
calculator.py 核心指标引擎单元测试

覆盖:
1. add_indicators() 输出列完整性
2. EMA20/EMA60 计算正确性
3. ATR 计算正确性
4. PA 特征 (body_pct, close_loc, is_bullish)
5. trend_depth 计算正确性
6. 边界测试 (空 DataFrame、短数据)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import pandas as pd
from core.calculator import add_indicators


def _make_ohlcv(rows: int = 100, seed: int = 42) -> pd.DataFrame:
    """构造合理的 OHLCV 数据"""
    np.random.seed(seed)
    prices = np.cumsum(np.random.randn(rows) * 0.5) + 50
    prices = np.maximum(prices, 5)
    
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=rows, freq='B').strftime('%Y-%m-%d'),
        'open': prices + np.random.uniform(-0.3, 0.3, rows),
        'close': prices + np.random.uniform(-0.3, 0.3, rows),
        'volume': np.random.uniform(1e6, 1e8, rows),
    })
    # 确保 high/low 逻辑正确
    df['high'] = df[['open', 'close']].max(axis=1) + np.random.uniform(0.1, 1.0, rows)
    df['low'] = df[['open', 'close']].min(axis=1) - np.random.uniform(0.1, 1.0, rows)
    return df


class TestOutputColumns:
    """验证 add_indicators 输出列的完整性"""

    def test_all_indicator_columns_exist(self):
        """所有核心指标列应存在"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        required = [
            'ema20', 'ema60', 'ema60_slope', 'atr', 'adx_str',
            'body_pct', 'close_loc', 'is_bullish', 'upper_wick_pct', 'lower_wick_pct',
            'trend_depth',
        ]
        for col in required:
            assert col in result.columns, f"缺少核心列: {col}"

    def test_no_side_effects_on_input(self):
        """add_indicators 不应修改原始 DataFrame"""
        df = _make_ohlcv()
        original_cols = set(df.columns)
        _ = add_indicators(df)
        # 原始 df 不应被修改 (因为 add_indicators 做了 df.copy())
        assert set(df.columns) == original_cols


class TestEMA:
    """验证 EMA 计算正确性"""

    def test_ema20_basic(self):
        """EMA20 应与 pandas ewm 一致"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        expected = df['close'].ewm(span=20, adjust=False).mean()
        pd.testing.assert_series_equal(
            result['ema20'], expected, 
            check_names=False, atol=1e-10
        )

    def test_ema60_basic(self):
        """EMA60 应与 pandas ewm 一致"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        expected = df['close'].ewm(span=60, adjust=False).mean()
        pd.testing.assert_series_equal(
            result['ema60'], expected,
            check_names=False, atol=1e-10
        )


class TestATR:
    """验证 ATR 计算正确性"""

    def test_atr_positive(self):
        """ATR 应始终为正数"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        assert (result['atr'].dropna() >= 0).all(), "ATR 不应有负值"
        assert (result['atr'].iloc[14:].dropna() > 0).all(), "14 根后 ATR 应为正值"

    def test_atr_manual_verification(self):
        """ATR 前 14 根的均值应等于所有 TR 的简单均值 (min_periods=1)"""
        df = _make_ohlcv(20)
        result = add_indicators(df)
        
        # 手工计算 TR
        high = result['high']
        low = result['low']
        close_prev = result['close'].shift(1)
        tr1 = high - low
        tr2 = (high - close_prev).abs()
        tr3 = (low - close_prev).abs()
        import numpy as np
        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        
        # 第 14 根 (idx=13) 的 ATR 应等于前 14 根 TR 的均值
        expected_atr_13 = tr.iloc[:14].mean()
        assert abs(result.iloc[13]['atr'] - expected_atr_13) < 1e-6


class TestPAFeatures:
    """验证价格行为特征的正确性"""

    def test_body_pct_range(self):
        """body_pct 应在 [0, 1] 范围"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        valid = result['body_pct'].dropna()
        assert (valid >= 0).all(), "body_pct 不应为负"
        assert (valid <= 1.01).all(), "body_pct 不应超过 1"

    def test_close_loc_range(self):
        """close_loc 应在 [0, 1] 范围"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        valid = result['close_loc'].dropna()
        assert (valid >= -0.01).all(), "close_loc 不应显著为负"
        assert (valid <= 1.01).all(), "close_loc 不应超过 1"

    def test_is_bullish_logic(self):
        """is_bullish 应正确反映 close > open"""
        df = _make_ohlcv()
        result = add_indicators(df)
        
        manual_bullish = result['close'] > result['open']
        pd.testing.assert_series_equal(
            result['is_bullish'], manual_bullish,
            check_names=False
        )


class TestTrendDepth:
    """验证 trend_depth 计算正确性"""

    def test_trend_depth_positive(self):
        """trend_depth 应为正值"""
        df = _make_ohlcv(200)
        result = add_indicators(df)
        
        valid = result['trend_depth'].iloc[120:].dropna()
        assert (valid >= 0).all(), "trend_depth 不应为负"


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_dataframe(self):
        """空 DataFrame 应安全返回"""
        df = pd.DataFrame()
        result = add_indicators(df)
        assert result is not None
        assert result.empty

    def test_single_row(self):
        """单行 DataFrame 不应报错"""
        df = _make_ohlcv(1)
        result = add_indicators(df)
        assert len(result) == 1
        assert 'ema20' in result.columns

    def test_short_data(self):
        """5 行数据不应报错"""
        df = _make_ohlcv(5)
        result = add_indicators(df)
        assert len(result) == 5
        assert 'atr' in result.columns


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--maxfail=3'])
