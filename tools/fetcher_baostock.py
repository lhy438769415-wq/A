# -*- coding: utf-8 -*-
"""
Baostock 数据源适配器
用于获取A股历史数据，无需注册、完全免费、稳定可靠。

数据能力:
- 日线数据：完整历史
- 分钟线数据：5/15/30/60分钟
- 股票列表：全A股
"""

import baostock as bs
import pandas as pd
from typing import List, Optional
import logging
import time
import sys
from functools import wraps
from core.log_config import get_logger

logger = get_logger(__name__)


class BsBlacklistedError(Exception):
    """Baostock 服务端返回黑名单封禁时抛出，上层应立即终止同步。"""
    pass


class BsConnectionDeadError(Exception):
    """Baostock 登录超时/连接断开时抛出，表示本进程的 session 不可恢复。
    与 BsBlacklistedError 同等对待：穿透 @retry_on_failure，不做重试。"""
    pass

# =========================================================================
# 连接管理
# 🟢 Baostock Mechanism Note:
# Baostock server allows multiple login sessions from the same IP.
# In Multiprocessing mode, each process calls bs.login() independently.
# This creates n separate sessions, which is valid and does not trigger IP bans
# as long as the total request frequency (requests per second) is reasonable.
# =========================================================================
import threading
import sys

_bs_lock = threading.Lock()
_bs_logged_in = False

# =========================================================================
# 🛡️ 超时保护器 (Timeout Guard)
# Baostock 底层使用原生 socket 且无超时设置，在 VPN/不稳定网络环境下
# bs.login() 和 bs.query_*() 可能因 TCP recv() 无限等待而卡死。
# 此函数通过子线程 + Event 实现非侵入式超时包装。
# =========================================================================
def _run_with_timeout(func, args=(), kwargs=None, timeout=30, desc="operation"):
    """
    在子线程中执行 func，超时则抛出 TimeoutError。
    
    Args:
        func: 要执行的函数
        args: 位置参数
        kwargs: 关键字参数
        timeout: 超时秒数 (默认 30s)
        desc: 操作描述 (用于日志)
    Returns:
        func 的返回值
    Raises:
        TimeoutError: 超时
        Exception: func 内部异常
    """
    if kwargs is None:
        kwargs = {}
    result_container = [None]
    error_container = [None]
    done_event = threading.Event()
    
    def _worker():
        try:
            result_container[0] = func(*args, **kwargs)
        except Exception as e:
            error_container[0] = e
        finally:
            done_event.set()
    
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    
    if not done_event.wait(timeout=timeout):
        raise TimeoutError(f"Baostock {desc} 超时 ({timeout}s)，可能是网络/VPN不稳定")
    
    if error_container[0] is not None:
        raise error_container[0]
    
    return result_container[0]

# 🟢 Baostock 网络操作的默认超时 (秒)
# 登录超时与查询超时对齐：6 Worker 并发建立 VPN 连接时，
# 最后一个 Worker 的 TCP 握手可能需要 20-31 秒（含重传），20 秒会误杀健康连接
BS_LOGIN_TIMEOUT = 45
BS_QUERY_TIMEOUT = 45

# 🟢 登录重试参数
# 6 Worker 并发 bs.login() 时，最后 1-2 个可能因服务端/VPN 并发限制失败
# 等其他 Worker 登录完成后重试，竞争消失，成功率大幅提升
BS_LOGIN_MAX_RETRIES = 3     # 最大重试次数
BS_LOGIN_RETRY_DELAY = 5.0   # 首次重试等待秒数 (后续按 attempt 递增: 5s, 10s)

