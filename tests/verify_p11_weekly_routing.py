"""P1-1 回归: 周线只路由缺口家族 + StrategyRegistry fail-fast。

不变量:
1. run_weekly_scan 只路由缺口家族三个策略 (GAP H1 / GAP PINBAR / GAP H2)。
   3K 不在周线范围内 (用户明确周线只考虑 gap), 即使误传 STRATEGY_3K 也要被忽略,
   不能跑去 3K 分支, 也不能因为含非 gap key 而漏掉 gap。
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

    def test_all_three_gap_route_to_gap_only(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2'],
                  m_gap, m_3k, m_fgap, m_f3k)
        self.assertTrue(m_gap.called, "缺口扫描应被调用")
        self.assertFalse(m_3k.called, "周线绝不跑 3K")
        gap_arg = m_gap.call_args.kwargs['strategies']
        self.assertEqual(set(gap_arg),
                         {'STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2'})

    def test_single_gap_runs_only_that_gap(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_GAP_H2'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertTrue(m_gap.called)
        self.assertFalse(m_3k.called, "周线不应跑 3K")
        self.assertEqual(m_gap.call_args.kwargs['strategies'], ['STRATEGY_GAP_H2'])

    def test_3k_is_ignored_in_weekly(self):
        """3K 不是周线策略, 误传也不应触发任何扫描 (不跑 gap 也不跑 3K)。"""
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['STRATEGY_3K'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertFalse(m_gap.called, "3K 不是缺口策略, 不应触发缺口扫描")
        self.assertFalse(m_3k.called, "周线不应跑 3K 分支")

    def test_unknown_only_runs_nothing(self):
        m_gap = MagicMock(); m_3k = MagicMock()
        m_fgap = MagicMock(); m_f3k = MagicMock()
        self._run(['BOGUS_STRATEGY'], m_gap, m_3k, m_fgap, m_f3k)
        self.assertFalse(m_gap.called)
        self.assertFalse(m_3k.called)


if __name__ == '__main__':
    unittest.main()
