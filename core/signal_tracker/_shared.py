# -*- coding: utf-8 -*-
"""
Signal Tracker 子包 — 跨模块共享常量与日志器。

P11 拆分来源: 原 core/signal_tracker.py (1363 行 God Module) 拆分为
archive / tracking / gaps / report / dashboard / compat 六个聚焦模块。
本文件仅承载跨模块共享的:
  - logger (名称保持 'core.signal_tracker', 日志渠道不分裂)
  - 信号生命周期参数 PENDING_EXPIRY / ACTIVE_EXPIRY
"""

import logging

# 单一 logger, 名称与拆分前一致, 避免日志渠道分裂到子模块名
logger = logging.getLogger('core.signal_tracker')

# 信号生命周期参数
# 有效期: 超过此根数未触发入场, 标记 EXPIRED
PENDING_EXPIRY = {'daily': 20, 'weekly': 8}
# 持仓期限: 入场后超过此根数仍未触达 TP/SL, 标记 EXPIRED
ACTIVE_EXPIRY = {'daily': 60, 'weekly': 20}