def _ensure_login(force=False):
    """
    确保 Baostock 已登录（惰性连接）
    
    🛡️ 带退避重试的登录机制：
    - 6 Worker 并发登录时，部分 Worker 可能因服务端并发限制失败
    - 等待其他 Worker 完成登录后重试，利用网络空闲窗口恢复连接
    - 黑名单立即穿透，不做重试
    
    Args:
        force: 是否强制重连
    
    Raises:
        BsBlacklistedError: 黑名单封禁，不可重试
        BsConnectionDeadError: 所有重试均失败，连接不可恢复
    """
    global _bs_logged_in
    if force and _bs_logged_in:
        logger.info("强制重置 Baostock 连接...")
        bs_logout()
    
    if _bs_logged_in:
        return
    
    last_error = None
    for attempt in range(1, BS_LOGIN_MAX_RETRIES + 1):
        try:
            lg = _run_with_timeout(bs.login, timeout=BS_LOGIN_TIMEOUT, desc="login")
        except TimeoutError as e:
            last_error = f"登录超时: {e}"
        except Exception as e:
            # bs.login() 内部 Python 异常 (OSError/ConnectionResetError 等)
            last_error = f"登录异常: {e}"
        else:
            # bs.login() 正常返回，检查 error_code
            if lg.error_code == '0':
                _bs_logged_in = True
                logger.info("✅ Baostock 登录成功")
                return
            # 黑名单立即穿透，不做重试
            if '黑名单' in str(lg.error_msg):
                raise BsBlacklistedError(f"Baostock 黑名单封禁: {lg.error_msg}")
            last_error = f"登录失败: {lg.error_msg}"
        
        # 本次登录未成功，决定是否重试
        if attempt < BS_LOGIN_MAX_RETRIES:
            delay = BS_LOGIN_RETRY_DELAY * attempt  # 5s, 10s
            logger.warning(f"⚠️ Baostock {last_error} (尝试 {attempt}/{BS_LOGIN_MAX_RETRIES}), {delay:.0f}s 后重试...")
            time.sleep(delay)
        else:
            # 所有重试均失败，抛出不可恢复异常触发 Worker 熔断
            msg = f"Baostock 登录彻底失败 ({BS_LOGIN_MAX_RETRIES}次均失败): {last_error}"
            logger.error(f"❌ {msg}")
            print(f"❌ {msg}")
            raise BsConnectionDeadError(msg)

def bs_logout():
    """显式登出（可选，程序结束时自动调用）"""
    global _bs_logged_in
    if _bs_logged_in:
        try:
            bs.logout()
        except Exception:
            pass
        _bs_logged_in = False
        logger.info("Baostock 已登出")

