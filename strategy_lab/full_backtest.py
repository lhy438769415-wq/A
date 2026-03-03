# -*- coding: utf-8 -*-
"""
策略实验室 - 全量回测与胜率统计
🔒 OFFLINE ONLY: 仅从本地数据库读取，禁止联网！
"""
import sys
import os
import pandas as pd
import numpy as np
import logging
from datetime import datetime

# Ensure root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import data_manager
from core.database import get_db_connection
from strategy_lab.pattern_bullish_pinbar import BullishPinbarStrategy

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==========================================
# 回测参数
# ==========================================
HOLD_DAYS = [1, 3, 5, 10]  # 统计信号后 N 天的收益
MIN_DATA_POINTS = 50      # 股票至少需要 50 个交易日数据

def get_all_codes_from_db():
    """从本地数据库获取所有股票代码 (离线)"""
    import sqlite3
    DB_PATH = "data/ashare.db"
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("SELECT DISTINCT symbol FROM daily_bars")
        codes = [row[0] for row in cursor.fetchall()]
        conn.close()
        return codes
    except Exception as e:
        logger.error(f"Error reading DB: {e}")
        return []

def calculate_forward_returns(df, signal_idx, hold_days):
    """
    计算信号后 N 天的收益率
    Returns: dict of {hold_days: return_pct}
    """
    results = {}
    signal_pos = df.index.get_loc(signal_idx)
    signal_close = df.loc[signal_idx, 'close']
    
    for days in hold_days:
        future_pos = signal_pos + days
        if future_pos < len(df):
            future_close = df.iloc[future_pos]['close']
            ret = (future_close - signal_close) / signal_close * 100
            results[days] = ret
        else:
            results[days] = None  # Not enough data
    return results

def run_full_backtest():
    """
    全量回测主函数
    """
    logger.info("="*60)
    logger.info("🧪 Strategy Lab: Full Database Backtest")
    logger.info("🔒 OFFLINE MODE - No Network Requests")
    logger.info("="*60)
    
    # 1. 获取所有股票代码
    all_codes = get_all_codes_from_db()
    logger.info(f"📊 Found {len(all_codes)} stocks in database")
    
    if not all_codes:
        logger.error("Database is empty!")
        return
    
    strategy = BullishPinbarStrategy()
    
    # 统计结构: {match_type: {hold_days: [returns]}}
    stats = {}
    signal_examples = {}  # 保存每种类型的示例
    
    processed = 0
    skipped = 0
    
    for code in all_codes:
        # 🔒 OFFLINE ONLY
        df = data_manager.get_stock_data_offline(code, limit=500)  # 更长历史
        
        if df is None or len(df) < MIN_DATA_POINTS:
            skipped += 1
            continue
        
        # 确保日期排序 (升序)
        if 'date' in df.columns:
            df = df.sort_values('date').reset_index(drop=True)
        elif 'trade_date' in df.columns:
            df = df.sort_values('trade_date').reset_index(drop=True)
        
        # 运行形态检测
        df_res = strategy.detect(df)
        
        # 获取信号
        signals = df_res[df_res['signal_pinbar']]
        
        if signals.empty:
            processed += 1
            continue
        
        # 计算每个信号的后续收益
        for idx, row in signals.iterrows():
            match_type = row.get('match_type', 'Unknown')
            
            if match_type not in stats:
                stats[match_type] = {d: [] for d in HOLD_DAYS}
                signal_examples[match_type] = []
            
            # 计算前向收益
            returns = calculate_forward_returns(df_res, idx, HOLD_DAYS)
            
            for days, ret in returns.items():
                if ret is not None:
                    stats[match_type][days].append(ret)
            
            # 保存示例 (最多 5 个)
            if len(signal_examples[match_type]) < 5:
                date_str = row.get('date', row.get('trade_date', 'N/A'))
                signal_examples[match_type].append({
                    'code': code,
                    'date': date_str,
                    'close': row['close'],
                    'wick_pct': row['lower_wick_pct'] * 100
                })
        
        processed += 1
        
        # 进度显示
        if processed % 200 == 0:
            logger.info(f"   Progress: {processed}/{len(all_codes)} stocks processed...")
    
    # ==========================================
    # 输出统计结果
    # ==========================================
    logger.info("\n" + "="*60)
    logger.info("📈 BACKTEST RESULTS")
    logger.info("="*60)
    logger.info(f"Processed: {processed} stocks | Skipped: {skipped} (insufficient data)")
    
    for match_type, hold_stats in stats.items():
        logger.info(f"\n🎯 [{match_type}]")
        
        total_signals = len(hold_stats[HOLD_DAYS[0]]) if hold_stats[HOLD_DAYS[0]] else 0
        logger.info(f"   Total Signals: {total_signals}")
        
        for days in HOLD_DAYS:
            returns = hold_stats[days]
            if not returns:
                continue
            
            arr = np.array(returns)
            win_rate = (arr > 0).sum() / len(arr) * 100
            avg_ret = arr.mean()
            median_ret = np.median(arr)
            
            logger.info(f"   {days}D: WinRate={win_rate:.1f}% | Avg={avg_ret:+.2f}% | Median={median_ret:+.2f}%")
        
        # 显示示例
        if match_type in signal_examples:
            logger.info(f"   Examples:")
            for ex in signal_examples[match_type][:3]:
                logger.info(f"      - {ex['code']} @ {ex['date']}: Close={ex['close']:.2f}, Wick={ex['wick_pct']:.1f}%")
    
    logger.info("\n" + "="*60)
    logger.info("✅ Backtest Complete")
    logger.info("="*60)

if __name__ == "__main__":
    run_full_backtest()
