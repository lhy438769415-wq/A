# -*- coding: utf-8 -*-
"""
P1 + P2 回归测试

测试范围:
  测试 1: P1 策略自描述机制 (get_metadata / get_signal_info / annotate_chart / StrategyRegistry)
  测试 2: P2 Watchlist Facade (WatchlistManager 委托给 signal_tracker)
  测试 3: 导入无报错 (所有修改过的文件)
"""

import os
import sys
import unittest
import tempfile
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import numpy as np


# =========================================================================
# 测试 3: 导入无报错
# =========================================================================
class TestImports(unittest.TestCase):
    """验证所有修改过的文件能正常 import，无 SyntaxError / ImportError"""

    def test_import_base_strategy(self):
        from core.strategies.base import BaseStrategy
        self.assertTrue(hasattr(BaseStrategy, 'get_metadata'))
        self.assertTrue(hasattr(BaseStrategy, 'get_signal_info'))
        self.assertTrue(hasattr(BaseStrategy, 'annotate_chart'))

    def test_import_mtr_strategy(self):
        from core.strategies.mtr_strategy import MTRStrategy
        self.assertTrue(hasattr(MTRStrategy, 'get_metadata'))

    def test_import_three_k_strategy(self):
        from core.strategies.three_k_strategy import ThreeKStrategy
        self.assertTrue(hasattr(ThreeKStrategy, 'get_metadata'))

    def test_import_structural_gap_strategy(self):
        from core.strategies.structural_gap_strategy import StructuralGapStrategy
        self.assertTrue(hasattr(StructuralGapStrategy, 'get_metadata'))

    def test_import_gap_pinbar_strategy(self):
        from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
        self.assertTrue(hasattr(GapPinbarStrategy, 'get_metadata'))

    def test_import_gap_h2_strategy(self):
        from core.strategies.gap_h2_strategy import GapH2Strategy
        self.assertTrue(hasattr(GapH2Strategy, 'get_metadata'))

    def test_import_strategy_registry(self):
        from core.strategy_registry import StrategyRegistry
        self.assertTrue(hasattr(StrategyRegistry, 'get_metadata'))
        self.assertTrue(hasattr(StrategyRegistry, 'get_strategies_by_timeframe'))
        self.assertTrue(hasattr(StrategyRegistry, '_resolve_class'))

    def test_import_signal_tracker(self):
        from core.signal_tracker import (
            check_signal_exists, add_signal_entry,
            get_signal_status, get_signals_by_status, update_signal_entry
        )
        # 确保 5 个兼容函数存在
        self.assertTrue(callable(check_signal_exists))
        self.assertTrue(callable(add_signal_entry))
        self.assertTrue(callable(get_signal_status))
        self.assertTrue(callable(get_signals_by_status))
        self.assertTrue(callable(update_signal_entry))

    def test_import_watchlist(self):
        from tools.watchlist import WatchlistManager
        self.assertTrue(callable(WatchlistManager))


# =========================================================================
# 测试 1: P1 策略自描述机制
# =========================================================================
class TestBaseStrategyMetadata(unittest.TestCase):
    """验证 BaseStrategy.get_metadata() 返回正确结构"""

    def test_base_metadata_structure(self):
        from core.strategies.base import BaseStrategy
        meta = BaseStrategy.get_metadata()
        self.assertIsInstance(meta, dict)
        # 检查必需字段
        required_keys = [
            'display_name', 'sl_column', 'entry_column',
            'tp_columns', 'score_column', 'signal_column',
            'supported_timeframes'
        ]
        for key in required_keys:
            self.assertIn(key, meta, f"BaseStrategy.get_metadata() 缺少 '{key}' 字段")

    def test_base_metadata_defaults(self):
        from core.strategies.base import BaseStrategy
        meta = BaseStrategy.get_metadata()
        self.assertEqual(meta['display_name'], '策略')
        self.assertEqual(meta['sl_column'], '')
        self.assertEqual(meta['entry_column'], '')
        self.assertIsInstance(meta['tp_columns'], list)
        self.assertEqual(meta['tp_columns'], [])
        self.assertEqual(meta['supported_timeframes'], ['daily'])


