
import pandas as pd
import threading
import queue
import logging
import time
from datetime import datetime, timedelta
# 🟢 Use ProcessPoolExecutor for true parallel fetching (Bypassing GIL & Baostock Lock)
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Optional, List, Dict, Any

from config import settings
from core.database import get_db_connection, init_db
from tools import fetcher

logger = logging.getLogger(__name__)

# ==========================================
# ⚡ Global Snapshot Cache
# ==========================================
_snapshot_cache = {}
_cache_lock = threading.Lock()
_last_snapshot_time = 0

# 📅 Holiday Calendar Cache
_trading_days_cache = set()
_last_calendar_update = 0

def is_trading_day(date_obj: datetime.date = None) -> bool:
    """
    Check if today (or date_obj) is a trading day in A-share market.
    
    🟢 优先使用本地 JSON 交易日历，避免每次启动都联网。
    """
    global _trading_days_cache, _last_calendar_update
    
    if date_obj is None:
        date_obj = datetime.now().date()
        
    date_str = date_obj.strftime("%Y-%m-%d")
    
    # 刷新缓存（空或过期时）
    now_ts = time.time()
    if not _trading_days_cache or (now_ts - _last_calendar_update > 86400):
        try:
            import json
            import os
            
            # 优先加载本地 JSON 交易日历
            json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'trading_calendar.json')
            
            if os.path.exists(json_path):
                logger.info("📅 Loading local trading calendar...")
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # data structure: {"2025": [...], "2026": [...]}
                    dates = []
                    for year, day_list in data.items():
                        if isinstance(day_list, list):
                            dates.extend(day_list)
                    
                    if dates:
                        _trading_days_cache = set(dates)
                        _last_calendar_update = now_ts
                        logger.info(f"✅ Local calendar loaded. {len(_trading_days_cache)} trading days.")
            else:
                logger.warning(f"⚠️ Local calendar not found: {json_path}")
                
        except Exception as e:
            logger.error(f"❌ Failed to load calendar: {e}")
        
        # Fallback: Simple Weekend Check
        if not _trading_days_cache:
            logger.warning("⚠️ Using weekend fallback for trading day check")
            return date_obj.weekday() < 5

    return date_str in _trading_days_cache

def preload_snapshots():
    """
    [Deprecated] 全市场快照预加载。
    在 Pure Baostock 模式下已完全废弃，调用此函数将不执行任何操作。
    """
    pass

def get_stock_name(code: str) -> str:
    """
    Get stock name from cache (fast) or return code if missing.
    Thread-safe. Now supports Baostock Only mode.
    """
    symbol = code.split('.')[-1]
    
    global _snapshot_cache
    
    with _cache_lock:
        # 如果缓存为空，尝试通过 Baostock 加载全市场名称
        if not _snapshot_cache:
            try:
                import baostock as bs
                from tools.fetcher_baostock import _ensure_login
                _ensure_login()
                
                logger.info("🏷️ Initializing stock name cache from Baostock...")
                rs = bs.query_stock_basic()
                if rs.error_code == '0':
                    while rs.next():
                        row = rs.get_row_data()
                        # row: [code, code_name, ipoDate, outDate, type, status]
                        s_code = row[0].split('.')[-1]
                        _snapshot_cache[s_code] = {'name': row[1]}
                    logger.info(f"✅ Loaded {len(_snapshot_cache)} stock names.")
                else:
                    logger.warning(f"⚠️ Failed to load names from Baostock: {rs.error_msg}")
            except Exception as e:
                logger.error(f"❌ Stock name initialization failed: {e}")

        if symbol in _snapshot_cache:
            return _snapshot_cache[symbol].get('name', code)
            
    return code

