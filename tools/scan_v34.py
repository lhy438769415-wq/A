import sys
import os
import pandas as pd
import numpy as np
import io
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 强制设置 UTF-8 编码以支持 Windows 终端和日志重定向
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Setup path
sys.path.append(os.getcwd())

from core.strategies.mtr_strategy import MTRStrategy
import core.data_provider as data_provider
from core.calculator import add_indicators

def scan_candidate(code):
    try:
        # Get enough data for Swing calculation
        df = data_provider.get_stock_data(code, limit=250)
        if df is None or len(df) < 100:
            return None
        
        # Add basic indicators required by strategy
        df = add_indicators(df)
        strat = MTRStrategy()
        df = strat.calculate_signals(df)
        
        # We check the last 5 days for any MTR activity to catch recent setups
        recent_df = df.tail(5)
        
        # Find the most recent signal/potential row
        mask = recent_df['mtr_stage'] != 'NONE'
        if not mask.any():
            return None
            
        target_row = recent_df[mask].iloc[-1]
        stage = target_row['mtr_stage']
        
        # Fetch stock name
        try:
            name = data_provider.get_stock_name(code)
        except:
            name = code

        return {
            'code': code,
            'name': name,
            'date': str(target_row['date']),
            'stage': stage,
            'close': target_row['close'],
            'score': target_row.get('mtr_score', 0),
            'sl': target_row.get('sl_price', 0),
            'tp1': target_row.get('tp1_target', 0)
        }
            
    except Exception as e:
        return None

def run_v34_scan(limit=None):
    all_codes = data_provider.get_stock_list()
    if not all_codes:
        print("❌ No offline data found in database.")
        return
    
    if limit:
        all_codes = all_codes[:limit]
        
    print(f"--- [V34.4] Scanning {len(all_codes)} stocks from local database...")
    
    results = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(scan_candidate, code): code for code in all_codes}
        completed = 0
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
            
            completed += 1
            if completed % 100 == 0:
                print(f"Progress: {completed}/{len(all_codes)}...")

    # Sort and Print Results
    if not results:
        print("INFO: No MTR patterns (V34.4) detected in recent data.")
        return

    # Separate Confirmed and Potential
    confirmed = [r for r in results if r['stage'] == 'SETUP_READY']
    potential = [r for r in results if r['stage'] == 'SETUP_FORMING']

    print("\n" + "="*80)
    print(f"MTR V34.4 RADAR REPORT ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print("="*80)

    if confirmed:
        print(f"\n[SETUP_READY] - (Count: {len(confirmed)})")
        print("-" * 80)
        print(f"{'Code':<10} | {'Name':<12} | {'Stage':<18} | {'Score':<6} | {'Entry':<8} | {'SL':<8} | {'TP1':<8}")
        for r in sorted(confirmed, key=lambda x: x['score'], reverse=True)[:15]:
            print(f"{r['code']:<10} | {r['name']:<12} | {r['stage']:<18} | {r['score']:<6.1f} | {r['close']:<8.2f} | {r['sl']:<8.2f} | {r['tp1']:<8.2f}")

    if potential:
        print(f"\n[SETUP_FORMING] - (Count: {len(potential)})")
        print("-" * 80)
        print(f"{'Code':<10} | {'Name':<12} | {'Stage':<18} | {'Score':<6} | {'Price':<8} | {'SL_Ref':<8} | {'TP_Ref':<8}")
        for r in sorted(potential, key=lambda x: x['score'], reverse=True)[:15]:
            print(f"{r['code']:<10} | {r['name']:<12} | {r['stage']:<18} | {r['score']:<6.1f} | {r['close']:<8.2f} | {r['sl']:<8.2f} | {r['tp1']:<8.2f}")

    print("\n" + "="*80)
    print("Strategy Notes: V34.4 uses strict H0 global max and chronological H1 search.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit number of stocks to scan")
    args = parser.parse_args()
    
    run_v34_scan(limit=args.limit)