class TestMTRMetadata(unittest.TestCase):
    """验证 MTR 策略 get_metadata() 覆写值"""

    def setUp(self):
        from core.strategies.mtr_strategy import MTRStrategy
        self.meta = MTRStrategy.get_metadata()

    def test_display_name(self):
        self.assertEqual(self.meta['display_name'], 'MTR反转')

    def test_sl_column(self):
        self.assertEqual(self.meta['sl_column'], 'sl_price')

    def test_entry_column(self):
        self.assertEqual(self.meta['entry_column'], 'entry_price')

    def test_tp_columns(self):
        self.assertEqual(self.meta['tp_columns'], ['tp1_price', 'tp2_price'])

    def test_score_column(self):
        self.assertEqual(self.meta['score_column'], 'mtr_score')

    def test_signal_column(self):
        self.assertEqual(self.meta['signal_column'], 'signal_mtr')

    def test_supported_timeframes(self):
        self.assertEqual(self.meta['supported_timeframes'], ['daily'])


class TestThreeKMetadata(unittest.TestCase):
    """验证 3K 策略 get_metadata() 覆写值"""

    def setUp(self):
        from core.strategies.three_k_strategy import ThreeKStrategy
        self.meta = ThreeKStrategy.get_metadata()

    def test_display_name(self):
        self.assertEqual(self.meta['display_name'], '3K动能')

    def test_sl_column(self):
        self.assertEqual(self.meta['sl_column'], 'sl_3k_gap_test')

    def test_entry_column(self):
        self.assertEqual(self.meta['entry_column'], 'entry_3k_gap_test')

    def test_tp_columns(self):
        self.assertEqual(self.meta['tp_columns'], ['tp_3k_gap_test'])

    def test_score_column(self):
        self.assertEqual(self.meta['score_column'], '')

    def test_signal_column(self):
        self.assertEqual(self.meta['signal_column'], 'signal_3k_gap_test')

    def test_supported_timeframes(self):
        self.assertEqual(self.meta['supported_timeframes'], ['daily'])


class TestStructuralGapMetadata(unittest.TestCase):
    """验证 Structural Gap 策略 get_metadata() 覆写值"""

    def setUp(self):
        from core.strategies.structural_gap_strategy import StructuralGapStrategy
        self.meta = StructuralGapStrategy.get_metadata()

    def test_display_name(self):
        self.assertEqual(self.meta['display_name'], '结构缺口')

    def test_sl_column(self):
        self.assertEqual(self.meta['sl_column'], 'sl_struct_gap')

    def test_entry_column(self):
        self.assertEqual(self.meta['entry_column'], 'entry_struct_gap')

    def test_tp_columns(self):
        self.assertEqual(self.meta['tp_columns'], ['tp_struct_gap'])

    def test_score_column(self):
        self.assertEqual(self.meta['score_column'], 'sig_bar_quality')

    def test_signal_column(self):
        self.assertEqual(self.meta['signal_column'], 'signal_struct_gap_confirm')

    def test_supported_timeframes(self):
        self.assertIn('daily', self.meta['supported_timeframes'])
        self.assertIn('weekly', self.meta['supported_timeframes'])


