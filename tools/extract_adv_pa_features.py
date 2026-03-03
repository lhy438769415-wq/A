import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
from config import settings

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def advanced_evaluate_and_extract(df: pd.DataFrame, signal_idx: int) -> dict:
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_struct_gap']
        sl_price = sig_row['sl_struct_gap']
        tp_price = sig_row['tp_struct_gap']
        
        # --- Advanced PB Feature Extraction ---
        gap_top = sig_row['struct_gap_top_exact']
        gap_floor = sig_row['struct_gap_floor_exact']
        
        # 找到这波 pull back 是在多久前回撤开始的
        # 找出 _breakout_low 以来的这段窗口
        pb_start_date = df.iloc[signal_idx]['struct_gap_test_date'] # 近似的底
        
        # 给定我们从突破算起，我们直接抓信号前方至多 40 根K线作为上下文
        lookback = 40
        start_idx = max(0, signal_idx - lookback)
        context_df = df.iloc[start_idx:signal_idx]
        
        # 在这 40 根线中，找寻最高点，作为回撤起跌点
        high_idx_local = context_df['high'].idxmax()
        
        # 真实的回撤期：起跌最高点 -> 信号前夕
        pb_df = df.loc[high_idx_local:df.index[signal_idx-1]]
        pb_len = len(pb_df)
        
        pb_features = {
            'pb_length': pb_len,
            'bear_bar_pct': 0.0,
            'max_consec_bear': 0,
            'overlap_pct': 0.0,
            # 'below_ema20': 0,
        }
        
        if pb_len >= 2:
            # 1. 空头K线占比 (收盘 < 开盘)
            is_bear = (pb_df['close'] < pb_df['open']).astype(int)
            pb_features['bear_bar_pct'] = round(is_bear.mean(), 2)
            
            # 2. 最大连续阴线数 (Max Consecutive Bear bars)
            max_consec = 0
            curr_consec = 0
            for b in is_bear:
                if b == 1:
                    curr_consec += 1
                    max_consec = max(max_consec, curr_consec)
                else:
                    curr_consec = 0
            pb_features['max_consec_bear'] = max_consec
            
            # 3. K线重叠度 (Overlap Percentage)
            # 跌势中的重叠度：后一根的 High > 前一根的 Close。重叠度越高，空头下杀越不果断
            close_vals = pb_df['close'].values
            high_vals = pb_df['high'].values
            if pb_len > 2:
                overlap_mask = high_vals[1:] > close_vals[:-1]
                pb_features['overlap_pct'] = round(np.mean(overlap_mask), 2)
                
            # 4. EMA20 交互 (如果需要)
            if 'ema20' in pb_df.columns:
                below_ema = (pb_df['close'] < pb_df['ema20']).astype(int)
                pb_features['pct_below_ema20'] = round(below_ema.mean(), 2)
            else:
                pb_features['pct_below_ema20'] = 0.0
                
        # 补上基础的 sig bar quality 方便联合对比
        bar_range = sig_row['high'] - sig_row['low']
        sig_bar_quality = (sig_row['close'] - sig_row['low']) / bar_range if bar_range > 0 else 0
        pb_features['sig_bar_quality'] = round(sig_bar_quality, 2)
        
        # --- Trade Simulation ---
        post_signal = df.iloc[signal_idx + 1:]
        
        if post_signal.empty:
            return {'status': 'PENDING', 'reason': '数据结尾，尚未触发入场', 'features': pb_features}
            
        trade_status = 'WAITING_TRIGGER'
        for i, row in post_signal.iterrows():
            if trade_status == 'WAITING_TRIGGER':
                if row['low'] < sl_price:
                    return {'status': 'INVALIDATED', 'reason': '未入场即跌穿防守线', 'features': pb_features}
                if row['high'] >= entry_price:
                    trade_status = 'IN_TRADE'
                    if row['low'] <= sl_price:
                        return {'status': 'LOSS', 'result': 0, 'features': pb_features}
                    if row['high'] >= tp_price:
                        return {'status': 'WIN', 'result': 1, 'features': pb_features}
            elif trade_status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    return {'status': 'LOSS', 'result': 0, 'features': pb_features}
                if row['high'] >= tp_price:
                    return {'status': 'WIN', 'result': 1, 'features': pb_features}
                    
        return {'status': 'HOLDING', 'reason': '持仓中至今未达目标', 'features': pb_features}
        
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e), 'features': {}}

def process_stock_adv(code: str, bars_limit=1500) -> list:
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
            trade_res = advanced_evaluate_and_extract(df, idx)
            if trade_res['status'] in ['WIN', 'LOSS']:
                record = {'code': code, 'status': trade_res['status'], 'result': trade_res['result']}
                record.update(trade_res['features'])
                results.append(record)
        return results
    except Exception as e:
        return []

def main():
    logger.info("开始提取 Advanced Pullback 特征与执行回测...")
    all_codes = get_stock_list()
    if not all_codes: return
    limit = 800
    all_codes = all_codes[:limit]
    
    all_records = []
    
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = {executor.submit(process_stock_adv, code, 1500): code for code in all_codes}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                print(f"进度: {completed} / {len(all_codes)}", end='\r')
            records = future.result()
            if records: all_records.extend(records)
                
    if not all_records: return
        
    df_res = pd.DataFrame(all_records)
    output_path = os.path.join(project_root, "data", "adv_pullback_features.csv")
    df_res.to_csv(output_path, index=False)
    logger.info(f"\nAdvanced 回撤特征矩阵保存至 {output_path}")
    print(df_res.head())

if __name__ == "__main__":
    main()
