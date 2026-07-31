# -*- coding: utf-8 -*-
"""
P1-8 / P1-9 回归测试:
- P1-8: _drop_incomplete_current_week 必须以'本周一'为周边界(非自然周五),
        正确丢弃未完成周(周一~周五)的半成品周K, 保留完整周K(周六/周日或先前周)。
- P1-9: scan_single_code_weekly 的宽窗口参数已更名为 signal_lookback(默认60),
        不再静默吞掉 recent_weeks(与 3K 的紧窗口语义解耦)。
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from core.scan_engine import _drop_incomplete_current_week, scan_single_code_weekly


def _make_df(last_date: str, n: int = 10):
    """构造末尾日期为 last_date 的周线 DataFrame(其余为过去 n 周)。"""
    end = pd.Timestamp(last_date)
    dates = [end - pd.Timedelta(weeks=i) for i in range(n)][::-1]
    return pd.DataFrame({'close': [10.0] * n, 'trade_date': [d.strftime('%Y-%m-%d') for d in dates]})


class TestP18DropIncompleteWeek(unittest.TestCase):
    def test_friday_intraday_drops_incomplete_week(self):
        # 周五盘前扫描: 最新周K=本周五(未完成) -> 必须丢弃(旧实现 last>last_friday 为 False 会漏删)
        df = _make_df('2026-07-31')  # 2026-07-31 是周五
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-07-31'))
        self.assertEqual(len(out), len(df) - 1)

    def test_monday_keeps_prior_complete_week(self):
        # 周一扫描: 最新周K=上周五(完整先前周) -> 保留
        df = _make_df('2026-07-24')  # 上周五
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-07-27'))  # 周一
        self.assertEqual(len(out), len(df))

    def test_saturday_keeps_complete_week(self):
        # 周六扫描: 最新周K=本周五(完整) -> 保留
        df = _make_df('2026-07-31')
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-08-01'))  # 周六
        self.assertEqual(len(out), len(df))

    def test_wednesday_drops_partial_week(self):
        # 周三扫描: 最新周K=本周三(半成品) -> 丢弃
        df = _make_df('2026-07-29')
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-07-29'))
        self.assertEqual(len(out), len(df) - 1)

    def test_holiday_thursday_scanned_thursday_drops(self):
        # 假日缩短周(周五休市), 周四收盘扫描: 视为未完成 -> 安全丢弃(仅失1周即时性)
        df = _make_df('2026-07-30')
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-07-30'))
        self.assertEqual(len(out), len(df) - 1)

    def test_tuesday_keeps_prior_week(self):
        df = _make_df('2026-07-24')
        out = _drop_incomplete_current_week(df, today=pd.Timestamp('2026-07-28'))  # 周二
        self.assertEqual(len(out), len(df))

    def test_empty_df_passthrough(self):
        out = _drop_incomplete_current_week(pd.DataFrame())
        self.assertEqual(len(out), 0)


class TestP19GapLookbackParam(unittest.TestCase):
    def test_signal_lookback_default_is_wide(self):
        import inspect
        sig = inspect.signature(scan_single_code_weekly)
        self.assertIn('signal_lookback', sig.parameters)
        self.assertNotIn('recent_weeks', sig.parameters)
        self.assertEqual(sig.parameters['signal_lookback'].default, 60)


if __name__ == '__main__':
    unittest.main()
