"""P1-6 回归: 心跳必须反映真实运行状态, 杜绝"假成功"。

不变量(_write_heartbeat 依据 RUN_SUMMARY):
- error 非空 -> status='error'   (崩溃/异常, 不再靠 check_heartbeat 只猜"过期")
- job_ran=True -> status='ok'     (真实任务执行并跑完)
- 其余        -> status='idle'    (菜单空跑 / 数据缺失早退, 不算有效运行)
并附 mode / signals / discord_configured 真值字段。
"""
import json
from unittest.mock import patch, mock_open

import hunter


def _write_and_read(summary):
    """隔离磁盘: 用 mock_open 捕获 _write_heartbeat 写出的 json, 返回解析后的 dict。"""
    mo = mock_open()
    captured = {}

    def _collect(*a, **k):
        # json.dump 会多次调用 f.write, 拼接还原
        captured['raw'] = ''.join(
            c.args[0] for c in mo().write.call_args_list if c.args
        )

    with patch('hunter.open', mo), \
         patch('hunter.os.makedirs'), \
         patch.object(mo(), 'write', side_effect=_collect):
        hunter.RUN_SUMMARY.clear()
        hunter.RUN_SUMMARY.update(summary)
        hunter._write_heartbeat()
    return json.loads(captured['raw'])


def test_job_ran_ok():
    d = _write_and_read({'mode': 'daily', 'job_ran': True, 'signals': 7,
                         'discord_configured': True, 'error': None})
    assert d['status'] == 'ok'
    assert d['mode'] == 'daily'
    assert d['signals'] == 7
    assert d['discord_configured'] is True


def test_idle_when_no_job():
    d = _write_and_read({'mode': None, 'job_ran': False, 'signals': 0,
                         'discord_configured': False, 'error': None})
    assert d['status'] == 'idle', "菜单空跑/数据缺失早退必须记 idle, 不得 ok(假成功)"


def test_error_status_on_crash():
    d = _write_and_read({'mode': 'daily', 'job_ran': False, 'signals': 0,
                         'discord_configured': True, 'error': 'boom'})
    assert d['status'] == 'error'
    assert d['error'] == 'boom'


def test_discord_not_configured_reflected():
    d = _write_and_read({'mode': 'weekly', 'job_ran': True, 'signals': 0,
                         'discord_configured': False, 'error': None})
    assert d['status'] == 'ok'
    assert d['discord_configured'] is False  # 便于排查"扫了但没推"
