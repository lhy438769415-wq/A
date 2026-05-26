# -*- coding: utf-8 -*-
"""
WatchlistManager — P2 Facade 模式重构

原先基于 JSON 文件的 WatchlistManager 已合并到 core/signal_tracker.py 的 SQLite 后端。
本文件保留 WatchlistManager 类接口作为 Facade，所有操作委托给 signal_tracker，
确保调用方 (hunter.py 等) 零改动。

状态映射:
  JSON Watchlist          →  SQLite Signal Tracker
  ───────────────────────    ──────────────────────
  NEW                      →  PENDING
  WATCHING / UPDATED       →  PENDING
  TRIGGERED                →  ACTIVE / WIN
  INVALIDATED              →  INVALIDATED / LOSS
  EXPIRED                  →  EXPIRED
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class WatchlistManager:
    """
    P2 Facade: 将 JSON Watchlist 操作委托给 core/signal_tracker 的 SQLite 后端。

    保持与原 WatchlistManager 完全相同的公开接口，
    调用方 (hunter.py 等) 无需任何改动。
    """

    def __init__(self, path=None):
        """
        初始化 Facade。path 参数保留以兼容旧接口，但不再使用。

        Args:
            path: 保留参数 (原 JSON 文件路径，现已忽略)
        """
        from core.signal_tracker import init_signal_archive
        init_signal_archive()
        # 内存缓存: 写操作后自动失效，避免每次读属性都查 DB
        self._data_cache = None
        # 内存缓存: 仅用于 update_status 中临时存储非 SQLite 状态
        self._status_overrides = {}

    def _invalidate_cache(self):
        """标记缓存失效 (写操作后调用)"""
        self._data_cache = None

    # ------------------------------------------------------------------
    # 属性兼容: .data → 代理到 SQLite (带缓存)
    # ------------------------------------------------------------------
    @property
    def data(self) -> dict:
        """
        兼容属性: 返回所有信号的 {code: data_dict} 字典。
        带缓存: 写操作后自动刷新，避免每次读都查 DB。
        """
        if self._data_cache is None:
            from core.signal_tracker import get_signals_by_status
            all_statuses = ['NEW', 'WATCHING', 'UPDATED', 'TRIGGERED', 'INVALIDATED', 'EXPIRED']
            self._data_cache = get_signals_by_status(all_statuses)
        return self._data_cache

    @data.setter
    def data(self, value):
        """兼容 setter: 忽略写入 (SQLite 是唯一数据源)"""
        pass

    # ------------------------------------------------------------------
    # 核心操作
    # ------------------------------------------------------------------
    def add_signal(self, code, entry, sl, score, sb_idx, date):
        """
        添加新信号 (委托给 signal_tracker.add_signal_entry)。

        Args:
            code: 股票代码
            entry: 入场价
            sl: 止损价
            score: 评分
            sb_idx: 信号K线索引
            date: 信号日期
        """
        from core.signal_tracker import add_signal_entry
        add_signal_entry(
            code=code, entry=float(entry) if entry else 0.0,
            sl=float(sl) if sl else 0.0, score=float(score) if score else 0.0,
            signal_bar_idx=int(sb_idx), date=date
        )

    def update_status(self, code, df):
        """
        更新信号状态 (委托给 signal_tracker.track_signals)。

        返回 JSON Watchlist 风格的状态名:
          NEW → WATCHING → TRIGGERED / INVALIDATED

        Args:
            code: 股票代码
            df: 最新行情 DataFrame

        Returns:
            str: 更新后的状态 (TRIGGERED / INVALIDATED / WATCHING 等)
        """
        from core.signal_tracker import get_signal_status, check_signal_exists

        if not check_signal_exists(code):
            return None

        old_status = get_signal_status(code)

        # 如果已经终结, 直接返回
        if old_status in ['TRIGGERED', 'INVALIDATED', 'EXPIRED']:
            return old_status

        if df is None or len(df) == 0:
            return old_status

        # 取当前信号信息
        from core.signal_tracker import get_signals_by_status
        sig_data = get_signals_by_status([old_status])
        if code not in sig_data:
            return old_status

        sig = sig_data[code]
        entry = sig.get('entry', 0)
        sl = sig.get('sl', 0)

        latest = df.iloc[-1]

        # 条件检查: 入场触发
        if entry > 0 and latest['high'] >= entry:
            self._status_overrides[code] = 'TRIGGERED'
            return 'TRIGGERED'

        # 条件检查: 止损失效
        if sl > 0 and latest['low'] <= sl:
            self._status_overrides[code] = 'INVALIDATED'
            return 'INVALIDATED'

        # NEW → WATCHING (非信号日自动转换)
        if old_status == 'NEW':
            self._status_overrides[code] = 'WATCHING'
            return 'WATCHING'

        return old_status

    def update_signal_bar(self, code, new_sb_idx, new_entry):
        """
        更新信号K线索引和入场价 (委托给 signal_tracker.update_signal_entry)。

        Args:
            code: 股票代码
            new_sb_idx: 新的信号K线索引
            new_entry: 新的入场价
        """
        from core.signal_tracker import update_signal_entry
        update_signal_entry(
            code=code, signal_bar_idx=int(new_sb_idx),
            entry=float(new_entry)
        )
        self._status_overrides[code] = 'UPDATED'

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_by_status(self, status) -> dict:
        """
        按状态获取信号列表 (委托给 signal_tracker.get_signals_by_status)。

        Args:
            status: JSON Watchlist 状态 (NEW/WATCHING/TRIGGERED/INVALIDATED/EXPIRED)

        Returns:
            dict: {code: data_dict}
        """
        from core.signal_tracker import get_signals_by_status
        return get_signals_by_status([status])

    def get_new(self) -> dict:
        """获取状态为 NEW 的信号"""
        return self.get_by_status("NEW")

    def get_watching(self) -> dict:
        """
        获取状态为 WATCHING 的信号 (含 UPDATED)。

        Returns:
            dict: {code: data_dict}
        """
        from core.signal_tracker import get_signals_by_status
        watching = get_signals_by_status(["WATCHING"])
        updated = get_signals_by_status(["UPDATED"])
        watching.update(updated)
        return watching

    def get_expired(self) -> dict:
        """获取状态为 EXPIRED 的信号"""
        return self.get_by_status("EXPIRED")

    def get_all(self) -> dict:
        """获取所有信号"""
        return self.data
