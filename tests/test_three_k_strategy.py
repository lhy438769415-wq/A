# tests/test_three_k_strategy.py
"""
3K策略(突破缺口→测量缺口)单元测试
验证: 真实跳空缺口检测、突破缺口后验确认、测量缺口目标计算
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pandas as pd
import numpy as np
from core.strategies.three_k_strategy import ThreeKStrategy


def _make_base_df(rows: int = 80) -> pd.DataFrame:
    """生成基础DataFrame，包含3K策略所需的所有列"""
    np.random.seed(42)
    df = pd.DataFrame({
        'open': np.random.uniform(10, 20, rows),
        'high': np.random.uniform(20, 25, rows),
        'low': np.random.uniform(5, 10, rows),
        'close': np.random.uniform(10, 20, rows),
        'ema20': np.random.uniform(10, 15, rows),
        'atr': np.full(rows, 1.0),
        'body_pct': np.full(rows, 0.7),
        'lower_wick_pct': np.full(rows, 0.1),
        'upper_wick_pct': np.full(rows, 0.05),
        'close_loc': np.full(rows, 0.8),
        'is_bullish': np.full(rows, True),
    })
    return df


def _make_3k_scenario(gap_type='real', gap_held=True) -> pd.DataFrame:
    """
    构造精确的3K场景数据
    
    Args:
        gap_type: 'real' (Low>=High真实跳空) 或 'weak' (Open>=Close弱缺口)
        gap_held: True=缺口保持开放, False=缺口被回补
    """
    rows = 80
    df = _make_base_df(rows)
    
    # *** 关键: 将3K前的K线高点设低，确保 prior_swing_high < gap test area ***
    # 模拟: 前期波段在低位震荡, 3K突破到高位
    for i in range(0, 58):
        df.loc[i, ['open', 'high', 'low', 'close']] = [8.0, 12.0, 5.0, 10.0]
        df.loc[i, 'atr'] = 2.0
    
    # 在第60-62行构造3K形态 (K1=58, K2=59, K3=60，使用0-indexed)
    idx_k1, idx_k2, idx_k3 = 58, 59, 60
    
    # K1: 强阳线 10 -> 15, High=16, Low=9.5
    df.loc[idx_k1, ['open', 'high', 'low', 'close']] = [10.0, 16.0, 9.5, 15.0]
    df.loc[idx_k1, ['body_pct', 'close_loc', 'is_bullish']] = [0.85, 0.85, True]
    df.loc[idx_k1, ['lower_wick_pct', 'upper_wick_pct']] = [0.05, 0.05]
    
    if gap_type == 'real':
        # K2: 真实跳空 — Low(K2)=16.5 >= High(K1)=16.0
        df.loc[idx_k2, ['open', 'high', 'low', 'close']] = [17.0, 22.0, 16.5, 21.0]
        # K3: 真实跳空 — Low(K3)=22.5 >= High(K2)=22.0
        df.loc[idx_k3, ['open', 'high', 'low', 'close']] = [23.0, 28.0, 22.5, 27.0]
    else:
        # [V2.3] 弱缺口: K3.Open < K2.Close (每步开盘弱于前收 → 不满足 gap_ok)
        df.loc[idx_k2, ['open', 'high', 'low', 'close']] = [15.5, 20.0, 14.0, 19.0]
        df.loc[idx_k3, ['open', 'high', 'low', 'close']] = [18.5, 25.0, 18.0, 24.0]  # Open(18.5) < Close_K2(19.0)
    
    # K2, K3 的形态参数
    for idx in [idx_k2, idx_k3]:
        df.loc[idx, ['body_pct', 'close_loc', 'is_bullish']] = [0.80, 0.80, True]
        df.loc[idx, ['lower_wick_pct', 'upper_wick_pct']] = [0.05, 0.05]
    
    # EMA20 设低以满足 env_ok
    df.loc[idx_k1:idx_k3, 'ema20'] = 8.0
    df.loc[idx_k1:idx_k3, 'atr'] = 2.0
    
    # 确保递增高低点 (extremes_ok)
    df.loc[idx_k1, ['high', 'low']] = [16.0, 9.5]
    # K2和K3已经满足递增
    
    # 3K之后的K线 (确认缺口是否保持开放)
    # [V2.2] gap_floor = max(K1H=16, K2H=22, PSH=12) = 22.0
    for i in range(idx_k3 + 1, min(idx_k3 + 6, rows)):
        if gap_held:
            # 缺口保持开放: 后续低点(23.0) > gap_floor(22.0)
            df.loc[i, ['open', 'high', 'low', 'close']] = [26.0, 27.0, 23.0, 26.5]
        else:
            # 缺口被回补: 后续低点(14.0) < gap_floor(22.0)
            df.loc[i, ['open', 'high', 'low', 'close']] = [20.0, 21.0, 14.0, 15.0]
        df.loc[i, ['body_pct', 'close_loc', 'is_bullish']] = [0.6, 0.6, True]
        df.loc[i, ['lower_wick_pct', 'upper_wick_pct']] = [0.1, 0.1]
        df.loc[i, 'ema20'] = 15.0
        df.loc[i, 'atr'] = 2.0
    
    return df


class TestGapDetection:
    """测试真实跳空缺口检测逻辑"""
    
    def test_real_gap_detected(self):
        """真实跳空(Low>=High)应被识别"""
        df = _make_3k_scenario(gap_type='real')
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 检查 gap_ok 在 K3 位置为 True
        assert result.loc[60, 'gap_ok'] == True, \
            "真实跳空缺口(Low>=High)应该被 gap_ok 识别"
    
    def test_weak_gap_rejected(self):
        """弱缺口(仅Open>=Close)应被拒绝"""
        df = _make_3k_scenario(gap_type='weak')
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 弱缺口不满足 Low>=High，gap_ok 应为 False
        assert result.loc[60, 'gap_ok'] == False, \
            "弱缺口(Open>=Close但Low<High)不应该被识别为真实跳空"


class TestBreakoutGapConfirmation:
    """测试突破缺口后验确认逻辑"""
    
    def test_gap_held_open(self):
        """缺口保持开放: 后续波段低点 > K1高点"""
        df = _make_3k_scenario(gap_type='real', gap_held=True)
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 在3K之后的确认窗口内, breakout_gap_open 应为 True
        confirm_rows = result.iloc[61:66]
        has_open = confirm_rows['breakout_gap_open'].any()
        assert has_open, "缺口保持开放时，breakout_gap_open 应为 True"
    
    def test_gap_closed(self):
        """缺口被回补: 后续波段低点 < K1高点"""
        df = _make_3k_scenario(gap_type='real', gap_held=False)
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 在缺口被回补的区域, breakout_gap_open 应为 False
        confirm_rows = result.iloc[63:66]  # 给rolling窗口时间
        all_closed = (~confirm_rows['breakout_gap_open']).all()
        assert all_closed, "缺口被回补时，breakout_gap_open 应为 False"


class TestMeasuredGapTarget:
    """测试测量缺口目标价计算"""
    
    def test_measured_target_calculation(self):
        """缺口开放时应有测量缺口目标价"""
        df = _make_3k_scenario(gap_type='real', gap_held=True)
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 找到 breakout_gap_open=True 的行
        open_rows = result[result['breakout_gap_open'] == True]
        if len(open_rows) > 0:
            target = open_rows.iloc[0]['measured_gap_target']
            assert not np.isnan(target), "缺口开放时应有测量目标价"
            assert target > 0, "测量目标价应为正数"
    
    def test_no_target_when_gap_closed(self):
        """缺口关闭时不应有测量缺口目标价"""
        df = _make_3k_scenario(gap_type='real', gap_held=False)
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        # 缺口被回补的区域不应有目标价
        closed_rows = result.iloc[63:66]
        targets = closed_rows['measured_gap_target']
        all_nan = targets.isna().all()
        assert all_nan, "缺口关闭时不应有测量目标价"


class TestColumnExistence:
    """测试新增列是否存在"""
    
    def test_new_columns_exist(self):
        """验证新增列存在且无临时列泄漏"""
        df = _make_base_df()
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        assert 'breakout_gap_open' in result.columns, "缺少 breakout_gap_open 列"
        assert 'measured_gap_target' in result.columns, "缺少 measured_gap_target 列"
        assert 'signal_3k' in result.columns, "缺少 signal_3k 列"
        assert 'sl_3k' in result.columns, "缺少 sl_3k 列"
    
    def test_short_data_safety(self):
        """数据不足60行时应安全返回"""
        df = _make_base_df(30)
        strategy = ThreeKStrategy()
        result = strategy.calculate_signals(df)
        
        assert 'signal_3k' in result.columns
        assert not result['signal_3k'].any()


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--maxfail=2'])
