"""P1-1 回归: 周线 3K/GAP 并行路由 + StrategyRegistry fail-fast。

两项不变量:
1. run_weekly_scan 必须并行路由 3K 与缺口家族 —— 原 `if STRATEGY_3K in active`
   独占分支导致"选了 3K 就不扫 gap"(漏信号)。
2. StrategyRegistry._resolve_class 对未知名必须显式报错(fail-fast), 不再静默
   回退 MTR(藏错: CLI 拼错策略名会悄悄跑 MTR)。
"""
import unittest
from unittest.mock import patch, MagicMock

from core.strategy_registry import StrategyRegistry
from core.strategies.mtr_strategy import MTRStrategy
from core.strategies.three_k_strategy import ThreeKStrategy


class TestRegistryFailFast(unittest.TestCase):
    def test_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            StrategyRegistry._resolve_class('TOTALLY_BOGUS')

    def test_get_strategy_unknown_raises(self):
        with self.assertRaises(KeyError):
            StrategyRegistry.get_strategy('TOTALLY_BOGUS')

    def test_get_metadata_unknown_raises(self):
        with self.assertRaises(KeyError):
            StrategyRegistry.get_metadata('TOTALLY_BOGUS')

    def test_exact_and_alias_still_resolve(self):
        self.assertEqual(StrategyRegistry._resolve_class('MTR_MASTER'), MTRStrategy)
        self.assertEqual(StrategyRegistry._resolve_class('MTR_V35_STRUCTURAL'), MTRStrategy)
        self.assertEqual(StrategyRegistry._resolve_class('3K'), ThreeKStrategy)
        self.assertEqual(StrategyRegistry._resolve_class('STRATEGY_3K'), ThreeKStrategy)


class TestWeeklyRouting(unittest.TestCase):
    """run_weekly_scan 路由: 用 mock 隔离 DB 与扫描器, 仅验证"哪些路径被调用"。"""

    def _run(self, active, m_gap, m_3k, m_fgap, m_f3k):
        from core import scan_engine
        fake_cur = MagicMock()
        fake_cur.fetchone.return_value = (1,)  # weekly_bars 存在
        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value = fake_cur
        with patch('core.scan_engine.dp') as mdp, \
             patch('sqlite3.connect', return_value=fake_conn), \
             patch('core.scan_engine.scan_weekly_gap_signals', m_gap), \
             patch('core.scan_engine.scan_weekly_3k_signals', m_3k), \
             patch('core.scan_engine.format_push_weekly_gap', m_fgap), \
             patch('core.scan_engine.format_push_weekly_3k', m_f3k):
            mdp.get_stock_list.return_value = ['sh.600000']
            m_gap.return_value = {'signals_gap': []}
            m_3k.return_value = {}
            scan_engine.run_weekly_scan(active, weeks=4, all_codes=['sh.600000'])

    def test_both_3k_and_gap_run(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_3K', 'STRATEGY_STRUCTURAL_GAP'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertTrue(m_gap.called, "缺口扫描应被调用")
        self.assertTrue(m_3k.called, "3K 扫描应被调用")
        # 缺口扫描器只应收到缺口策略, 不应混入 3K
        gap_arg = m_gap.call_args.kwargs['strategies']
        self.assertIn('STRATEGY_STRUCTURAL_GAP', gap_arg)
        self.assertNotIn('STRATEGY_3K', gap_arg)

    def test_gap_only_runs_gap_not_3k(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_GAP_H2'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertTrue(m_gap.called)
        self.assertFalse(m_3k.called, "仅选 gap 时不应跑 3K")

    def test_3k_only_runs_3k_not_gap(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_3K'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertFalse(m_gap.called, "仅选 3K 时不应跑 gap")
        self.assertTrue(m_3k.called)

    def test_unknown_only_runs_nothing(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['BOGUS_STRATEGY'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertFalse(m_gap.called)
        self.assertFalse(m_3k.called)


if __name__ == '__main__':
    unittest.main()
