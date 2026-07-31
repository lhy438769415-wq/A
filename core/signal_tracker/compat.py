# -*- coding: utf-8 -*-
"""
P2 WatchlistManager 兼容层 (JSON Watchlist → SQLite Signal Tracker)

状态映射 + 5 个兼容函数, 供 tools/watchlist.py 委托调用。
拆分自 core/signal_tracker.py (P11)。
"""

from datetime import datetime
import json

from core.database import get_db_connection, init_signal_archive

from ._shared import logger
from .archive import archive_signal


# 状态映射: JSON Watchlist → SQLite Signal Tracker
_STATUS_MAP_JSON_TO_SQL = {
    'NEW': 'PENDING',
    'WATCHING': 'PENDING',
    'UPDATED': 'PENDING',
    'TRIGGERED': 'ACTIVE',
    'INVALIDATED': 'INVALIDATED',
    'EXPIRED': 'EXPIRED',
}
_STATUS_MAP_SQL_TO_JSON = {
    'PENDING': 'WATCHING',     # PENDING 在 Watchlist 视角 = 等待/观察中
    'ACTIVE': 'TRIGGERED',     # ACTIVE = 已入场触发
    'WIN': 'TRIGGERED',        # WIN = 已触发后止盈
    'LOSS': 'INVALIDATED',     # LOSS = 已触发后止损
    'EXPIRED': 'EXPIRED',      # 过期
    'INVALIDATED': 'INVALIDATED',
}


def check_signal_exists(code: str, timeframe: str = 'daily') -> bool:
    """
    检查指定代码是否存在信号记录。

    Args:
        code: 股票代码
        timeframe: 时间周期 (daily/weekly)

    Returns:
        bool: 是否存在信号
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM signal_archive WHERE code = ? AND timeframe = ? LIMIT 1",
                (code, timeframe)
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.error(f"检查信号存在性失败 {code}: {e}")
        return False


def add_signal_entry(code: str, entry: float, sl: float, score: float = 0,
                     signal_bar_idx: int = -1, date: str = '',
                     timeframe: str = 'daily', strategy: str = '') -> str:
    """
    添加信号记录 (WatchlistManager.add_signal 的兼容接口)。

    将 JSON Watchlist 的 NEW 状态映射为 SQLite 的 PENDING 状态。

    Args:
        code: 股票代码
        entry: 入场价
        sl: 止损价
        score: 评分
        signal_bar_idx: 信号K线索引
        date: 信号日期
        timeframe: 时间周期
        strategy: 策略名称

    Returns:
        str: signal_id (空字符串表示失败)
    """
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')

    if not strategy:
        strategy = 'UNKNOWN'

    signal_id = archive_signal(
        code=code, strategy=strategy, timeframe=timeframe,
        entry=entry, sl=sl, tp=0,
        signal_date=date, name='',
        signal_bar_idx=signal_bar_idx, score=score
    )
    return signal_id


def get_signal_status(code: str, timeframe: str = 'daily') -> str:
    """
    获取指定代码的最新信号状态 (映射为 JSON Watchlist 的状态名)。

    Args:
        code: 股票代码
        timeframe: 时间周期

    Returns:
        str: JSON Watchlist 状态 (NEW/WATCHING/TRIGGERED/INVALIDATED/EXPIRED)
             空字符串表示无记录
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT status FROM signal_archive WHERE code = ? AND timeframe = ? "
                "ORDER BY scan_date DESC LIMIT 1",
                (code, timeframe)
            ).fetchone()
            if row is None:
                return ''
            sql_status = row[0]
            return _STATUS_MAP_SQL_TO_JSON.get(sql_status, 'WATCHING')
    except Exception as e:
        logger.error(f"获取信号状态失败 {code}: {e}")
        return ''


def get_signals_by_status(statuses: list, timeframe: str = None) -> dict:
    """
    按状态筛选信号，返回 {code: data_dict} 格式 (兼容 WatchlistManager.get_by_status)。

    Args:
        statuses: JSON Watchlist 状态列表 (如 ['NEW', 'WATCHING'])
        timeframe: 可选时间周期过滤

    Returns:
        dict: {code: {status, entry, sl, score, signal_bar_idx, ...}}
    """
    init_signal_archive()

    # 将 JSON 状态映射为 SQL 状态
    sql_statuses = set()
    for s in statuses:
        mapped = _STATUS_MAP_JSON_TO_SQL.get(s, None)
        if mapped:
            sql_statuses.add(mapped)
    if not sql_statuses:
        return {}

    try:
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(sql_statuses))
            params = list(sql_statuses)
            query = f"SELECT * FROM signal_archive WHERE status IN ({placeholders})"
            if timeframe:
                query += " AND timeframe = ?"
                params.append(timeframe)
            query += " ORDER BY scan_date DESC"

            rows = conn.execute(query, params).fetchall()
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]

            result = {}
            seen_codes = set()
            for row in rows:
                sig = dict(zip(col_names, row))
                code = sig['code']
                # 去重：每个 code 只保留最新记录
                if code in seen_codes:
                    continue
                seen_codes.add(code)

                sql_status = sig['status']
                json_status = _STATUS_MAP_SQL_TO_JSON.get(sql_status, 'WATCHING')

                # [P0-2.4] 从 extra_json 解析真实 signal_bar_idx (不再恒为 -1)
                signal_bar_idx = -1
                try:
                    extra = json.loads(sig.get('extra_json', '{}') or '{}')
                    signal_bar_idx = extra.get('signal_bar_idx', -1)
                except Exception:
                    pass
                result[code] = {
                    'status': json_status,
                    'entry': sig.get('entry_price', 0) or 0,
                    'sl': sig.get('sl_price', 0) or 0,
                    'score': sig.get('ev_score', 0) or 0,
                    'signal_bar_idx': signal_bar_idx,
                    'signal_date': sig.get('signal_date', ''),
                    'added_date': sig.get('scan_date', ''),
                    'days_watching': 0,
                }
            return result
    except Exception as e:
        logger.error(f"按状态获取信号失败: {e}")
        return {}


def update_signal_entry(code: str, signal_bar_idx: int = -1, entry: float = None,
                        timeframe: str = 'daily', signal_id: str = '') -> bool:
    """
    更新信号记录的入场价和信号K线索引 (WatchlistManager.update_signal_bar 兼容接口)。

    Args:
        code: 股票代码
        signal_bar_idx: 新的信号K线索引
        entry: 新的入场价
        timeframe: 时间周期

    Returns:
        bool: 是否更新成功
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            updates = []
            params = []

            if entry is not None:
                updates.append("entry_price = ?")
                params.append(entry)

            updates.append("updated_at = ?")
            params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            if not updates:
                return False

            # [P0-2.3] 精确更新: 优先按 signal_id, 否则按 code+timeframe 仅更新最新一条
            if signal_id:
                params.append(signal_id)
                query = f"UPDATE signal_archive SET {', '.join(updates)} WHERE signal_id = ?"
            else:
                # SQLite 的 UPDATE 不支持 ORDER BY/LIMIT, 改用子查询锁定最新一行的 signal_id
                row = conn.execute(
                    "SELECT signal_id FROM signal_archive "
                    "WHERE code = ? AND timeframe = ? ORDER BY scan_date DESC LIMIT 1",
                    (code, timeframe),
                ).fetchone()
                if row is None:
                    return False
                params.append(row[0])
                query = f"UPDATE signal_archive SET {', '.join(updates)} WHERE signal_id = ?"
            conn.execute(query, params)
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"更新信号记录失败 {code}: {e}")
        return False
