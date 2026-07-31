# -*- coding: utf-8 -*-
"""
P0-5 回归: 去字母化后, 任何用户可见出口都不得出现经回测证明为噪声的
A+/A/B/C/D 假字母评级。

覆盖:
  1. 日线直通路径的图表 reason 文案 (hunter._classify_signals -> reason_txt) 不含假字母
  2. archive_signal 终端归档日志只显命中因子证据, 不含假字母

不依赖真实扫描/Discord: monkeypatch prepare_daily_chart, 并用临时库隔离
WatchlistManager (沿用 P0-3 按路径分池隔离)。
"""
import os
import sys
import queue
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 假字母标记 (任何命中即视为回归)
LETTER_TOKENS = ['极品', '高预期', '常态', '低预期', '毒性',
                 '(A+)', '(A)', '(B)', '(C)', '(D)', '🌟', '👍', '⚠️', '💀']


@contextmanager
def patch_db_path():
    """把 settings.DB_PATH 指到临时库, 隔离 WatchlistManager/归档, 不碰生产库。"""
    import config.settings as s
    import core.database as db
    tmp = tempfile.mkdtemp()
    old = s.DB_PATH
    s.DB_PATH = os.path.join(tmp, 't.db')
    db._INIT_DONE_PATHS = set()
    db._db_pools = {}
    try:
        yield tmp
    finally:
        s.DB_PATH = old
        db._INIT_DONE_PATHS = set()
        db._db_pools = {}


def _mk_rating(letter='A+', factors=('缺口确认', '快回调')):
    return {
        'letter': letter,
        'score': 80,
        'factors': [{'name': f, 'hit': True} for f in factors],
        'toxic': letter == 'D',
    }


def _run_classify():
    """驱动 _classify_signals, 捕获直通路径的图表 reason 文案 (不得含假字母)。"""
    import pandas as pd
    import hunter

    captured_reasons = []

    orig_chart = hunter.prepare_daily_chart
    def fake_chart(res, passed=True, reason=""):
        captured_reasons.append(reason)
        return ({'code': res['code'], 'ai_parsed': {'verdict': 'PASS', 'reason': reason}}, None, reason)
    hunter.prepare_daily_chart = fake_chart

    try:
        all_hits = [{
            'code': 'sz.000001', 'name_cn': '测试股', 'type': 'MTR_MASTER',
            'df': pd.DataFrame({'date': [pd.Timestamp('2026-07-29')]}),
            'info': {
                'entry': 10.0, 'sl': 9.0, 'tp1': 11.0, 'rr': 1.0,
                'signal_bar_idx': 299, 'signal_date': '2026-07-29',
                'rating': _mk_rating('A+', ('缺口确认', '快回调')),
            },
        }]
        import threading
        hunter._classify_signals(
            all_hits, queue.Queue(), queue.Queue(), threading.Event(), [], use_ai=False
        )
    finally:
        hunter.prepare_daily_chart = orig_chart

    return captured_reasons


def _check_archive_log():
    """直接调用 archive_signal, 捕获其 logger.info, 断言只显因子证据、不含假字母。"""
    import logging
    import core.signal_tracker.archive as arc_mod
    from core.signal_tracker import init_signal_archive
    init_signal_archive()

    records = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    arc_mod.logger.addHandler(handler)
    try:
        arc_mod.archive_signal(
            code='sz.000001', strategy='MTR_MASTER', timeframe='daily',
            entry=10.0, sl=9.0, tp=11.0, ev_rating='',
            evidence='●缺口确认 ●快回调', signal_date='2026-07-29',
            signal_bar_idx=299, rr=1.0, name='测试股'
        )
    finally:
        arc_mod.logger.removeHandler(handler)

    msg = ' '.join(records)
    bad = [tok for tok in LETTER_TOKENS if tok in msg]
    return msg, bad


def main():
    with patch_db_path():
        reasons = _run_classify()
        archive_msg, bad_tokens = _check_archive_log()

    fails = []
    # 1) 直通路径 reason 文案不得含假字母标记
    for r in reasons:
        for tok in LETTER_TOKENS:
            if tok in (r or ''):
                fails.append(f"reason 含假字母标记 '{tok}': {r}")

    # 2) archive 终端日志不得含假字母, 且应显因子证据
    if bad_tokens:
        fails.append(f"archive 日志含假字母标记 {bad_tokens}: {archive_msg}")
    if '●缺口确认' not in archive_msg:
        fails.append(f"archive 日志未显示因子证据: {archive_msg}")

    if fails:
        print("❌ P0-5 回归失败:")
        for f in fails:
            print("   -", f)
        return 1

    print("✅ P0-5 回归通过:")
    print(f"   直通 reason 样例: {reasons[0] if reasons else ''}")
    print(f"   archive 日志: {archive_msg}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