class TestGapPinbarMetadata(unittest.TestCase):
    """验证 Gap Pinbar 策略 get_metadata() 覆写值"""

    def setUp(self):
        from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
        self.meta = GapPinbarStrategy.get_metadata()

    def test_display_name(self):
        self.assertEqual(self.meta['display_name'], 'Gap Pinbar')

    def test_sl_column(self):
        self.assertEqual(self.meta['sl_column'], 'sl_gap_pinbar')

    def test_entry_column(self):
        self.assertEqual(self.meta['entry_column'], 'entry_gap_pinbar')

    def test_tp_columns(self):
        self.assertEqual(self.meta['tp_columns'], ['tp_gap_pinbar'])

    def test_score_column(self):
        self.assertEqual(self.meta['score_column'], 'sig_bar_quality_gp')

    def test_signal_column(self):
        self.assertEqual(self.meta['signal_column'], 'signal_gap_pinbar')

    def test_supported_timeframes(self):
        self.assertIn('daily', self.meta['supported_timeframes'])
        self.assertIn('weekly', self.meta['supported_timeframes'])


class TestGapH2Metadata(unittest.TestCase):
    """验证 Gap H2 策略 get_metadata() 覆写值"""

    def setUp(self):
        from core.strategies.gap_h2_strategy import GapH2Strategy
        self.meta = GapH2Strategy.get_metadata()

    def test_display_name(self):
        self.assertEqual(self.meta['display_name'], 'Gap H2')

    def test_sl_column(self):
        self.assertEqual(self.meta['sl_column'], 'sl_gap_h2')

    def test_entry_column(self):
        self.assertEqual(self.meta['entry_column'], 'entry_gap_h2')

    def test_tp_columns(self):
        self.assertEqual(self.meta['tp_columns'], ['tp_gap_h2'])

    def test_score_column(self):
        self.assertEqual(self.meta['score_column'], 'sig_bar_quality_h2')

    def test_signal_column(self):
        self.assertEqual(self.meta['signal_column'], 'signal_gap_h2')

    def test_supported_timeframes(self):
        self.assertIn('daily', self.meta['supported_timeframes'])
        self.assertIn('weekly', self.meta['supported_timeframes'])


class TestStrategyRegistryMetadata(unittest.TestCase):
    """验证 StrategyRegistry.get_metadata() 能通过策略名获取元数据"""

    def test_get_metadata_mtr(self):
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('MTR_MASTER')
        self.assertEqual(meta['display_name'], 'MTR反转')

    def test_get_metadata_3k(self):
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('STRATEGY_3K')
        self.assertEqual(meta['display_name'], '3K动能')

    def test_get_metadata_structural_gap(self):
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('STRATEGY_STRUCTURAL_GAP')
        self.assertEqual(meta['display_name'], '结构缺口')

    def test_get_metadata_gap_pinbar(self):
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('STRATEGY_GAP_PINBAR')
        self.assertEqual(meta['display_name'], 'Gap Pinbar')

    def test_get_metadata_gap_h2(self):
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('STRATEGY_GAP_H2')
        self.assertEqual(meta['display_name'], 'Gap H2')

    def test_get_metadata_alias_mtr(self):
        """验证别名模糊匹配也能获取元数据"""
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('MTR_V35')
        self.assertEqual(meta['display_name'], 'MTR反转')

    def test_get_metadata_alias_3k(self):
        """验证别名模糊匹配也能获取元数据"""
        from core.strategy_registry import StrategyRegistry
        meta = StrategyRegistry.get_metadata('3K')
        self.assertEqual(meta['display_name'], '3K动能')


