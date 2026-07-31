# -*- coding: utf-8 -*-
"""
日志噪声断言门禁 (R4) —— 落实"终端噪声铁律"。

铁律: benign 告警须从源头消除, 不可靠 grep -v / redirect 隐瞒。
本测试把这条软原则变成硬门禁: 模拟一次**成功的同步收尾**, 断言全过程
ERROR 级日志行数为 0, 且成功标志(🎉 ... Data Sync Completed!)出现。

直接防的是本会话修复的 bug: 之前每次成功同步都刷一条假 [P1-7] ERROR。
复用抽出后的 `_finalize_sync` (包含投毒 pill + join 守卫 + 登出 + 成功日志)。
"""
import logging
from unittest.mock import MagicMock, patch

from core.data_provider import _finalize_sync


class _AllLevelRecorder(logging.Handler):
    """捕获全部级别日志, 供统计 ERROR 与成功标志。"""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def error_messages(self):
        return [r.getMessage() for r in self.records if r.levelno >= logging.ERROR]


def _capture(logger_name):
    log = logging.getLogger(logger_name)
    for h in list(log.handlers):
        log.removeHandler(h)
    rec = _AllLevelRecorder()
    log.addHandler(rec)
    log.setLevel(logging.DEBUG)
    return log, rec


def test_successful_sync_finish_emits_no_error():
    """成功收尾: 全程零 ERROR, 且成功标志出现 (落实终端噪声铁律)。"""
    log, rec = _capture("test_log_noise")
    thread = MagicMock()
    thread.is_alive.return_value = False  # 正常结束

    # 用 mock 替代真实 baostock 登出, 避免测试产生外部副作用
    with patch("tools.fetcher_baostock.bs_logout", lambda: None):
        result = _finalize_sync(
            thread, MagicMock(), "Daily", log,
            download_count=3189, skip_count=295, total_count=3484,
        )

    assert result == (3189, 3484)
    errs = rec.error_messages()
    assert not errs, f"成功同步却出现 ERROR 日志(违反终端噪声铁律): {errs}"
    # 成功标志必须出现
    assert any("Data Sync Completed" in r.getMessage() for r in rec.records), \
        "成功收尾必须打印 Data Sync Completed 标志"
