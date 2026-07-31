# -*- coding: utf-8 -*-
"""
P1-7 join 守卫行为契约测试 (R2)。

P1-7 当初只测了 future 收割看门狗, 从没测 `writer_thread.join` 这段超时守卫。
本测试补齐"成功/失败两条路径"的契约, 直接堵死 `if not x.join()` 式误用复发的洞:

  - 成功路径: writer 正常结束 -> 不触发 [P1-7] ERROR, 且不调用 stop()
  - 失败路径: writer 在 join 超时后仍 is_alive() -> 触发 [P1-7] ERROR 且调用 stop()

测试直接驱动抽出后的 `_wait_writer_stop` (纯单元, 无需网络/真实线程)。
"""
import logging
from unittest.mock import MagicMock

from core.data_provider import _wait_writer_stop


class _ErrorRecorder(logging.Handler):
    """捕获 ERROR 及以上级别日志, 供断言使用。"""
    def __init__(self):
        super().__init__()
        self.error_messages = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.error_messages.append(record.getMessage())


def _make_logger(name):
    log = logging.getLogger(name)
    # 避免污染全局根 logger / 重复 handler
    for h in list(log.handlers):
        log.removeHandler(h)
    rec = _ErrorRecorder()
    log.addHandler(rec)
    log.setLevel(logging.DEBUG)
    return log, rec


def test_writer_finishes_clean_no_error():
    """成功路径: writer 正常结束 -> 零 [P1-7] ERROR, 不调用 stop()。"""
    log, rec = _make_logger("test_join_ok")
    thread = MagicMock()
    thread.is_alive.return_value = False  # 正常: join 后线程已退出

    timed_out = _wait_writer_stop(thread, "DB Writer", log)

    assert timed_out is False, "正常结束应返回 False(未超时)"
    assert not any("[P1-7]" in m for m in rec.error_messages), \
        f"成功路径不应报 [P1-7], 实际: {rec.error_messages}"
    thread.stop.assert_not_called(), "成功路径不应调用 stop()"


def test_writer_hung_triggers_error_and_stop():
    """失败路径: writer 卡死 -> 触发 [P1-7] ERROR 且调用 stop()。"""
    log, rec = _make_logger("test_join_hung")
    thread = MagicMock()
    thread.is_alive.return_value = True  # 卡死: join 超时后仍活着

    timed_out = _wait_writer_stop(thread, "DB Writer", log)

    assert timed_out is True, "卡死应返回 True(超时)"
    assert any("[P1-7]" in m for m in rec.error_messages), \
        f"卡死必须报 [P1-7], 实际 ERROR: {rec.error_messages}"
    thread.stop.assert_called_once(), "卡死必须调用 stop() 标记停止"
