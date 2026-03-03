import pandas as pd
import numpy as np
import os
import json
import logging
import sys
from datetime import datetime

# 确保核心路径在 python path 中
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import get_stock_list, get_stock_data
from core.calculator import add_indicators

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def scan_gold_set(limit_stocks=300):
    """
    [Phase 1] 黄金案例挖掘原型机 (Gold Set Mining Prototype)
    
    目标：寻找符合 Al Brooks 修正版逻辑图的 MTR 案例。
    逻辑：
    1. Down Trend -> 2. Sell Climax (Low) -> 3. MLH (Prior High) -> 4. Breakout -> 5. HL (Higher Low) -> 6. Signal
    """
    stock_list = get_stock_list()
    if not stock_list:
        logger.error("❌ 无法获取股票列表，请检查数据库")
        return
    
    # 为了效率，优先扫描一部分股票
    target_stocks = stock_list[:limit_stocks]
    logger.info(f"🚀 开始扫描 {len(target_stocks)} 只股票寻找 MTR 黄金案例...")
    
    gold_set = []
    
    for symbol in target_stocks:
        try:
            # 获取 2 年日线 (约 500 根)
            df = get_stock_data(symbol, limit=500)
            if df is None or len(df) < 150:
                continue
                
            # 计算基础指标
            df = add_indicators(df)
            
            # 使用更宽的窗口寻找波段高点作为 MLH 候选
            df['is_sw_h'] = (df['high'] == df['high'].rolling(window=21, center=True).max())
            
            # 向量化检索潜力点 (滑动窗口)
            # 我们寻找最近 6 个月内发生的 MTR
            for i in range(150, len(df) - 5):
                # 关注窗口：过去 100 根到当前
                win = df.iloc[i-100:i+1].copy()
                
                # 1. 寻找期间极低点 (Sell Climax Candidates)
                climax_idx = win['low'].idxmin()
                climax_val = win['low'].min()
                climax_pos = win.index.get_loc(climax_idx)
                
                # climax 必须在窗口前半段，留出后半段跑 Break/Test
                if climax_pos < 20 or climax_pos > 70:
                    continue
                
                # 2. 溯源 Strategic MLH (创出极值前的波段高点)
                pre_climax = win.iloc[:climax_pos]
                mlh_candidates = pre_climax[pre_climax['is_sw_h']]
                if mlh_candidates.empty:
                    # 备选：如果 21 窗口没找到，用 11 窗口
                    mlh_candidates = pre_climax[pre_climax['high'] == pre_climax['high'].rolling(window=11, center=True).max()]
                
                if mlh_candidates.empty:
                    continue
                
                mlh_row = mlh_candidates.iloc[-1]
                mlh_val = mlh_row['high']
                
                # 3. 验证 Breakout (First Leg)
                # 价格必须在 Climax 后收于 MLH 之上
                post_climax = win.iloc[climax_pos:]
                break_cases = post_climax[post_climax['close'] > mlh_val]
                
                if break_cases.empty:
                    continue
                    
                break_idx = break_cases.index[0]
                break_pos = win.index.get_loc(break_idx)
                
                # 4. 验证 Higher Low (Test)
                # 突破点后的回调
                post_break = win.iloc[break_pos:]
                if len(post_break) < 3:
                    continue
                    
                pb_low = post_break['low'].min()
                
                # 物理硬约束：HL > Low (至少留出 0.5 ATR 的空间证明其强势)
                atr = win.loc[climax_idx, 'atr']
                if pb_low <= climax_val + 0.3 * atr:
                    continue
                
                # 同时要求回调不能涨过头 (即不能是持续拉升，必须有回调动作)
                # 回调幅度 = (Break_High - PB_Low) / (Break_High - Climax_Low) 应在 30%-70% 之间
                break_high = win.iloc[break_pos:]['high'].max()
                total_range = break_high - climax_val
                if total_range <= 0: continue
                
                retrenchment = (break_high - pb_low) / total_range
                if retrenchment < 0.25 or retrenchment > 0.85:
                    continue
                
                # 5. Signal Bar 识别 (当前棒)
                curr_bar = win.iloc[-1]
                prev_bar = win.iloc[-2]
                
                # 信号棒：阳线，突破前高，且收盘接近最高
                if (curr_bar['close'] > curr_bar['open'] and 
                    curr_bar['high'] > prev_bar['high'] and 
                    curr_bar['close_loc'] > 0.6):
                    
                    case = {
                        "symbol": symbol,
                        "date": str(curr_bar['date']),
                        "climax_date": str(win.loc[climax_idx, 'date']),
                        "mlh_date": str(mlh_row['date']),
                        "mlh_val": round(float(mlh_val), 2),
                        "break_date": str(win.loc[break_idx, 'date']),
                        "low_val": round(float(climax_val), 2),
                        "hl_val": round(float(pb_low), 2),
                        "retrenchment": round(float(retrenchment), 2)
                    }
                    gold_set.append(case)
                    logger.info(f"✨ 发现理想 MTR 案例: {symbol} 在 {case['date']}")
                    
                    # 找到一个后，快速跳过该股的其他窗口，防止冗余
                    break
                    
            if len(gold_set) >= 15:
                break

        except Exception as e:
            logger.error(f"⚠️ 处理 {symbol} 时报错: {e}")
            continue

    # 保存结果
    output_path = os.path.join("data", "gold_standards.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(gold_set, f, indent=4, ensure_ascii=False)
    
    logger.info(f"✅ 扫描完成。共收集 {len(gold_set)} 个黄金案例。保存至: {output_path}")

if __name__ == "__main__":
    scan_gold_set()