# ==========================================
# 🛡️ Data Integrity Guards
# ==========================================
def validate_integrity(symbol: str, df: pd.DataFrame) -> bool:
    """
    Perform strict checks on OHLCV data.
    Returns True if valid, False if corrupt.
    """
    if df.empty: return False
    
    try:
        # 1. Null/NaN Check
        if df.isnull().any().any():
            logger.warning(f"⚠️ {symbol}: Contains NaN values (Dropped)")
            return False
            
        # 2. Logic Check (High must be highest, Low must be lowest)
        # Using vectorized comparison with small epsilon for float precision
        # High >= Low
        if not (df['high'] >= df['low']).all():
            logger.warning(f"⚠️ {symbol}: Found High < Low")
            return False
            
        # High >= Open & High >= Close
        if not ((df['high'] >= df['open']) & (df['high'] >= df['close'])).all():
             logger.warning(f"⚠️ {symbol}: High is not max")
             return False
             
        # Low <= Open & Low <= Close
        if not ((df['low'] <= df['open']) & (df['low'] <= df['close'])).all():
             logger.warning(f"⚠️ {symbol}: Low is not min")
             return False

        # 3. Volume Check
        if (df['volume'] < 0).any():
            logger.warning(f"⚠️ {symbol}: Negative volume found")
            return False

        # 4. Duplicate Date Check
        if df['trade_date'].duplicated().any():
            logger.warning(f"⚠️ {symbol}: Duplicate dates found")
            return False

    except Exception as e:
        logger.error(f"❌ {symbol}: Validation crashed: {e}")
        return False
        
    return True

# ==========================================
# 🧱 Database Writer (Thread)
# ==========================================
class DatabaseWriter(threading.Thread):
    def __init__(self, data_queue):
        super().__init__()
        self.data_queue = data_queue
        self.running = True
        self.conn_ctx = None # 保存上下文管理器
        self.conn = None     # 保存真实的连接对象
        self.stats = {'success': 0, 'failed': 0, 'rejected': 0}

    def run(self):
        try:
            # 🟢 修复点：正确获取 ContextManager 中的连接对象
            self.conn_ctx = get_db_connection()
            self.conn = self.conn_ctx.__enter__()
            
            # Enable WAL mode explicitly for concurrency
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            # 🟢 V8.6: 增大 WAL cache 减少 fsync
            self.conn.execute("PRAGMA cache_size=-64000;")  # 64MB cache
            self.conn.execute("PRAGMA wal_autocheckpoint=2000;")  # 延迟 checkpoint
            
            # Prepare SQL statement
            sql_insert = """
                REPLACE INTO daily_bars 
                (symbol, trade_date, open, high, low, close, volume, adjust)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            logger.info("💾 [DB Writer] Engine started (Batched Commit Mode)...")
            
            # 🟢 V8.6: 批量 commit 优化 — 积攒 N 条后统一 commit
            BATCH_COMMIT_SIZE = 50  # 每 50 只股票 commit 一次
            pending_count = 0
            last_commit_time = time.time()
            COMMIT_INTERVAL = 3.0  # 最多 3 秒强制 commit 一次
            
            while self.running or not self.data_queue.empty():
                try:
                    item = self.data_queue.get(timeout=1)
                except queue.Empty:
                    # 超时时检查是否有待 commit 的数据
                    if pending_count > 0 and time.time() - last_commit_time > COMMIT_INTERVAL:
                        self.conn.commit()
                        pending_count = 0
                        last_commit_time = time.time()
                    continue

                if item is None:
                    # Poison pill — commit 残余数据后退出
                    if pending_count > 0:
                        self.conn.commit()
                    break

                symbol, df = item
                
                # Integrity Check
                if not validate_integrity(symbol, df):
                    self.stats['rejected'] += 1
                    self.data_queue.task_done()
                    continue
                    
                try:
                    if 'symbol' not in df.columns:
                        df['symbol'] = symbol
                    if 'adjust' not in df.columns:
                        df['adjust'] = 'qfq'
                        
                    df_to_write = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'adjust']]
                    records = df_to_write.to_records(index=False).tolist()
                    
                    # 🟢 V8.6: 不再逐条 commit，而是积攒后批量提交
                    self.conn.executemany(sql_insert, records)
                    pending_count += 1
                    self.stats['success'] += 1
                    
                    # 达到批量阈值或超时则 commit
                    if pending_count >= BATCH_COMMIT_SIZE or time.time() - last_commit_time > COMMIT_INTERVAL:
                        self.conn.commit()
                        pending_count = 0
                        last_commit_time = time.time()
                    
                except Exception as e:
                    self.stats['failed'] += 1
                    logger.error(f"❌ [DB Error] {symbol} Write failed: {e}")
                    try:
                        self.conn.rollback()
                        pending_count = 0
                    except: pass
                finally:
                    self.data_queue.task_done()
                        
        except Exception as e:
            logger.error(f"Database writer error: {e}")
        finally:
            # 🟢 修复点：正确退出上下文，归还连接给连接池
            if self.conn_ctx:
                try:
                    self.conn_ctx.__exit__(None, None, None)
                except: pass
            logger.info(f"🏁 [DB Writer] Stopped. Stats: {self.stats}")

    def stop(self):
        self.running = False

class WeeklyDatabaseWriter(threading.Thread):
    def __init__(self, data_queue):
        super().__init__()
        self.data_queue = data_queue
        self.running = True
        self.conn_ctx = None
        self.conn = None
        self.stats = {'success': 0, 'failed': 0, 'rejected': 0}

    def run(self):
        try:
            self.conn_ctx = get_db_connection()
            self.conn = self.conn_ctx.__enter__()
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            
            sql_insert = """
                REPLACE INTO weekly_bars 
                (symbol, trade_date, open, high, low, close, volume, adjust)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            logger.info("💾 [Weekly DB Writer] Engine started...")
            
            while self.running or not self.data_queue.empty():
                try:
                    item = self.data_queue.get(timeout=1)
                except queue.Empty:
                    continue

                if item is None: break

                symbol, df = item
                
                if not validate_integrity(symbol, df):
                    self.stats['rejected'] += 1
                    self.data_queue.task_done()
                    continue
                    
                try:
                    if 'symbol' not in df.columns: df['symbol'] = symbol
                    if 'adjust' not in df.columns: df['adjust'] = 'qfq'
                        
                    df_to_write = df[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'adjust']]
                    records = df_to_write.to_records(index=False).tolist()
                    
                    with self.conn: 
                        self.conn.executemany(sql_insert, records)
                    self.stats['success'] += 1
                    
                except Exception as e:
                    self.stats['failed'] += 1
                    logger.error(f"❌ [Weekly DB Error] {symbol}: {e}")
                finally:
                    self.data_queue.task_done()
                        
        except Exception as e:
            logger.error(f"Weekly Database writer error: {e}")
        finally:
            if self.conn_ctx:
                try: self.conn_ctx.__exit__(None, None, None)
                except: pass
            logger.info(f"🏁 [Weekly DB Writer] Stopped. Stats: {self.stats}")

    def stop(self):
        self.running = False