# =========================================================================
# 错误处理装饰器（此名称保留以兼容现有代码，但行为已更改）
# =========================================================================
def retry_on_failure(max_retries=3, delay=1.0):
    """
    防御性编程装饰器：
    遇到错误重试指定次数，失败则返回 None，不再强制退出程序，保证多进程安全。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            # 兼容带有默认 max_retries 的旧代码调用，如 @retry_on_failure(max_retries=1)
            # 在外层已经绑定了，所以这里闭包内直接使用 max_retries
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (BsBlacklistedError, BsConnectionDeadError):
                    raise  # 黑名单封禁/登录超时不可重试，直接向上传播
                except Exception as e:
                    logger.warning(f"⚠️ {func.__name__} 失败 (尝试 {attempt}/{max_retries}): {e}")
                    if attempt < max_retries:
                        time.sleep(delay)
                    else:
                        logger.error(f"❌ {func.__name__} 达到最大重试次数，放弃执行。")
                        return None
        return wrapper
    return decorator

# =========================================================================
# 股票列表
# =========================================================================
@retry_on_failure()
def bs_fetch_stock_list() -> tuple[list[str], dict[str, str]]:
    """
    获取 A 股股票列表及中文名（从 Baostock）
    
    Returns:
        tuple: (code_list, name_dict)
            - code_list: ['sh.600000', 'sz.000001', ...]
            - name_dict: {'sh.600000': '浦发银行', ...}
    """
    with _bs_lock:
        _ensure_login()
        
        # 获取当日日期
        today = pd.Timestamp.now().strftime('%Y-%m-%d')
        
        # 🛡️ query_stock_basic 需拉取全市场数千只股票信息，数据量大
        # 在 VPN 环境下极易因 socket 无超时而卡死，加超时保护
        try:
            rs = _run_with_timeout(bs.query_stock_basic, timeout=BS_QUERY_TIMEOUT, desc="query_stock_basic")
        except TimeoutError as e:
            logger.error(f"❌ 获取股票列表超时: {e}")
            return [], {}
        
        if rs.error_code != '0':
            logger.error(f"获取股票列表失败: {rs.error_msg}")
            return [], {}
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 过滤规则（与原 fetcher.py 一致）
    # code 格式: sh.600000, sz.000001
    code_list = []
    name_dict = {}  # { "sh.600000": "浦发银行", ... }
    
    for _, row in df.iterrows():
        code = row['code']  # sh.600000 格式
        name = row.get('code_name', '')
        
        # 🟢 [V9.13 Fix] 排除指数(type=2)、基金等非股票证券
        # Baostock type: 1=股票, 2=指数, 3=其他
        # 之前缺失此过滤，导致 000xxx 开头的指数（如上证红利、上证B股）
        # 被误判为深市主板股票，每次"发现"~400只伪新股
        stock_type = row.get('type', '')
        if str(stock_type) != '1':
            continue
        
        # 提取纯代码
        pure_code = code.split('.')[-1]
        
        # 过滤：科创板(688)、创业板(300)、北交所(8/4/9开头)、ST
        if pure_code.startswith(('9', '8', '4', '688', '300')):
            continue
        # [User Request] Temporarily allow ST stocks
        # if 'ST' in name or '退' in name:
        #     continue
        
        # 仅保留主板：沪市(60开头)、深市主板(00开头)
        if pure_code.startswith('6') or pure_code.startswith('0'):
            code_list.append(code)
            if name:  # 同时收集中文名，零额外网络开销
                name_dict[code] = name
    
    logger.info(f"✅ [Baostock] 获取股票列表: {len(code_list)} 只")
    return code_list, name_dict

# =========================================================================
# 日线历史数据
# =========================================================================
@retry_on_failure(max_retries=1)  # 🟢 减少重试次数，避免高频请求
def bs_fetch_daily_history(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """
    获取日线历史数据
    
    Args:
        symbol: 股票代码（纯数字，如 '600000'）
        start_date: 开始日期 (YYYYMMDD 或 YYYY-MM-DD)
        end_date: 结束日期 (YYYYMMDD 或 YYYY-MM-DD)
    
    Returns:
        DataFrame with columns: trade_date, open, high, low, close, volume, symbol, adjust
    """
    with _bs_lock:
        _ensure_login()
        
        # 转换代码格式: 600000 -> sh.600000
        if not symbol.startswith(('sh.', 'sz.')):
            prefix = 'sh' if symbol.startswith('6') else 'sz'
            full_code = f"{prefix}.{symbol}"
        else:
            full_code = symbol
            symbol = symbol.split('.')[-1]
        
        # 统一日期格式为 YYYY-MM-DD
        if len(start_date) == 8:
            start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        if len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        
        # 查询日线数据（前复权）
        # 🛡️ 超时保护：query_history_k_data_plus 底层 socket 可能无限挂起
        try:
            rs = _run_with_timeout(
                bs.query_history_k_data_plus,
                args=(full_code, "date,open,high,low,close,volume"),
                kwargs=dict(start_date=start_date, end_date=end_date, frequency="d", adjustflag="2"),
                timeout=BS_QUERY_TIMEOUT,
                desc=f"query_daily({symbol})"
            )
        except TimeoutError as e:
            # 🛡️ 查询超时 = 守护线程卡在 C 扩展 socket recv() 中
            # 该线程持有 baostock 内部全局锁，同进程后续调用必死锁
            # 必须触发 Worker 熔断，不能再用这个进程
            msg = f"[Baostock] {symbol} 日线查询超时(连接已损坏): {e}"
            logger.error(msg)
            raise BsConnectionDeadError(msg)
        
        if rs.error_code != '0':
            # 🛡️ 黑名单检测：立即终止，避免继续浪费请求配额
            if '黑名单' in str(rs.error_msg):
                raise BsBlacklistedError(f"Baostock 黑名单封禁: {rs.error_msg}")
            logger.warning(f"[Baostock] {symbol} 查询失败: {rs.error_msg}")
            return None
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
    
    if not data_list:
        return None
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 类型转换
    df = df.rename(columns={'date': 'trade_date'})
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # 添加标准字段
    df['symbol'] = symbol
    df['adjust'] = 'qfq'
    
    # 过滤无效数据（成交量为0的停牌日）
    df = df[df['volume'] > 0]
    
    # 🟢 去重：Baostock 复权数据偶尔有重复日期
    if df['trade_date'].duplicated().any():
        logger.warning(f"[Baostock] {symbol}: 检测到重复日期，执行去重")
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
    
    if df.empty:
        return None
    
    logger.debug(f"[Baostock] {symbol}: 获取 {len(df)} 条日线数据")
    return df

# =========================================================================
# 周线历史数据
# =========================================================================
@retry_on_failure(max_retries=1)
def bs_fetch_weekly_history(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """
    获取周线历史数据
    
    Args:
        symbol: 股票代码（纯数字，如 '600000'）
        start_date: 开始日期 (YYYYMMDD 或 YYYY-MM-DD)
        end_date: 结束日期 (YYYYMMDD 或 YYYY-MM-DD)
    
    Returns:
        DataFrame with columns: trade_date, open, high, low, close, volume, symbol, adjust
    """
    with _bs_lock:
        _ensure_login()
        
        if not symbol.startswith(('sh.', 'sz.')):
            prefix = 'sh' if symbol.startswith('6') else 'sz'
            full_code = f"{prefix}.{symbol}"
        else:
            full_code = symbol
            symbol = symbol.split('.')[-1]
        
        if len(start_date) == 8:
            start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        if len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        
        # 🛡️ 超时保护：query_history_k_data_plus 底层 socket 可能无限挂起
        try:
            rs = _run_with_timeout(
                bs.query_history_k_data_plus,
                args=(full_code, "date,open,high,low,close,volume"),
                kwargs=dict(start_date=start_date, end_date=end_date, frequency="w", adjustflag="2"),
                timeout=BS_QUERY_TIMEOUT,
                desc=f"query_weekly({symbol})"
            )
        except TimeoutError as e:
            # 🛡️ 同日线：查询超时 = 守护线程死锁风险，必须触发 Worker 熔断
            msg = f"[Baostock] {symbol} 周线查询超时(连接已损坏): {e}"
            logger.error(msg)
            raise BsConnectionDeadError(msg)
        
        if rs.error_code != '0':
            if '黑名单' in str(rs.error_msg):
                raise BsBlacklistedError(f"Baostock 黑名单封禁: {rs.error_msg}")
            logger.warning(f"[Baostock] {symbol} 周线查询失败: {rs.error_msg}")
            return None
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
    
    if not data_list:
        return None
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    df = df.rename(columns={'date': 'trade_date'})
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['symbol'] = symbol
    df['adjust'] = 'qfq'
    df = df[df['volume'] > 0]
    
    if df['trade_date'].duplicated().any():
        logger.warning(f"[Baostock] {symbol}: 检测到周线重复日期，执行去重")
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
    
    if df.empty:
        return None
    
    logger.debug(f"[Baostock] {symbol}: 获取 {len(df)} 条周线数据")
    return df

# =========================================================================
# 分钟线历史数据
# =========================================================================
@retry_on_failure()
def bs_fetch_minute_history(symbol: str, start_date: str, end_date: str, 
                            freq: str = '5') -> Optional[pd.DataFrame]:
    """
    获取分钟线历史数据
    
    Args:
        symbol: 股票代码
        start_date/end_date: YYYYMMDD 或 YYYY-MM-DD
        freq: '5'=5分钟, '15'=15分钟, '30'=30分钟, '60'=60分钟
    
    Returns:
        DataFrame with OHLCV data
    """
    with _bs_lock:
        _ensure_login()
        
        # 转换代码格式
        if not symbol.startswith(('sh.', 'sz.')):
            prefix = 'sh' if symbol.startswith('6') else 'sz'
            full_code = f"{prefix}.{symbol}"
        else:
            full_code = symbol
            symbol = symbol.split('.')[-1]
        
        # 统一日期格式
        if len(start_date) == 8:
            start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
        if len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"
        
        rs = bs.query_history_k_data_plus(
            full_code,
            "date,time,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency=freq,
            adjustflag="2"
        )
        
        if rs.error_code != '0':
            logger.warning(f"[Baostock] {symbol} 分钟线查询失败: {rs.error_msg}")
            return None
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
    
    if not data_list:
        return None
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 合并 date + time 为 datetime
    df['datetime'] = df['date'] + ' ' + df['time'].str[:8]
    df = df.drop(columns=['date', 'time'])
    df = df.rename(columns={'datetime': 'trade_date'})
    
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df['symbol'] = symbol
    df = df[df['volume'] > 0]
    
    # 🟢 去重：分钟线也可能存在重复
    if df['trade_date'].duplicated().any():
        logger.warning(f"[Baostock] {symbol}: 分钟线检测到重复日期，执行去重")
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
    
    logger.debug(f"[Baostock] {symbol}: 获取 {len(df)} 条 {freq}分钟线")
    return df


if __name__ == "__main__":
    # 简单测试
    
    print("测试 Baostock 适配器...")
    
    # 测试登录
    _ensure_login()
    
    # 测试股票列表
    codes, names = bs_fetch_stock_list()
    print(f"股票列表: {len(codes)} 只, 前5只: {codes[:5]}")
    print(f"中文名样本: {dict(list(names.items())[:3])}")
    
    # 测试日线数据
    df = bs_fetch_daily_history('600000', '2024-01-01', '2024-01-15')
    if df is not None:
        print(f"日线数据:\n{df.head()}")
    
    bs_logout()
    print("✅ Baostock 测试完成")
