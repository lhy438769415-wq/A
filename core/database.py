# -*- coding: utf-8 -*-
"""
数据库连接池与初始化模块

功能:
1. SQLite 连接池管理（避免频繁创建/销毁连接）
2. WAL 模式支持高并发读写
3. 自动创建表结构
"""

import sqlite3
import os
import queue
import threading
import logging
from contextlib import contextmanager
from config import settings

logger = logging.getLogger(__name__)

# =========================================================================
# 连接池配置
# =========================================================================
_db_pool = queue.Queue()
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = settings.DB_POOL_SIZE

# 连接池统计（调试用）
_pool_stats = {
    'created': 0,   # 新建连接数
    'reused': 0,    # 复用连接数
    'closed': 0     # 关闭连接数
}

# =========================================================================
# 连接池上下文管理器
# =========================================================================
@contextmanager
def get_db_connection():
    """
    获取数据库连接（自动连接池管理）
    
    用法:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
            conn.commit()
    """
    conn = None
    try:
        with _pool_lock:
            if not _db_pool.empty():
                # 从池中获取已有连接
                conn = _db_pool.get_nowait()
                logger.debug("从连接池复用连接")
                _pool_stats['reused'] += 1
            else:
                # 创建新连接
                os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
                # 允许跨线程使用（由队列保证线程安全）
                conn = sqlite3.connect(
                    settings.DB_PATH, 
                    timeout=settings.DB_TIMEOUT, 
                    check_same_thread=False
                )
                # 启用 WAL 模式提升并发性能
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                logger.debug(f"创建新数据库连接: {settings.DB_PATH}")
                _pool_stats['created'] += 1

        yield conn

    except Exception as e:
        logger.error(f"数据库连接错误: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        raise
    finally:
        if conn:
            try:
                # 检查连接是否仍然有效
                conn.execute("SELECT 1")
                with _pool_lock:
                    if _db_pool.qsize() < _MAX_POOL_SIZE:
                        # 归还到连接池
                        _db_pool.put_nowait(conn)
                    else:
                        # 池已满，直接关闭
                        conn.close()
                        logger.debug("连接池已满，关闭连接")
                        _pool_stats['closed'] += 1
            except Exception as e:
                logger.error(f"归还连接到池时出错: {e}")
                try:
                    conn.close()
                except:
                    pass

# =========================================================================
# 数据库初始化
# =========================================================================
def init_db():
    """
    初始化数据库表结构
    
    表结构 (daily_bars):
    - symbol: 股票代码 (如 '600000')
    - trade_date: 交易日期 (YYYY-MM-DD)
    - open/high/low/close: OHLC 价格
    - volume: 成交量
    - adjust: 复权标识 ('qfq'/'hfq'/'none')
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                adjust TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON daily_bars (symbol);")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS weekly_bars (
                symbol TEXT,
                trade_date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                adjust TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_weekly_symbol ON weekly_bars (symbol);")
            
            # [V1.9 Data Engineering] Pre-calculated Indicators Store
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS abu_indicators (
                symbol TEXT,
                trade_date TEXT,
                ema_20 REAL,
                atr REAL,
                trend_slope REAL,
                wick_pct REAL,
                gap_down_count INTEGER,
                relative_vol REAL,
                linreg_res REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, trade_date)
            );
            """)
            
            conn.commit()
            logger.info("✅ 数据库初始化成功 (Tables: daily_bars, abu_indicators)")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise
