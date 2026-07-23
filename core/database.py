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

# init_db 进程内幂等: 仅首次调用执行建表与日志 (多策略扫描会多次调用 init_db)
_INIT_LOCK = threading.Lock()
_INIT_DONE = False

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
            except Exception:                pass
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
                except Exception:                    pass

# =========================================================================
# 数据库初始化
# =========================================================================
def init_db():
    """
    初始化数据库表结构（进程内幂等: 仅首次调用执行建表与日志,
    后续调用直接返回, 避免多策略扫描时重复刷屏 / 重复 DDL）。

    表结构 (daily_bars):
    - symbol: 股票代码 (如 '600000')
    - trade_date: 交易日期 (YYYY-MM-DD)
    - open/high/low/close: OHLC 价格
    - volume: 成交量
    - adjust: 复权标识 ('qfq'/'hfq'/'none')
    """
    global _INIT_DONE
    with _INIT_LOCK:
        if _INIT_DONE:
            return
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
            
            # [Signal Tracker] 信号追踪归档表
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_archive (
                signal_id      TEXT PRIMARY KEY,
                code           TEXT NOT NULL,
                name           TEXT,
                strategy       TEXT NOT NULL,
                timeframe      TEXT DEFAULT 'daily',
                signal_date    TEXT NOT NULL,
                scan_date      TEXT NOT NULL,
                entry_price    REAL,
                sl_price       REAL,
                tp_price       REAL,
                rr_ratio       REAL,
                ev_rating      TEXT,
                ev_score       INTEGER,
                status         TEXT DEFAULT 'PENDING',
                activated_date TEXT,
                resolved_date  TEXT,
                exit_price     REAL,
                max_favorable  REAL,
                max_adverse    REAL,
                bars_to_resolve INTEGER,
                extra_json     TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sa_status ON signal_archive (status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sa_code ON signal_archive (code);")

            # [Review Bridge] 复盘报告表 (baostock.db 单一来源, 由 core.database 拥有)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_reviews (
                review_id       TEXT PRIMARY KEY,
                signal_id       TEXT,
                code            TEXT NOT NULL,
                market          TEXT DEFAULT 'CN',
                trade_date      TEXT NOT NULL,
                strategy        TEXT DEFAULT 'STRUCTURAL_GAP',
                direction       TEXT DEFAULT 'LONG',
                market_state    TEXT,
                structure_tf    TEXT,
                key_levels      TEXT,
                vacuum_check    TEXT,
                entry_tf        TEXT,
                signal_bar_note TEXT,
                micro_pattern   TEXT,
                pattern_tags    TEXT,
                pattern_combo   TEXT,
                momentum_type   TEXT,
                always_in_dir   TEXT,
                trap_check      TEXT,
                planned_rr      REAL,
                order_type      TEXT,
                entry_price     REAL,
                sl_price        REAL,
                tp_price        REAL,
                open_time       TEXT,
                exit_price      REAL,
                exit_type       TEXT,
                result          TEXT,
                final_r         REAL,
                close_time      TEXT,
                is_correct      TEXT,
                context_tag     TEXT,
                entry_reason    TEXT,
                skip_reason     TEXT,
                execution_score INTEGER,
                lesson_tag      TEXT,
                review_report   TEXT,
                notes           TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_code ON trade_reviews (code);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_date ON trade_reviews (trade_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_strategy ON trade_reviews (strategy);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_direction ON trade_reviews (direction);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_market_state ON trade_reviews (market_state);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tr_result ON trade_reviews (result);")

            conn.commit()
        with _INIT_LOCK:
            _INIT_DONE = True
        logger.info("✅ 数据库初始化成功 (Tables: daily_bars, weekly_bars, abu_indicators, signal_archive, trade_reviews)")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise


def init_signal_archive():
    """初始化 signal_archive 表 (委托 baostock.db 单一来源, 幂等)。

    历史: signal_archive 曾同时在 core/signal_tracker.py 与 core/database.py
    各定义一份, 构成 schema 漂移隐患。现已统一由本模块拥有, signal_tracker
    仅委托调用, 禁止在其它文件重复定义。
    """
    init_db()


def init_review_db():
    """初始化 trade_reviews 表 (委托 baostock.db 单一来源, 幂等)。

    trade_reviews 归 core/database.py (baostock.db) 单一所有, review_bridge
    仅委托调用, 禁止在其它文件重复定义。
    """
    init_db()


def close_all_connections():
    """
    优雅关闭所有数据库连接：WAL checkpoint → 排空连接池 → 关闭连接
    
    用于程序退出时确保 WAL 临时文件合并到主库，避免下次启动时的恢复等待。
    """
    global _db_pool
    drained = 0
    with _pool_lock:
        while not _db_pool.empty():
            try:
                conn = _db_pool.get_nowait()
                # 先做 WAL checkpoint，把临时文件合并进主库
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.close()
                drained += 1
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
    if drained > 0:
        logger.info(f"🔒 连接池已排空: {drained} 个连接 (WAL checkpoint 完成)")
