import pandas as pd
import numpy as np
import os
import json
import logging
import sys
from datetime import datetime

# 确保核心路径在 python path 中
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import get_stock_list, get_stock_data, get_stock_data_weekly
from core.calculator import add_indicators
from core.patterns import PatternRegistry

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_gold_standards(file_path: str = "data/gold_standards.json") -> dict:
    """加载黄金标准文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load gold standards: {e}")
        return {"patterns": []}

def scan_patterns(limit_stocks=300, validate_against_gold=False):
    """
    基于形态库的通用形态扫描器 
    (Dynamic Pattern Scanner)
    """
    stock_list = get_stock_list()
    if not stock_list:
        logger.error("❌ 无法获取股票列表，请检查数据库")
        return
        
    patterns = PatternRegistry.get_all_patterns()
    if not patterns:
        logger.error("❌ 未发现任何注册的形态，退出扫描。")
        return
        
    logger.info(f"🧩 加载了 {len(patterns)} 个高胜率形态: {list(patterns.keys())}")
    
    # 获取需要验证的黄金案例
    gold_data = load_gold_standards()
    
    # 为了效率，优先扫描一部分股票，或者指定扫描黄金案例涉及的股票
    if validate_against_gold:
        gold_symbols = list(set([p['symbol'] for p in gold_data['patterns']]))
        target_stocks = gold_symbols
        logger.info(f"🧪 验证模式，仅扫描 Gold Standards 中的股票: {target_stocks}")
    else:
        target_stocks = stock_list[:limit_stocks]
        logger.info(f"🚀 开始全市场扫描 {len(target_stocks)} 只股票寻找设定形态...")

    found_matches = []

    for symbol in target_stocks:
        try:
            # 获取 2 年日线
            df_daily = get_stock_data(symbol, limit=500)
            if df_daily is None or len(df_daily) < 150:
                continue
                
            # 默认获取周线（因为有些高级形态依赖周线）
            df_weekly = get_stock_data_weekly(symbol, limit=150)
            
            # 计算基础指标准备环境
            df_daily = add_indicators(df_daily)
            
            # 遍历所有形态注册表
            for p_name, pattern_instance in patterns.items():
                logger.debug(f"🔍 检查 {symbol} 形态: {p_name}")
                
                # 若需要，可以从 gold_standards 重写参数，这里简单采用默认参数或从配置加载
                signals = pattern_instance.detect(df_daily, df_weekly)
                
                if signals.any():
                    # 提取信号发生的日期
                    date_col = 'trade_date' if 'trade_date' in df_daily.columns else 'date'
                    signal_dates = df_daily[signals][date_col].tolist()
                    for sd in signal_dates:
                         match_info = {
                             "pattern": p_name,
                             "symbol": symbol,
                             "date": str(sd),
                         }
                         found_matches.append(match_info)
                         logger.info(f"✨ 命中高胜率形态! [{p_name}] -> {symbol} @ {sd}")
                
        except Exception as e:
             logger.error(f"⚠️ 处理 {symbol} 时报错: {e}")
             continue
             
    if found_matches:
        logger.info(f"✅ 扫描完成！共发现 {len(found_matches)} 次形态触发。")
    else:
        logger.info(f"⏳ 扫描完成，未发现匹配形态。")
        
    return found_matches

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true", help="Validate against gold standards JSON")
    args = parser.parse_args()
    
    scan_patterns(validate_against_gold=args.validate)
