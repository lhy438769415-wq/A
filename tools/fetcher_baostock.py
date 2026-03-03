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

logger = logging.getLogger(__name__)

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
_bs_circuit_breaker = False # 🔴 全局熔断标志

def _ensure_login(force=False):
    """
    确保 Baostock 已登录（惰性连接）
    
    Args:
        force: 是否强制重连
    """
    global _bs_logged_in, _bs_circuit_breaker
    
    # ⚡ 熔断检查
    if _bs_circuit_breaker:
        raise RuntimeError("Baostock Circuit Breaker Active")
    
    if force and _bs_logged_in:
        logger.info("强制重置 Baostock 连接...")
        bs_logout()
    
    if not _bs_logged_in:
        lg = bs.login()
        if lg.error_code != '0':
            msg = f"Baostock 登录失败: {lg.error_msg}"
            logger.error(msg)
            print(f"❌ {msg}")
            
            # 🔴 触发熔断
            _bs_circuit_breaker = True
            sys.exit(1)
            
        _bs_logged_in = True
        logger.info("✅ Baostock 登录成功")

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
def retry_on_failure(max_retries=None, delay=None):
    """
    防御性编程装饰器：
    ❌ 原始逻辑：重试
    ✅ 当前逻辑：捕获异常 -> 记录日志 -> 提示用户 -> 退出程序
    (应用户要求，遇到网络/解码错误不重试，直接终止以避免脏数据或死循环)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            global _bs_circuit_breaker
            
            # ⚡ 快速失败：如果熔断器已触发，直接抛出或返回
            if _bs_circuit_breaker:
                # logger.debug("Circuit breaker active, skipping request.")
                return None
                
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 严重错误处理逻辑
                _bs_circuit_breaker = True # 🔴 立即触发熔断，通知所有线程停止
                
                error_msg = f"❌ {func.__name__} 执行遇到严重错误: {e}"
                logger.critical(error_msg)
                print(f"\n{error_msg}")
                print("⚠️  检测到数据源或网络异常，根据安全策略，程序将立即停止。")
                print("⚠️  请检查网络连接 (VPN) 或稍后再试。")
                
                # 尝试安全登出
                try:
                    bs_logout()
                except:
                    pass
                
                # 强制退出程序
                import os
                os._exit(1) # 🔴 使用 os._exit(1) 强制杀掉整个进程，而不仅仅是线程
        return wrapper
    return decorator

# =========================================================================
# 股票列表
# =========================================================================
@retry_on_failure()
def bs_fetch_stock_list() -> List[str]:
    """
    获取 A 股股票列表（从 Baostock）
    返回格式: ['sh.600000', 'sz.000001', ...]
    """
    with _bs_lock:
        _ensure_login()
        
        # 获取当日日期
        today = pd.Timestamp.now().strftime('%Y-%m-%d')
        
        # query_stock_basic 返回所有股票基本信息
        rs = bs.query_stock_basic()
        
        if rs.error_code != '0':
            logger.error(f"获取股票列表失败: {rs.error_msg}")
            return []
        
        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    
    # 过滤规则（与原 fetcher.py 一致）
    # code 格式: sh.600000, sz.000001
    code_list = []
    
    for _, row in df.iterrows():
        code = row['code']  # sh.600000 格式
        name = row.get('code_name', '')
        
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
    
    logger.info(f"✅ [Baostock] 获取股票列表: {len(code_list)} 只")
    return code_list

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
        rs = bs.query_history_k_data_plus(
            full_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2"  # 2=前复权
        )
        
        if rs.error_code != '0':
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
        
        rs = bs.query_history_k_data_plus(
            full_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="w",
            adjustflag="2"  # 2=前复权
        )
        
        if rs.error_code != '0':
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
    logging.basicConfig(level=logging.DEBUG)
    
    print("测试 Baostock 适配器...")
    
    # 测试登录
    _ensure_login()
    
    # 测试股票列表
    codes = bs_fetch_stock_list()
    print(f"股票列表: {len(codes)} 只, 前5只: {codes[:5]}")
    
    # 测试日线数据
    df = bs_fetch_daily_history('600000', '2024-01-01', '2024-01-15')
    if df is not None:
        print(f"日线数据:\n{df.head()}")
    
    bs_logout()
    print("✅ Baostock 测试完成")
