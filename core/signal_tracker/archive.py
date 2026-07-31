# -*- coding: utf-8 -*-
"""
信号归档 (Signal Tracker 职责 1)

archive_signal(): 信号归档 (扫描完成后写入, 幂等)。
拆分自 core/signal_tracker.py (P11)。
"""

import json
from datetime import datetime

import core.data_provider as dp

from ._shared import logger


def archive_signal(code, strategy, timeframe, entry, sl, tp,
                   ev_rating='', signal_date='', ev_score=None,
                   rr=0, name='', evidence='', **extra) -> str:
    """
    将新信号写入 signal_archive 表。幂等操作 — 相同 signal_id 不会重复插入。
    
    Returns:
        signal_id: 归档成功返回 ID, 已存在返回已有 ID, 失败返回 ''
    """
    if not signal_date:
        signal_date = datetime.now().strftime('%Y-%m-%d')
    
    # 标准化策略名
    strategy = strategy.upper()
    
    signal_id = f"{code}_{strategy}_{timeframe}_{signal_date}"
    scan_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not name:
        try:
            name = dp.get_stock_name(code)
        except Exception:
            name = ''
    
    # 将额外因子信息序列化为 JSON
    extra_json = json.dumps(extra, ensure_ascii=False, default=str) if extra else '{}'
    
    try:
        from core.database import get_db_connection
        with get_db_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signal_archive 
                (signal_id, code, name, strategy, timeframe, signal_date, scan_date,
                 entry_price, sl_price, tp_price, rr_ratio, ev_rating, ev_score, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal_id, code, name, strategy, timeframe, signal_date, scan_date,
                  entry, sl, tp, rr, ev_rating, ev_score, extra_json))
            conn.commit()
            
            if conn.total_changes > 0:
                # 去字母化(P0-5): 终端日志只显示命中因子证据, 不再打印经回测证明为噪声的 A+/A/B/C/D 假字母
                _log_tail = f" {evidence}" if evidence else ""
                logger.info(f"📥 信号归档: {name}({code}) [{strategy}/{timeframe}]{_log_tail}")
            else:
                logger.debug(f"信号已存在, 跳过: {signal_id}")
            return signal_id
    except Exception as e:
        logger.error(f"信号归档失败 {code}: {e}")
        return ''
