import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

# Ensure project root is in path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
from config import settings

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def evaluate_trade_and_extract(df: pd.DataFrame, signal_idx: int) -> dict:
    """
    模拟交易生命周期，并提取形态发生时的特征矩阵
    """
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_struct_gap']
        sl_price = sig_row['sl_struct_gap']
        tp_price = sig_row['tp_struct_gap']
        
        # --- Feature Extraction ---
        # 1. 缺口绝对宽度 / ATR (Gap_Size_ATR)
        gap_top = sig_row['struct_gap_top_exact']
        gap_floor = sig_row['struct_gap_floor_exact']
        atr_val = sig_row.get('atr', 1e-5) # fallback if atr is missing
        gap_size_atr = (gap_top - gap_floor) / atr_val if pd.notna(gap_top) and pd.notna(gap_floor) and atr_val > 0 else np.nan
        
        # 2. 回撤深度 (Retracement_Depth)
        # (有效突破最高点 - 回调探底点) / (有效突破最高点 - Gap Floor)
        # 用信号前一天的最高点近似（或者简单用： Gap Size / 信号 K 线的价差）
        # 简单算法：当前信号点的高点距离 Floor 的比例
        retracement_depth = (sig_row['high'] - gap_floor) / (gap_top - gap_floor + 1e-5)
        
        # 3. 信号 K 线质量 (Signal_Bar_Quality)
        # 收盘位百分比： (Close - Low) / (High - Low)
        bar_range = sig_row['high'] - sig_row['low']
        sig_bar_quality = (sig_row['close'] - sig_row['low']) / bar_range if bar_range > 0 else 0
        
        features = {
            'gap_size_atr': round(gap_size_atr, 2),
            'retracement_depth': round(retracement_depth, 2),
            'sig_bar_quality': round(sig_bar_quality, 2),
            'tp_price': tp_price,
            'sl_price': sl_price
        }
        
        # --- Trade Simulation ---
        post_signal = df.iloc[signal_idx + 1:]
        
        if post_signal.empty:
            return {'status': 'PENDING', 'reason': '数据结尾，尚未触发入场', 'features': features}
            
        trade_status = 'WAITING_TRIGGER'
        entry_date = None
        exit_date = None
        actual_entry = None
        
        for i, row in post_signal.iterrows():
            if trade_status == 'WAITING_TRIGGER':
                if row['low'] < sl_price:
                    return {'status': 'INVALIDATED', 'reason': '未入场即跌穿防守线', 'features': features}
                
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row['date'] if 'date' in row else i
                    trade_status = 'IN_TRADE'
                    
                    if row['low'] <= sl_price:
                        return {
                            'status': 'LOSS', 
                            'result': 0, # Hit SL before TP
                            'features': features
                        }
                    
                    if row['high'] >= tp_price:
                        return {
                            'status': 'WIN',
                            'result': 1, # Hit TP
                            'features': features
                        }
            
            elif trade_status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    return {
                        'status': 'LOSS',
                        'result': 0,
                        'features': features
                    }
                if row['high'] >= tp_price:
                    return {
                        'status': 'WIN',
                        'result': 1,
                        'features': features
                    }
                    
        if trade_status == 'IN_TRADE':
             return {'status': 'HOLDING', 'reason': '持仓中至今未达目标', 'features': features}
             
        return {'status': 'PENDING', 'reason': '挂单中至今未触发', 'features': features}
        
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e), 'features': {}}

def process_stock(code: str, bars_limit=1500) -> list:
    try:
        df = get_stock_data(code, limit=bars_limit)
        if df is None or len(df) < 100:
            return []
            
        df = add_indicators(df)
        strategy = StructuralGapStrategy()
        df = strategy.calculate_signals(df)
        
        signal_indices = [i for i, val in enumerate(df['signal_struct_gap_confirm']) if val]
        
        results = []
        for idx in signal_indices:
            trade_res = evaluate_trade_and_extract(df, idx)
            
            if trade_res['status'] in ['WIN', 'LOSS']:
                sig_date = df.iloc[idx]['date'] if 'date' in df.columns else df.index[idx]
                if hasattr(sig_date, 'strftime'):
                    sig_date = sig_date.strftime('%Y-%m-%d')
                else:
                    sig_date = str(sig_date)
                    
                record = {
                    'code': code,
                    'signal_date': sig_date,
                    'status': trade_res['status'],
                    'result': trade_res['result']
                }
                record.update(trade_res['features'])
                results.append(record)
                
        return results
        
    except Exception as e:
        logger.debug(f"Error testing {code}: {e}")
        return []

def main():
    logger.info("开始提取 Structural Gap 特征与执行回测...")
    all_codes = get_stock_list()
    if not all_codes:
        logger.error("无法获取股票列表")
        return
        
    # 为节约算力，先取前 800 只股票作为数据集原型
    limit = 800
    all_codes = all_codes[:limit]
    
    all_records = []
    
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = {executor.submit(process_stock, code, 1500): code for code in all_codes}
        
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                print(f"进度: {completed} / {len(all_codes)}", end='\r')
                
            records = future.result()
            if records:
                all_records.extend(records)
                
    print(f"\n扫描完毕。共收集有效闭环样本: {len(all_records)} 个")
    
    if not all_records:
        return
        
    df_res = pd.DataFrame(all_records)
    output_path = os.path.join(project_root, "data", "struct_gap_features.csv")
    df_res.to_csv(output_path, index=False)
    logger.info(f"特征矩阵已保存至 {output_path}")

    # ===== Baseline Stats =====
    total = len(df_res)
    win_rate = df_res['result'].mean() * 100
    print(f"\n🎯 整体胜率基准: {win_rate:.2f}% (Win: {df_res['result'].sum()}, Total: {total})")

if __name__ == "__main__":
    main()