# ==========================================
# 🚀 Repository / Data Provider
# ==========================================

def get_stock_list() -> List[str]:
    """Get stock list from LOCAL DATABASE."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_bars'")
            if not cursor.fetchone():
                logger.warning("daily_bars table does not exist")
                return []

            cursor.execute("SELECT DISTINCT symbol FROM daily_bars")
            rows = cursor.fetchall()

            if not rows:
                logger.info("No stocks found in database")
                return []

            code_list = []
            for row in rows:
                symbol = row[0]
                if symbol.startswith('6'):
                    full_code = f"sh.{symbol}"
                else:
                    full_code = f"sz.{symbol}"
                code_list.append(full_code)

            logger.info(f"Retrieved {len(code_list)} stock codes from database")
            return code_list

    except Exception as e:
        logger.error(f"Failed to read stock list from database: {e}")
        return []

def get_last_date(symbol: str) -> Optional[str]:
    """Get max trade date for a symbol."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(trade_date) FROM daily_bars WHERE symbol=?", (symbol,))
            res = cursor.fetchone()
            return res[0] if res else None
    except:
        return None

def _read_sql_safe(query: str, conn, params: tuple = None) -> pd.DataFrame:
    """
    Execute read_sql with exponential backoff for 'database is locked' errors.
    """
    max_retries = 3
    base_delay = 0.5
    
    for i in range(max_retries):
        try:
            return pd.read_sql(query, conn, params=params)
        except Exception as e:
            if "database is locked" in str(e) or "database is malformed" in str(e): # Malformed sometimes reported when locked
                if i == max_retries - 1:
                    logger.error(f"❌ DB Locked after {max_retries} retries: {query[:50]}...")
                    raise e
                    
                delay = base_delay * (2 ** i)
                logger.warning(f"⚠️ DB Locked. Retry {i+1}... (Wait {delay}s)")
                time.sleep(delay)
            else:
                raise e
    return pd.DataFrame()

