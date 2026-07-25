# -*- coding: utf-8 -*-
"""
Signal Tracker 信号追踪器 — 拆分后子包入口。

原 core/signal_tracker.py (1363 行 God Module) 经 P11 拆分为:
  - archive.py     信号归档 (scan 完成后写入, 幂等)
  - tracking.py    追踪状态机 (每日/每周检查价格推进状态)
  - gaps.py        历史已止盈缺口查询 (供图表叠加绘制)
  - report.py      统计报表 + 报表 Discord 格式化
  - dashboard.py   交互式仪表盘 + Discord 异动推送
  - compat.py      P2 WatchlistManager 兼容层 (JSON→SQLite 状态映射)

本 __init__ 重新导出原模块的全部公开名称, 保证所有调用方
(hunter.py / notifier.py / watchlist.py / tests) 零改动。
"""

# 先暴露数据库句柄别名, 保证 patch('core.signal_tracker.get_db_connection')
# 仍能命中 tracking.track_signals (其内部通过包命名空间延迟解析该符号)
from core.database import get_db_connection, init_signal_archive  # noqa: F401

from .archive import archive_signal  # noqa: F401
from .tracking import track_signals  # noqa: F401
from .gaps import get_resolved_gaps  # noqa: F401
from .report import generate_report, format_tracker_discord_msg  # noqa: F401
from .dashboard import run_tracker_dashboard  # noqa: F401
from .compat import (  # noqa: F401
    _STATUS_MAP_JSON_TO_SQL,
    _STATUS_MAP_SQL_TO_JSON,
    check_signal_exists,
    add_signal_entry,
    get_signal_status,
    get_signals_by_status,
    update_signal_entry,
)

__all__ = [
    'archive_signal',
    'track_signals',
    'get_resolved_gaps',
    'generate_report',
    'format_tracker_discord_msg',
    'run_tracker_dashboard',
    '_STATUS_MAP_JSON_TO_SQL',
    '_STATUS_MAP_SQL_TO_JSON',
    'check_signal_exists',
    'add_signal_entry',
    'get_signal_status',
    'get_signals_by_status',
    'update_signal_entry',
    'get_db_connection',
    'init_signal_archive',
]
