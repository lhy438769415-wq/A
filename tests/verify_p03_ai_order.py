"""P1-3 回归: AI 审核顺序 — 被 AI 拒绝的信号不得归档进 signal_archive(避免永久 PENDING 污染追踪器)。

不变量:
- 归档必须发生在 AI 判定之后; _classify_signals 仅对 direct_picks+final_picks(通过)
  调用 _archive_passed_signals, rejected_list(被拒) 永不归档。
- 旧实现: archive_signal 在 _scan_market(AI 前) 对所有命中归档 -> 被拒信号永远 PENDING。
"""
import queue
from unittest.mock import patch, MagicMock

import pandas as pd

import hunter


def _make_df():
    """最小真实 DataFrame: 供 _classify_signals 的 watchlist 过滤逻辑
    (hunter.py:365 访问 res['df']['date'].iloc[-1]) 走通, 与 P1-3 归档顺序无关。"""
    return pd.DataFrame({
        'date': [pd.Timestamp('2026-07-30')],
        'open': [10.0], 'high': [11.0], 'low': [9.0], 'close': [10.0],
    })


def _make_res(code, stype):
    return {
        'code': code, 'type': stype, 'name_cn': code,
        'info': {'entry': 10.0, 'price': 10.0, 'sl': 9.0, 'tp1': 11.0,
                 'rr': 2.0, 'signal_date': '2026-07-30', 'signal_bar_idx': 100,
                 'rating': {}},
        'df': _make_df(),
    }


def test_archive_passed_signals_archives_passed_only_unit():
    """单元: _archive_passed_signals 对传入的通过信号调用 archive_signal(正确参数)。"""
    calls = []
    res = _make_res('sh.600001', 'STRATEGY_3K')

    def fake_archive(**kw):
        calls.append(kw)
        return kw.get('signal_id', 'id')
    with patch('core.signal_tracker.archive_signal', side_effect=fake_archive):
        hunter._archive_passed_signals([res])
    assert len(calls) == 1
    c = calls[0]
    assert c['code'] == 'sh.600001'
    assert c['strategy'] == 'STRATEGY_3K'
    assert c['timeframe'] == 'daily'


def test_rejected_signal_not_archived():
    """集成: 注入 PASS/FAIL, 断言被拒信号(sh.600002)不归档, 通过信号(sh.600001)归档。

    注: 必须用 ai_audit=True 的策略(仅 STRATEGY_AWIL 是日线 AI 审计策略),
    否则信号根本不进 AI 候选通道, 其 FAIL 结论被忽略、直接归 direct_picks。"""
    res_pass = _make_res('sh.600001', 'STRATEGY_AWIL')   # ai_audit=True -> 走 AI 候选
    res_fail = _make_res('sh.600002', 'STRATEGY_AWIL')

    # 预置结果队列: sh.600001 PASS, sh.600002 FAIL (模拟 AI 审计结论)
    result_q = queue.Queue()
    result_q.put(('PASS', (res_pass, None)))
    result_q.put(('FAIL', (res_fail, '结构松散')))

    archived = {}
    def fake_archive(**kw):
        archived[kw['code']] = kw
        return kw.get('signal_id', 'id')

    mock_wl = MagicMock()
    mock_wl.get_watching.return_value = {}
    mock_wl.data = {}

    with patch('tools.watchlist.WatchlistManager', return_value=mock_wl), \
         patch('core.signal_tracker.archive_signal', side_effect=fake_archive), \
         patch('hunter.prepare_daily_chart', side_effect=lambda res, **kw: (res, None, None)):
        hunter._classify_signals(
            all_hits=[res_pass, res_fail],
            analysis_queue=MagicMock(),   # put/join 均为 no-op
            result_queue=result_q,
            stop_event=MagicMock(),
            ai_threads=[],
            use_ai=True,
        )

    assert 'sh.600001' in archived, "通过信号应被归档"
    assert 'sh.600002' not in archived, "被 AI 拒绝的信号绝不得归档(否则永久 PENDING 污染追踪器)"


def test_no_ai_all_archived():
    """无 AI 模式: 所有候选转直通(direct_picks), 应全部归档。"""
    res = _make_res('sh.600003', 'STRATEGY_3K')
    archived = {}
    def fake_archive(**kw):
        archived[kw['code']] = kw
        return 'id'

    mock_wl = MagicMock()
    mock_wl.get_watching.return_value = {}
    mock_wl.data = {}

    with patch('tools.watchlist.WatchlistManager', return_value=mock_wl), \
         patch('core.signal_tracker.archive_signal', side_effect=fake_archive), \
         patch('hunter.prepare_daily_chart', side_effect=lambda res, **kw: (res, None, None)):
        hunter._classify_signals(
            all_hits=[res],
            analysis_queue=MagicMock(),
            result_queue=queue.Queue(),
            stop_event=MagicMock(),
            ai_threads=[],
            use_ai=False,
        )
    assert 'sh.600003' in archived, "无 AI 直通信号应归档"
