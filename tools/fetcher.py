# -*- coding: utf-8 -*-
"""
数据获取层 - Baostock Only (V8.13)

功能:
1. 单一数据源：只使用 Baostock
2. 简化架构：无降级、无熔断
3. 保持对外接口不变，确保向后兼容

变更历史:
- V8.13: 简化为 Baostock Only，移除 Tushare/AkShare
"""

import pandas as pd
from datetime import datetime
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

# =========================================================================
# 导入 Baostock 适配器
# =========================================================================
try:
    from tools.fetcher_baostock import (
        bs_fetch_stock_list,
        bs_fetch_daily_history,
        bs_fetch_weekly_history,
        bs_logout
    )
    BAOSTOCK_AVAILABLE = True
except ImportError as e:
    logger.error(f"❌ Baostock 不可用: {e}")
    BAOSTOCK_AVAILABLE = False

# =========================================================================
# 🚀 数据获取接口 (Baostock Only)
# =========================================================================
def fetch_stock_list_active() -> List[str]:
    """
    获取股票列表 (Baostock)
    
    Returns:
        股票代码列表，格式 ['sh.600000', 'sz.000001', ...]
    """
    if not BAOSTOCK_AVAILABLE:
        logger.error("❌ Baostock 不可用，无法获取股票列表")
        return []
    
    try:
        result = bs_fetch_stock_list()
        if result:
            logger.info(f"[Baostock] 获取股票列表: {len(result)} 只")
            return result
    except Exception as e:
        logger.error(f"❌ 获取股票列表失败: {e}")
    
    return []

def fetch_daily_history_active(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """
    获取日线历史数据 (Baostock)
    
    Args:
        symbol: 股票代码（纯数字，如 '600000'）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
    
    Returns:
        DataFrame with OHLCV data, or None if failed
    """
    if not BAOSTOCK_AVAILABLE:
        logger.error("❌ Baostock 不可用")
        return None
    
    try:
        result = bs_fetch_daily_history(symbol, start_date, end_date)
        if result is not None and not result.empty:
            logger.debug(f"[Baostock] {symbol}: {len(result)} 条日线")
            return result
    except Exception as e:
        logger.warning(f"[Baostock] {symbol} 获取失败: {e}")
    
    return None

def fetch_weekly_history_active(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """
    获取周线历史数据 (Baostock)
    
    Args:
        symbol: 股票代码（纯数字，如 '600000'）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)
    
    Returns:
        DataFrame with OHLCV data, or None if failed
    """
    if not BAOSTOCK_AVAILABLE:
        logger.error("❌ Baostock 不可用")
        return None
    
    try:
        result = bs_fetch_weekly_history(symbol, start_date, end_date)
        if result is not None and not result.empty:
            logger.debug(f"[Baostock] {symbol}: {len(result)} 条周线")
            return result
    except Exception as e:
        logger.warning(f"[Baostock] {symbol} 周线获取失败: {e}")
    
    return None

def fetch_daily_snapshot(symbol: str) -> Optional[pd.DataFrame]:
    """
    🚫 已禁用：实时快照功能
    
    保留函数签名以兼容旧代码。
    """
    logger.warning("⚠️ fetch_daily_snapshot() 已禁用（不埋伏尾盘）")
    return pd.DataFrame()

# =========================================================================
# ⚠️ 兼容性别名（保持向后兼容）
# =========================================================================
fetch_online_stock_list = fetch_stock_list_active
fetch_online_stock_list_safe = fetch_stock_list_active
fetch_daily_history = fetch_daily_history_active
fetch_daily_history_safe = fetch_daily_history_active


if __name__ == "__main__":
    # 快速测试
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    
    print("=" * 60)
    print("测试 Baostock Only 数据层")
    print("=" * 60)
    
    # 测试股票列表
    print("\n1. 测试股票列表获取...")
    codes = fetch_stock_list_active()
    print(f"   获取 {len(codes)} 只股票")
    if codes:
        print(f"   前3只: {codes[:3]}")
    
    # 测试日线数据
    print("\n2. 测试日线数据获取...")
    df = fetch_daily_history_active('600000', '20240101', '20240115')
    if df is not None:
        print(f"   获取 {len(df)} 条日线数据")
        print(df.head(3).to_string())
    else:
        print("   ❌ 日线数据获取失败")
    
    print("\n✅ Baostock Only 测试完成")
