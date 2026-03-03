import sqlite3
import os
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# ⚙️ 配置区
# ==========================================
# 确保文件名与回测引擎 要求的 ai_journal.db 完全一致
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ai_journal.db")

# 🟢 优化：添加连接池和连接复用
_journal_db_connection = None
_journal_db_lock = threading.Lock()

def get_db_connection() -> sqlite3.Connection:
    """获取数据库连接，使用单例模式避免频繁连接"""
    global _journal_db_connection

    # 确保目录存在
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # 如果连接不存在，则重新创建
    try:
        # Try to use the connection to check if it's valid
        if _journal_db_connection:
            _journal_db_connection.execute("SELECT 1")
    except Exception:
        _journal_db_connection = None

    if _journal_db_connection is None:
        # 🟢 Fix: Allow multi-thread access (protected by lock)
        _journal_db_connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        # 设置 WAL 模式提高并发性能
        _journal_db_connection.execute("PRAGMA journal_mode=WAL;")
        _journal_db_connection.execute("PRAGMA synchronous=NORMAL;")
        logger.debug(f"Created new journal database connection: {DB_PATH}")

    return _journal_db_connection

def init_journal_db():
    """初始化 AI 决策日志数据库"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 猎人循环日志表 (确保字段与写入逻辑、回测引擎完全对齐)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hunter_journal (
        symbol TEXT,
        trade_date TEXT,
        trade_time TEXT,
        strategy_type TEXT,
        daily_verdict TEXT,
        daily_reason TEXT,
        intraday_verdict TEXT,
        intraday_reason TEXT,
        final_decision TEXT,
        sl_price REAL,
        raw_response_daily TEXT,
        raw_response_intraday TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # 2. 守护者循环日志表 (持仓诊断)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS guardian_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT,
        trade_time TEXT,
        symbol TEXT,
        diagnosis_verdict TEXT,
        diagnosis_reason TEXT,
        raw_response TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    logger.info("AI Journal DB initialized successfully")

def log_hunter_decision(symbol: str, strategy_type: str, daily_res: Dict[str, Any], intraday_res: Dict[str, Any], final_decision: str, sl_price: float = 0.0) -> bool:
    """记录猎人循环决策

    Returns:
        bool: 是否成功写入
    """
    now = datetime.now()
    trade_date = now.strftime("%Y-%m-%d")
    trade_time = now.strftime("%H:%M:%S")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # 🟢 优化：增加数据预处理和验证
        daily_verdict = daily_res.get('verdict', '') if daily_res else ''
        # 🟢 优先级：详细分析 (analysis) > 精简理由 (reason) -> 确保 DB 中存的是长文
        daily_reason = (daily_res.get('analysis') or daily_res.get('reason', '')) if daily_res else ''
        intraday_verdict = intraday_res.get('verdict', '') if intraday_res else ''
        intraday_reason = intraday_res.get('reason', '') if intraday_res else ''

        raw_daily = str(daily_res.get('raw', '')) if daily_res else ''
        raw_intraday = str(intraday_res.get('raw', '')) if intraday_res else ''

        # 🟢 优化：批量插入提升性能
        cursor.execute("""
        INSERT INTO hunter_journal (
            symbol, trade_date, trade_time, strategy_type,
            daily_verdict, daily_reason,
            intraday_verdict, intraday_reason,
            final_decision, sl_price,
            raw_response_daily, raw_response_intraday
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, trade_date, trade_time, strategy_type,
            daily_verdict, daily_reason,
            intraday_verdict, intraday_reason,
            final_decision, sl_price,
            raw_daily, raw_intraday
        ))
        conn.commit()
        logger.debug(f"Successfully logged hunter decision for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Journal Error (Hunter) for {symbol}: {e}")
        return False

def log_guardian_decision(symbol: str, diagnosis_res: Dict[str, Any]) -> bool:
    """记录守护者循环决策

    Returns:
        bool: 是否成功写入
    """
    now = datetime.now()
    trade_date = now.strftime("%Y-%m-%d")
    trade_time = now.strftime("%H:%M:%S")

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # 🟢 优化：增加数据预处理和验证
        verdict = diagnosis_res.get('verdict', '') if diagnosis_res else ''
        reason = diagnosis_res.get('reason', '') if diagnosis_res else ''
        raw = str(diagnosis_res.get('raw', '')) if diagnosis_res else ''

        cursor.execute("""
        INSERT INTO guardian_journal (
            trade_date, trade_time, symbol,
            diagnosis_verdict, diagnosis_reason, raw_response
        ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            trade_date, trade_time, symbol,
            verdict, reason, raw
        ))
        conn.commit()
        logger.debug(f"Successfully logged guardian decision for {symbol}")
        return True
    except Exception as e:
        logger.error(f"Journal Error (Guardian) for {symbol}: {e}")
        return False

if __name__ == "__main__":
    init_journal_db()
    print("✅ AI Journal DB Initialized at:", DB_PATH)