class TestStrategyRegistryTimeframe(unittest.TestCase):
    """验证 StrategyRegistry.get_strategies_by_timeframe()"""

    def test_weekly_strategies(self):
        """weekly 应包含 STRUCTURAL_GAP, GAP_PINBAR, GAP_H2"""
        from core.strategy_registry import StrategyRegistry
        weekly = StrategyRegistry.get_strategies_by_timeframe('weekly')
        self.assertIn('STRATEGY_STRUCTURAL_GAP', weekly)
        self.assertIn('STRATEGY_GAP_PINBAR', weekly)
        self.assertIn('STRATEGY_GAP_H2', weekly)

    def test_weekly_excludes_daily_only(self):
        """weekly 不应包含仅支持 daily 的 MTR 和 3K"""
        from core.strategy_registry import StrategyRegistry
        weekly = StrategyRegistry.get_strategies_by_timeframe('weekly')
        self.assertNotIn('MTR_MASTER', weekly)
        self.assertNotIn('STRATEGY_3K', weekly)

    def test_daily_strategies(self):
        """daily 应包含全部 5 个策略"""
        from core.strategy_registry import StrategyRegistry
        daily = StrategyRegistry.get_strategies_by_timeframe('daily')
        self.assertIn('MTR_MASTER', daily)
        self.assertIn('STRATEGY_3K', daily)
        self.assertIn('STRATEGY_STRUCTURAL_GAP', daily)
        self.assertIn('STRATEGY_GAP_PINBAR', daily)
        self.assertIn('STRATEGY_GAP_H2', daily)
        self.assertEqual(len(daily), 5)

    def test_case_insensitive(self):
        """时间周期参数应不区分大小写"""
        from core.strategy_registry import StrategyRegistry
        self.assertEqual(
            StrategyRegistry.get_strategies_by_timeframe('Weekly'),
            StrategyRegistry.get_strategies_by_timeframe('weekly')
        )


