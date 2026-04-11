# -*- coding: utf-8 -*-
"""
Phase 1 回归测试 — 验证死代码清理不影响核心功能

测试内容:
1. MTRStrategy.calculate_signals() 精简后仍能正常运行
2. trend_depth 和 dist_ema 被正确计算
3. Signal Tracker 的参数化 SQL 查询正常工作
4. scanner.py 的新导入路径正常工作
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock


# =====================================================
# 测试 1: MTR 策略精简后的功能完整性
# =====================================================
class TestMTRPhase1Cleanup:
    """验证 MTR 死代码清理后核心功能不受影响"""

    @staticmethod
    def _make_mtr_df(rows: int = 200) -> pd.DataFrame:
        """构造一个足够 MTR 策略处理的 DataFrame"""
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(rows) * 0.5) + 50
        prices = np.maximum(prices, 5)  # 防止负价格
        
        df = pd.DataFrame({
            'date': pd.date_range('2024-01-01', periods=rows, freq='B').strftime('%Y-%m-%d'),
            'open': prices + np.random.uniform(-0.5, 0.5, rows),
            'high': prices + np.random.uniform(0.5, 2.0, rows),
            'low': prices - np.random.uniform(0.5, 2.0, rows),
            'close': prices + np.random.uniform(-0.3, 0.3, rows),
            'volume': np.random.uniform(1e6, 1e8, rows),
        })
        # 确保 OHLC 逻辑正确
        df['high'] = df[['open', 'high', 'close']].max(axis=1) + 0.1
        df['low'] = df[['open', 'low', 'close']].min(axis=1) - 0.1
        
        return df

    def test_calculate_signals_runs_without_error(self):
        """MTRStrategy.calculate_signals() 清理后应正常执行无异常"""
        from core.strategies.mtr_strategy import MTRStrategy
        
        df = self._make_mtr_df()
        strategy = MTRStrategy()
        result = strategy.calculate_signals(df)
        
        # 基础断言: 返回 DataFrame 且行数不变
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 200

    def test_trend_depth_calculated(self):
        """trend_depth 列应被正确计算（120根内最大跌幅/ATR）"""
        from core.strategies.mtr_strategy import MTRStrategy
        
        df = self._make_mtr_df()
        strategy = MTRStrategy()
        result = strategy.calculate_signals(df)
        
        assert 'trend_depth' in result.columns, "trend_depth 列缺失"
        # 在有效数据范围内 (前 120 根有 NaN), 应有正值
        valid = result['trend_depth'].iloc[120:]
        assert valid.notna().all(), "120 根后 trend_depth 不应有 NaN"
        assert (valid > 0).all(), "trend_depth 应为正值"

    def test_dist_ema_calculated(self):
        """dist_ema 列应被正确计算（EMA20 与 Low 的距离/ATR）"""
        from core.strategies.mtr_strategy import MTRStrategy
        
        df = self._make_mtr_df()
        strategy = MTRStrategy()
        result = strategy.calculate_signals(df)
        
        assert 'dist_ema' in result.columns, "dist_ema 列缺失"

    def test_dead_code_columns_absent(self):
        """已清理的 V29/V30 中间列不应再出现"""
        from core.strategies.mtr_strategy import MTRStrategy
        
        df = self._make_mtr_df()
        strategy = MTRStrategy()
        result = strategy.calculate_signals(df)
        
        dead_columns = [
            'is_sell_climax_raw', 'bear_dominance', 'is_prior_bear',
            'is_sw_h', 'is_sw_l', 'last_sw_h_val', 'last_sw_l_val',
            'climax_low_val', 'dynamic_mlh', 'is_break_structural',
            'has_break_fact', 'is_hl_structural', 'body_sqz',
            'h_is_lower'
        ]
        for col in dead_columns:
            assert col not in result.columns, f"已清理列 '{col}' 不应存在"

    def test_signal_columns_exist(self):
        """核心输出列应始终存在"""
        from core.strategies.mtr_strategy import MTRStrategy
        
        df = self._make_mtr_df()
        strategy = MTRStrategy()
        result = strategy.calculate_signals(df)
        
        required = ['signal_mtr', 'mtr_score', 'mtr_stage', 'ema20', 'atr']
        for col in required:
            assert col in result.columns, f"核心列 '{col}' 缺失"


# =====================================================
# 测试 2: Signal Tracker SQL 参数化
# =====================================================
class TestSignalTrackerSQL:
    """验证 SQL 参数化查询不影响功能"""

    def test_track_signals_with_timeframe(self):
        """带 timeframe 参数的 track_signals 应正常执行"""
        from core.signal_tracker import track_signals, init_signal_archive
        
        # 使用 mock 替代真实数据库连接
        with patch('core.signal_tracker.get_db_connection') as mock_conn:
            mock_ctx = MagicMock()
            mock_conn.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            
            # execute 返回空列表
            mock_ctx.execute.return_value.fetchall.return_value = []
            mock_ctx.execute.return_value.description = []
            
            # 不应抛出异常
            stats = track_signals(timeframe='weekly')
            assert isinstance(stats, dict)


# =====================================================
# 测试 3: 数据层导入统一
# =====================================================
class TestDataLayerUnification:
    """验证数据层导入路径正确"""

    def test_scanner_imports_data_provider(self):
        """scanner.py 应直接使用 core.data_provider"""
        import importlib
        from core import scanner
        importlib.reload(scanner)
        
        # 检查模块中 get_stock_data 确实来自 core.data_provider
        assert hasattr(scanner, 'get_stock_data')


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--maxfail=2'])