def get_stock_data(full_code: str, limit: int = None, timeframe: str = 'daily') -> Optional[pd.DataFrame]:
    """
    Get stock data (PURE OFFLINE - DB Only).
    
    This function ONLY reads from the local database.
    NO network requests will be made.
    
    For fresh data with live snapshot, use get_stock_data_hybrid() instead.
    But note: main.py and scanner.py should ONLY use this offline function
    to maintain architecture separation.
    """
    symbol = full_code.split('.')[-1]

    try:
        with get_db_connection() as conn:
            # 🟢 [Phase1] 移除 abu_indicators LEFT JOIN (该表从未写入，JOIN 列始终为 NULL)
            query = """
            SELECT 
                symbol, 
                trade_date as date, 
                open, high, low, close, volume
            FROM daily_bars
            WHERE symbol=? 
            ORDER BY trade_date ASC
            """
            df_hist = _read_sql_safe(query, conn, params=(symbol,))

            if df_hist.empty:
                return None

            if limit:
                df_hist = df_hist.tail(limit)

            return df_hist

    except Exception as e:
        logger.error(f"Failed to get stock data for {full_code}: {e}")
        return None

def get_stock_data_weekly(full_code: str, limit: int = None) -> Optional[pd.DataFrame]:
    """
    Get stock weekly data (PURE OFFLINE - DB Only).
    """
    symbol = full_code.split('.')[-1]

    try:
        with get_db_connection() as conn:
            query = """
            SELECT 
                symbol, 
                trade_date, 
                open, high, low, close, volume, adjust
            FROM weekly_bars
            WHERE symbol=? 
            ORDER BY trade_date ASC
            """
            df_hist = _read_sql_safe(query, conn, params=(symbol,))

            if df_hist.empty:
                return None

            if limit:
                df_hist = df_hist.tail(limit)

            return df_hist

    except Exception as e:
        logger.error(f"Failed to get stock weekly data for {full_code}: {e}")
        return None

# ==========================================
# 🔄 Update Logic
# ==========================================

# ==========================================
# 🔄 Update Logic (V8.5 Refactored)
# ==========================================

def get_latest_trade_date():
    now = datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        target_dt = now - timedelta(days=1)
    else:
        target_dt = now
    
    if target_dt.weekday() == 5: target_dt -= timedelta(days=1)
    elif target_dt.weekday() == 6: target_dt -= timedelta(days=2)
        
    return target_dt.strftime("%Y-%m-%d")


def get_all_last_dates_from_db() -> Dict[str, str]:
    """
    [Optimization] Batch fetch latest dates for all stocks.
    Returns: {symbol: '2024-01-25', ...}
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT symbol, MAX(trade_date) FROM daily_bars GROUP BY symbol"
            cursor.execute(query)
            rows = cursor.fetchall()
            return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.error(f"Failed to batch get last dates: {e}")
        return {}


def get_all_last_dates_from_weekly_db() -> Dict[str, str]:
    """
    Batch fetch latest dates for all stocks in weekly_bars.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT symbol, MAX(trade_date) FROM weekly_bars GROUP BY symbol"
            cursor.execute(query)
            rows = cursor.fetchall()
            return {row[0]: row[1] for row in rows}
    except Exception as e:
        logger.error(f"Failed to batch get last dates for weekly DB: {e}")
        return {}