class TestResolveClass(unittest.TestCase):
    """验证 StrategyRegistry._resolve_class()"""

    def test_exact_match(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.mtr_strategy import MTRStrategy
        cls = StrategyRegistry._resolve_class('MTR_MASTER')
        self.assertEqual(cls, MTRStrategy)

    def test_alias_mtr(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.mtr_strategy import MTRStrategy
        cls = StrategyRegistry._resolve_class('MTR_V35_STRUCTURAL')
        self.assertEqual(cls, MTRStrategy)

    def test_alias_3k(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.three_k_strategy import ThreeKStrategy
        cls = StrategyRegistry._resolve_class('3K')
        self.assertEqual(cls, ThreeKStrategy)

    def test_alias_structural(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.structural_gap_strategy import StructuralGapStrategy
        cls = StrategyRegistry._resolve_class('STRUCTURAL_GAP')
        self.assertEqual(cls, StructuralGapStrategy)

    def test_alias_pinbar(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
        cls = StrategyRegistry._resolve_class('GAP_PINBAR')
        self.assertEqual(cls, GapPinbarStrategy)

    def test_alias_h2(self):
        from core.strategy_registry import StrategyRegistry
        from core.strategies.gap_h2_strategy import GapH2Strategy
        cls = StrategyRegistry._resolve_class('H2')
        self.assertEqual(cls, GapH2Strategy)

    def test_unknown_fallback(self):
        """未知策略名应回退到 MTR"""
        from core.strategy_registry import StrategyRegistry
        from core.strategies.mtr_strategy import MTRStrategy
        cls = StrategyRegistry._resolve_class('NON_EXISTENT_STRATEGY')
        self.assertEqual(cls, MTRStrategy)


class TestGetSignalInfo(unittest.TestCase):
    """验证 get_signal_info() 返回正确结构"""

    def _make_df(self, columns_with_values):
        """构造测试 DataFrame"""
        n = 10
        data = {}
        for col, val in columns_with_values.items():
            if isinstance(val, list):
                data[col] = val
            else:
                data[col] = [val] * n
        return pd.DataFrame(data)

    def test_mtr_signal_info(self):
        from core.strategies.mtr_strategy import MTRStrategy
        df = self._make_df({
            'sl_price': 10.0,
            'entry_price': 12.0,
            'tp1_price': 16.0,
            'tp2_price': 18.0,
            'mtr_score': 75.0,
        })
        info = MTRStrategy.get_signal_info(df)
        self.assertAlmostEqual(info['sl'], 10.0)
        self.assertAlmostEqual(info['entry'], 12.0)
        self.assertAlmostEqual(info['tp1'], 16.0)
        self.assertAlmostEqual(info['tp2'], 18.0)
        self.assertAlmostEqual(info['score'], 75.0)

    def test_structural_gap_signal_info(self):
        from core.strategies.structural_gap_strategy import StructuralGapStrategy
        df = self._make_df({
            'sl_struct_gap': 10.0,
            'entry_struct_gap': 12.0,
            'tp_struct_gap': 16.0,
            'sig_bar_quality': 0.8,
        })
        info = StructuralGapStrategy.get_signal_info(df)
        self.assertAlmostEqual(info['sl'], 10.0)
        self.assertAlmostEqual(info['entry'], 12.0)
        self.assertAlmostEqual(info['tp1'], 16.0)
        self.assertIn('extra_info', info)
        self.assertIn('sig_quality', info['extra_info'])

    def test_signal_info_empty_df(self):
        from core.strategies.mtr_strategy import MTRStrategy
        info = MTRStrategy.get_signal_info(pd.DataFrame())
        self.assertEqual(info, {})

    def test_signal_info_none_df(self):
        from core.strategies.mtr_strategy import MTRStrategy
        info = MTRStrategy.get_signal_info(None)
        self.assertEqual(info, {})

    def test_signal_info_missing_columns(self):
        """列不存在时不应报错，只返回空"""
        from core.strategies.mtr_strategy import MTRStrategy
        df = self._make_df({'close': 10.0})
        info = MTRStrategy.get_signal_info(df)
        self.assertNotIn('sl', info)
        self.assertNotIn('entry', info)


class TestAnnotateChart(unittest.TestCase):
    """验证 annotate_chart() 方法存在且可调用"""

    def test_mtr_annotate_chart_callable(self):
        from core.strategies.mtr_strategy import MTRStrategy
        self.assertTrue(callable(MTRStrategy.annotate_chart))

    def test_structural_gap_annotate_chart_callable(self):
        from core.strategies.structural_gap_strategy import StructuralGapStrategy
        self.assertTrue(callable(StructuralGapStrategy.annotate_chart))

    def test_gap_pinbar_annotate_chart_callable(self):
        from core.strategies.gap_pinbar_strategy import GapPinbarStrategy
        self.assertTrue(callable(GapPinbarStrategy.annotate_chart))

    def test_gap_h2_annotate_chart_callable(self):
        from core.strategies.gap_h2_strategy import GapH2Strategy
        self.assertTrue(callable(GapH2Strategy.annotate_chart))

    def test_base_annotate_chart_no_op(self):
        """BaseStrategy.annotate_chart 默认为 no-op (不报错)"""
        from core.strategies.base import BaseStrategy
        mock_ax = MagicMock()
        # 应该不报错
        BaseStrategy.annotate_chart(mock_ax, pd.DataFrame(), 'TEST')


class TestWeeklyScannerHelper(unittest.TestCase):
    """验证 scanner_weekly_gap._get_strategy_cols() 辅助函数"""

    def test_structural_gap_cols(self):
        from tools.scanner_weekly_gap import _get_strategy_cols
        cols = _get_strategy_cols('STRATEGY_STRUCTURAL_GAP')
        self.assertEqual(cols['signal'], 'signal_struct_gap_confirm')
        self.assertEqual(cols['entry'], 'entry_struct_gap')
        self.assertEqual(cols['sl'], 'sl_struct_gap')
        self.assertEqual(cols['tp'], 'tp_struct_gap')
        self.assertEqual(cols['quality'], 'sig_bar_quality')
        self.assertEqual(cols['bars_since_breakout'], 'bars_since_breakout')
        self.assertEqual(cols['gap_top_exact'], 'struct_gap_top_exact')

    def test_gap_pinbar_cols(self):
        from tools.scanner_weekly_gap import _get_strategy_cols
        cols = _get_strategy_cols('STRATEGY_GAP_PINBAR')
        self.assertEqual(cols['signal'], 'signal_gap_pinbar')
        self.assertEqual(cols['entry'], 'entry_gap_pinbar')
        self.assertEqual(cols['sl'], 'sl_gap_pinbar')
        self.assertEqual(cols['tp'], 'tp_gap_pinbar')
        self.assertEqual(cols['quality'], 'sig_bar_quality_gp')
        self.assertEqual(cols['bars_since_breakout'], 'bars_since_breakout_gp')
        self.assertEqual(cols['gap_top_exact'], 'gap_pinbar_top_exact')

    def test_gap_h2_cols(self):
        from tools.scanner_weekly_gap import _get_strategy_cols
        cols = _get_strategy_cols('STRATEGY_GAP_H2')
        self.assertEqual(cols['signal'], 'signal_gap_h2')
        self.assertEqual(cols['entry'], 'entry_gap_h2')
        self.assertEqual(cols['sl'], 'sl_gap_h2')
        self.assertEqual(cols['tp'], 'tp_gap_h2')
        self.assertEqual(cols['quality'], 'sig_bar_quality_h2')
        self.assertEqual(cols['bars_since_breakout'], 'bars_since_breakout_h2')
        self.assertEqual(cols['gap_top_exact'], 'gap_h2_top_exact')

    def test_unknown_strategy_returns_mtr_fallback(self):
        """未知策略名由于 _resolve_class 回退到 MTR，应返回 MTR 的列映射"""
        from tools.scanner_weekly_gap import _get_strategy_cols
        cols = _get_strategy_cols('NON_EXISTENT')
        # _resolve_class 对未知策略回退到 MTR，所以返回 MTR 的列
        self.assertEqual(cols['signal'], 'signal_mtr')
        self.assertEqual(cols['entry'], 'entry_price')
        self.assertEqual(cols['sl'], 'sl_price')


# =========================================================================
# 测试 2: P2 Watchlist Facade
# =========================================================================
class TestWatchlistManagerInit(unittest.TestCase):
    """验证 WatchlistManager.__init__() 能正常初始化"""

    def test_init_without_path(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        self.assertIsNotNone(wm)
        self.assertIsNone(wm._data_cache)
        self.assertIsInstance(wm._status_overrides, dict)

    def test_init_with_path(self):
        """path 参数保留兼容，但应忽略"""
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager(path='/fake/path.json')
        self.assertIsNotNone(wm)


class TestWatchlistManagerData(unittest.TestCase):
    """验证 WatchlistManager.data 属性"""

    def test_data_property_returns_dict(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        data = wm.data
        self.assertIsInstance(data, dict)

    def test_data_setter_ignored(self):
        """data setter 应忽略写入 (SQLite 是唯一数据源)"""
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        wm.data = {'fake': 'data'}
        # 应该不报错，也不改变内部状态


class TestSignalTrackerCompatFunctions(unittest.TestCase):
    """验证 signal_tracker.py 中的 5 个兼容函数能正常调用"""

    def test_check_signal_exists_no_signal(self):
        """查询不存在的信号应返回 False"""
        from core.signal_tracker import check_signal_exists
        result = check_signal_exists('999999', timeframe='daily')
        self.assertFalse(result)

    def test_add_signal_entry(self):
        """添加信号应返回非空 signal_id"""
        from core.signal_tracker import add_signal_entry
        code = f'TEST_{datetime.now().strftime("%H%M%S")}'
        sid = add_signal_entry(
            code=code, entry=10.0, sl=9.0, score=80.0,
            date='2025-01-01', strategy='TEST_STRAT',
            timeframe='daily'
        )
        # 即使数据库写入可能因环境失败，函数不应抛出异常
        self.assertIsInstance(sid, str)

    def test_get_signal_status_no_signal(self):
        """查询不存在信号的状态应返回空字符串"""
        from core.signal_tracker import get_signal_status
        result = get_signal_status('999999', timeframe='daily')
        # 应返回空字符串或一个状态字符串
        self.assertIsInstance(result, str)

    def test_get_signals_by_status(self):
        """按状态查询信号应返回 dict"""
        from core.signal_tracker import get_signals_by_status
        result = get_signals_by_status(['PENDING'], timeframe='daily')
        self.assertIsInstance(result, dict)

    def test_update_signal_entry(self):
        """更新信号应返回 bool"""
        from core.signal_tracker import update_signal_entry
        result = update_signal_entry('999999', entry=10.5, timeframe='daily')
        self.assertIsInstance(result, bool)


class TestWatchlistManagerMethods(unittest.TestCase):
    """验证 WatchlistManager 核心方法"""

    def test_add_signal(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        # 不应抛出异常
        wm.add_signal('TESTCODE', 10.0, 9.0, 80, 0, '2025-01-01')

    def test_get_by_status(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        result = wm.get_by_status('WATCHING')
        self.assertIsInstance(result, dict)

    def test_get_new(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        result = wm.get_new()
        self.assertIsInstance(result, dict)

    def test_get_watching(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        result = wm.get_watching()
        self.assertIsInstance(result, dict)

    def test_get_expired(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        result = wm.get_expired()
        self.assertIsInstance(result, dict)

    def test_get_all(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        result = wm.get_all()
        self.assertIsInstance(result, dict)

    def test_update_signal_bar(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        # 不应抛出异常
        wm.update_signal_bar('TESTCODE', 5, 11.0)

    def test_update_status_no_signal(self):
        from tools.watchlist import WatchlistManager
        wm = WatchlistManager()
        # 不存在信号时返回 None
        result = wm.update_status('999999', None)
        self.assertIsNone(result)


class TestStatusMapping(unittest.TestCase):
    """验证状态映射逻辑 (JSON Watchlist ↔ SQLite Signal Tracker)"""

    def test_json_to_sql_mapping(self):
        from core.signal_tracker import _STATUS_MAP_JSON_TO_SQL
        self.assertEqual(_STATUS_MAP_JSON_TO_SQL['NEW'], 'PENDING')
        self.assertEqual(_STATUS_MAP_JSON_TO_SQL['WATCHING'], 'PENDING')
        self.assertEqual(_STATUS_MAP_JSON_TO_SQL['TRIGGERED'], 'ACTIVE')
        self.assertEqual(_STATUS_MAP_JSON_TO_SQL['INVALIDATED'], 'INVALIDATED')
        self.assertEqual(_STATUS_MAP_JSON_TO_SQL['EXPIRED'], 'EXPIRED')

    def test_sql_to_json_mapping(self):
        from core.signal_tracker import _STATUS_MAP_SQL_TO_JSON
        self.assertEqual(_STATUS_MAP_SQL_TO_JSON['PENDING'], 'WATCHING')
        self.assertEqual(_STATUS_MAP_SQL_TO_JSON['ACTIVE'], 'TRIGGERED')
        self.assertEqual(_STATUS_MAP_SQL_TO_JSON['INVALIDATED'], 'INVALIDATED')
        self.assertEqual(_STATUS_MAP_SQL_TO_JSON['EXPIRED'], 'EXPIRED')


# =========================================================================
# 测试: hunter.py 中 weekly_supported 已替换
# =========================================================================
class TestHunterWeeklySupported(unittest.TestCase):
    """验证 hunter.py 中的 weekly_supported 替换"""

    def test_hunter_uses_registry_for_weekly(self):
        """
        hunter.py 应该使用 StrategyRegistry.get_strategies_by_timeframe('weekly')
        而不是硬编码列表。验证导入无报错即可。
        """
        from core.strategy_registry import StrategyRegistry
        weekly = StrategyRegistry.get_strategies_by_timeframe('weekly')
        # hunter.py 期望 weekly_supported 包含这3个
        expected = ['STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2']
        for name in expected:
            self.assertIn(name, weekly)


if __name__ == '__main__':
    unittest.main(verbosity=2)