def _fetch_worker(full_code, target_date, last_date_cache=None):
    """
    [Producer] Downloads data (Process Worker).
    Returns: (symbol, df) or None
    """
    symbol = full_code.split('.')[-1]
    
    # 1. Check local cache (CPU ops)
    if last_date_cache and symbol in last_date_cache:
        last_date = last_date_cache[symbol]
    else:
        last_date = None

    if last_date and last_date >= target_date:
        return None
    
    # 🟢 [Architecture Fix] Baostock Only 模式下，允许获取当日数据
    start_date = "20230101" if not last_date else (pd.to_datetime(last_date) + timedelta(days=1)).strftime("%Y%m%d")
    today_str = datetime.now().strftime("%Y%m%d")
    end_date = today_str
    
    if start_date > end_date:
         return None

    # 2. Network IO (Pure Baostock via fetcher)
    try:
        # Each process has its own Baostock session (lock is process-local and free)
        df = fetcher.fetch_daily_history_active(symbol, start_date, end_date)
        
        if df is not None and not df.empty:
            return (symbol, df)
            
    except Exception as e:
        # Logger might not work perfectly in MP on Windows without config, but try best effort
        print(f"Worker Error {symbol}: {e}")
        
    return None

def _fetch_weekly_worker(full_code, target_date, last_date_cache=None):
    """
    [Producer] Downloads weekly data.
    """
    symbol = full_code.split('.')[-1]
    
    if last_date_cache and symbol in last_date_cache:
        last_date = last_date_cache[symbol]
    else:
        last_date = None

    if last_date and last_date >= target_date:
        return None
    
    # 周线如果是新加入，默认往前抓取 15 年，为日后的长期 EMA, MACD 指标做充裕沉淀
    start_date = (datetime.now() - timedelta(days=15*365)).strftime("%Y%m%d") if not last_date else (pd.to_datetime(last_date) - timedelta(days=14)).strftime("%Y%m%d")
    today_str = datetime.now().strftime("%Y%m%d")
    end_date = today_str
    
    if start_date > end_date:
         return None

    try:
        df = fetcher.fetch_weekly_history_active(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return (symbol, df)
    except Exception as e:
        print(f"Weekly Worker Error {symbol}: {e}")
        
    return None


def update_daily_data_batch(max_workers=settings.MAX_WORKERS):
    """
    [Main Controller] V8.5 Robust Sync
    """
    init_db()
    target_date = get_latest_trade_date()

    
    # ==========================================
    # Phase 1: 存量维护 (Maintenance)
    # 优先保证数据库中已有股票的更新，不依赖外部列表
    # ==========================================
    logger.info("📊 [Phase 1] 启动存量维护 (Maintenance)...")
    db_status_map = get_all_last_dates_from_db()
    local_stock_list = sorted(list(db_status_map.keys()))
    
    # 构造标准代码格式
    maintenance_tasks = []
    skipped_count = 0
    
    for symbol in local_stock_list:
        full_code = f"sh.{symbol}" if symbol.startswith('6') else f"sz.{symbol}"
        last_date = db_status_map.get(symbol)
        
        if last_date and last_date >= target_date:
            skipped_count += 1
        else:
            maintenance_tasks.append(full_code)
            
    logger.info(f"✅ [Phase 1] 存量扫描: 总数 {len(local_stock_list)} | 待更 {len(maintenance_tasks)} | 最新 {skipped_count}")

    # ==========================================
    # Phase 2: 增量发现 (Discovery) - 可选
    # 尝试联网获取新上市股票，失败不影响 Phase 1
    # ==========================================
    discovery_tasks = []
    try:
        logger.info("🌐 [Phase 2] 启动增量发现 (Discovery)...")
        online_codes = fetcher.fetch_stock_list_active()
        
        if online_codes:
            # 提取 set 进行差集运算
            local_set = set(f"sh.{s}" if s.startswith('6') else f"sz.{s}" for s in local_stock_list)
            online_set = set(online_codes)
            
            new_stocks = online_set - local_set
            
            for code in new_stocks:
                discovery_tasks.append(code)
                
            logger.info(f"🆕 [Phase 2] 发现新股: {len(discovery_tasks)} 只")
        else:
            logger.warning("⚠️ [Phase 2] 联网列表为空，跳过新股发现 (VPN/网络限制)")
            
    except Exception as e:
        logger.warning(f"⚠️ [Phase 2] 增量发现失败: {e} (不影响存量更新)")
    finally:
         # 🟢 [Fix] 立即登出主进程的 Baostock 连接
         # 防止在后续漫长的下载过程中连接空闲超时 (WinError 10060)
         try:
             from tools.fetcher_baostock import bs_logout
             bs_logout()
         except ImportError:
             pass

    # 合并任务
    final_tasks = maintenance_tasks + discovery_tasks
    logger.info(f"🚀 [Final] 最终任务队列: {len(final_tasks)} (存量 {len(maintenance_tasks)} + 新股 {len(discovery_tasks)})")

    if not final_tasks:
        logger.info("🎉 所有数据已是最新！")
        return

    # 3. Queue & Writer Setup
    data_queue = queue.Queue(maxsize=1000) # 🟢 Boost queue for high throughput
    writer_thread = DatabaseWriter(data_queue)
    writer_thread.start()
    
    # 4. Filter Targets (Using the calculated final_tasks directly)
    to_update = final_tasks

    if not to_update:
        logger.info("🎉 All data is already up to date!")
        writer_thread.stop()
        writer_thread.join()
        return

    # 5. Parallel Downloading
    download_count = 0
    # 5. Parallel Downloading (Multiprocessing)
    download_count = 0
    
    # 🟢 Multiprocessing: 4 workers = 4 independent connections
    # Baostock is stable with 4 processes.
    mp_workers = max(1, max_workers) 
    if mp_workers > 6: mp_workers = 6 # Cap at 6 to be safe
    
    logger.info(f"🚀 Starting ProcessPool with {mp_workers} workers...")
    
    import concurrent.futures
    with ProcessPoolExecutor(max_workers=mp_workers) as executor:
        futures = []
        for code in to_update:
            # Pass only picklable args. data_queue is NOT passed.
            futures.append(executor.submit(_fetch_worker, code, target_date, db_status_map))

        # 🟢 [V9.13 Fix] as_completed(timeout=N) 是全局超时，不是单任务间隔超时。
        # 30 秒全局超时导致只能下载约 400 只股票就被强制终止。
        # 修复：移除全局超时，改为对每个 future.result() 设置单任务超时 (60s)。
        SINGLE_TASK_TIMEOUT = 60  # 单任务最大等待秒数
        for i, future in enumerate(as_completed(futures)):
            try:
                result = future.result(timeout=SINGLE_TASK_TIMEOUT)
                if result:
                    symbol, df = result
                    data_queue.put((symbol, df))
                    download_count += 1
            except concurrent.futures.TimeoutError:
                logger.warning(f"⚠️ 单任务超时 ({SINGLE_TASK_TIMEOUT}s)，跳过")
            except Exception as e:
                logger.error(f"Task failed: {e}")

            # 🟢 V8.6: 降低打印频率，减少主线程 I/O 竞争
            if i % 200 == 0:
                q_size = data_queue.qsize()
                if q_size > 800:
                    logger.warning(f"⚠️ Write Queue congestion: {q_size}/1000")
                logger.info(f"Progress: {i}/{len(to_update)} | New: {download_count} | Queue: {q_size}")

    # ==========================================
    # Phase 3: Snapshot Sync (Removed)
    # ==========================================
    # Logic completely removed to prevent AkShare IP bans.
    
    logger.info(f"✅ All Phases finished. Waiting for DB Writer...")
    
    # 🟢 Fix: Graceful Exit (Poison Pill)
    data_queue.put(None)
    writer_thread.join()
    
    # Explicit Baostock Logout (Final Cleanup)
    try:
        from tools.fetcher_baostock import bs_logout
        bs_logout()
    except ImportError:
        pass

    logger.info("💾 Database sync complete.")
    logger.info(f"🎉 Data Sync Completed! (Downloaded: {download_count})")


def _fast_local_weekly_aggregation(symbols_to_agg: List[str]):
    """
    [Core Optimizer]
    Instead of downloading from Baostock one by one (~15 mins),
    this function directly reads the last 21 days of 'daily_bars' from local SQLite,
    resamples them into ISO Year-Week aggregations, and writes them to 'weekly_bars'.
    Takes < 5 seconds for the entire market.
    """
    if not symbols_to_agg:
        return
        
    logger.info(f"⚡ 开始极速本地日线->周线增量聚合 (Fast Local Aggregation), 预计 < 5 秒... (待处理股票: {len(symbols_to_agg)})")
    
    # 提取存入数据库所需的纯数字 symbol
    symbol_codes = [s.split('.')[-1] for s in symbols_to_agg]
    
    try:
        batch_size = 800 
        total_inserted = 0
        
        with get_db_connection() as conn:
            # 开启 WAL 提升写入性能
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            
            sql_delete = "DELETE FROM weekly_bars WHERE symbol IN ({}) AND trade_date >= date('now', '-25 days')"
            
            sql_insert = """
                REPLACE INTO weekly_bars 
                (symbol, trade_date, open, high, low, close, volume, adjust)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            for i in range(0, len(symbol_codes), batch_size):
                batch = symbol_codes[i:i+batch_size]
                placeholders = ','.join(['?'] * len(batch))
                
                # 先清除尾部数周的旧数据，防止周中运行造成的同一周出现两根 K 线 (比如同时出现周三和周五的日期)
                del_query = sql_delete.format(placeholders)
                conn.execute(del_query, batch)
                
                # 获取过去 21 天的数据以确保覆盖最近 3-4 周
                query = f"SELECT symbol, trade_date, open, high, low, close, volume, adjust FROM daily_bars WHERE symbol IN ({placeholders}) AND trade_date >= date('now', '-25 days') ORDER BY trade_date ASC"
                
                df = pd.read_sql_query(query, conn, params=tuple(batch))
                
                if df is None or df.empty:
                    continue
                    
                # 向量化聚合
                df['trade_date_dt'] = pd.to_datetime(df['trade_date'])
                df['iso_year_week'] = df['trade_date_dt'].dt.strftime('%G-%V')
                
                # 分组聚合
                weekly = df.groupby(['symbol', 'iso_year_week']).agg({
                    'trade_date': 'max',
                    'open': 'first',
                    'high': 'max',
                    'low': 'min',
                    'close': 'last',
                    'volume': 'sum',
                    'adjust': 'last'
                }).reset_index()
                
                weekly['trade_date'] = pd.to_datetime(weekly['trade_date']).dt.strftime('%Y-%m-%d')
                
                # 转为记录存入数据库
                records = weekly[['symbol', 'trade_date', 'open', 'high', 'low', 'close', 'volume', 'adjust']].to_records(index=False).tolist()
                
                with conn:
                    conn.executemany(sql_insert, records)
                    
                total_inserted += len(records)
                
        logger.info(f"✅ 极速本地增量聚合完成！(成功写入了 {total_inserted} 条周线记录)")
        
    except Exception as e:
        logger.error(f"❌ 极速本地合并周线失败: {e}")


def update_weekly_data_batch(max_workers=settings.MAX_WORKERS):
    """
    [周线雷达] 全量/增量极速下载总调度 (复用日线的多进程队列框架)
    """
    init_db()
    target_date = get_latest_trade_date()

    logger.info("📊 [Weekly Phase 1] 启动存量维护 (Maintenance)...")
    db_status_map = get_all_last_dates_from_weekly_db()
    local_stock_list = sorted(list(db_status_map.keys()))
    
    maintenance_tasks = []
    skipped_count = 0
    
    for symbol in local_stock_list:
        full_code = f"sh.{symbol}" if symbol.startswith('6') else f"sz.{symbol}"
        last_date = db_status_map.get(symbol)
        
        # 周线只要到了最新的本周结束日即可
        if last_date and last_date >= target_date:
            skipped_count += 1
        else:
            maintenance_tasks.append(full_code)
            
    logger.info(f"✅ [Weekly Phase 1] 存量扫描: 总数 {len(local_stock_list)} | 待更 {len(maintenance_tasks)} | 最新 {skipped_count}")

    # ==========================================
    # 🌟 极速本地增量聚合 (Fast Local Aggregation)
    # 对于已经在周线库里沉淀过 15年 历史的股票，无需再次通过网络慢慢拉取，
    # 只需要把近期（最近数周）的日线数据在内存中转换成周线，瞬间写入即可！
    # ==========================================
    if maintenance_tasks:
        _fast_local_weekly_aggregation(maintenance_tasks)
        # 既然已经本地合并完毕，就不再投入排队等待网络下载
        maintenance_tasks = []

    discovery_tasks = []
    try:
        logger.info("🌐 [Weekly Phase 2] 启动增量发现 (基于日线名单挖掘)...")
        # 🟢 如果日线库有，但周线库没有，直接当作新发现的任务
        full_daily_list = get_stock_list()
        
        if full_daily_list:
            local_set = set(f"sh.{s}" if s.startswith('6') else f"sz.{s}" for s in local_stock_list)
            online_set = set(full_daily_list)
            
            new_stocks = online_set - local_set
            
            for code in new_stocks:
                discovery_tasks.append(code)
                
            logger.info(f"🆕 [Weekly Phase 2] 发现新股(需进行15年拉取): {len(discovery_tasks)} 只")
        else:
            logger.warning("⚠️ [Weekly Phase 2] 日线列表为空")
            
    except Exception as e:
        logger.warning(f"⚠️ [Weekly Phase 2] 增量发现失败: {e} (不影响存量更新)")
    finally:
         try:
             from tools.fetcher_baostock import bs_logout
             bs_logout()
         except ImportError:
             pass

    final_tasks = maintenance_tasks + discovery_tasks
    logger.info(f"🚀 [Weekly Final] 最终任务队列: {len(final_tasks)} (存量 {len(maintenance_tasks)} + 新股 {len(discovery_tasks)})")

    if not final_tasks:
        logger.info("🎉 周线库所有数据已是最新！")
        return

    data_queue = queue.Queue(maxsize=1000)
    writer_thread = WeeklyDatabaseWriter(data_queue)
    writer_thread.start()
    
    to_update = final_tasks

    if not to_update:
        logger.info("🎉 All weekly data is already up to date!")
        writer_thread.stop()
        writer_thread.join()
        return

    download_count = 0
    mp_workers = max(1, max_workers) 
    if mp_workers > 6: mp_workers = 6 
    
    logger.info(f"🚀 Starting ProcessPool with {mp_workers} workers for Weekly...")
    
    # **核心极速并行下载**
    with ProcessPoolExecutor(max_workers=mp_workers) as executor:
        futures = []
        for code in to_update:
            futures.append(executor.submit(_fetch_weekly_worker, code, target_date, db_status_map))

        for i, future in enumerate(as_completed(futures)):
            try:
                result = future.result()
                if result:
                    symbol, df = result
                    data_queue.put((symbol, df))
                    download_count += 1
            except Exception as e:
                logger.error(f"Weekly Task failed: {e}")

            # 🟢 完美复用工业级的全屏进度提现
            if i % 10 == 0 or i == len(to_update) - 1:
                q_size = data_queue.qsize()
                logger.info(f"⏳ 周线进度: {i+1}/{len(to_update)} ({(i+1)/len(to_update)*100:.1f}%) | Downloaded: {download_count} | WriteQueue: {q_size}")

    logger.info(f"✅ Download Finished. Waiting for DB Writer to commit {data_queue.qsize()} chunks...")
    
    data_queue.put(None)
    writer_thread.join()
    
    try:
        from tools.fetcher_baostock import bs_logout
        bs_logout()
    except ImportError:
        pass

    logger.info(f"🎉 Weekly Data Sync Completed! (Records modified: {download_count})")

# ==========================================
# 🔀 混合数据策略 (Hybrid Data Strategy)
# ==========================================




def get_stock_data_hybrid(full_code: str, limit: int = 200) -> Optional[pd.DataFrame]:
    """
    [Refactored V8.13] 混合数据获取策略 (已转为纯离线读取)。
    
    由于系统切换至 "Baostock Only / T+1" 模式，实时快照功能已弃用。
    本函数现在是一个安全的离线读取封装，确保架构兼容性。
    
    Args:
        full_code: 股票代码，格式 sh.600000 或 sz.000001
        limit: 返回的最大记录数
        
    Returns:
        DataFrame with OHLCV data, or None if failed
    """
    # 🟢 直接重定向到纯离线获取逻辑，移除所有 Snapshot/Network 代码
    return get_stock_data(full_code, limit=limit)

