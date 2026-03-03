
# tools/scan_three_k.py
import sys
import os
import pandas as pd
from datetime import datetime
from tqdm import tqdm

# Setup paths
sys.path.append(os.getcwd())

import core.data_provider as data_provider
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy

def run_3k_scanner():
    print("\n" + "="*50)
    print("🚀 3K MOMENTUM SCANNER (Isolated Strategy Factory)")
    print("="*50)
    
    # 1. Get stock list
    stock_list = data_provider.get_stock_list()
    if not stock_list:
        print("❌ No stocks found in database.")
        return
        
    print(f"Loaded {len(stock_list)} stocks. Starting scan...")
    
    strategy = ThreeKStrategy()
    signals_found = []
    
    # 2. Sequential Scan to avoid resource bottleneck
    for code in tqdm(stock_list):
        try:
            # Get data
            df = data_provider.get_stock_data(code, limit=100)
            if df is None or len(df) < 60:
                continue
                
            # Add indicators
            df = add_indicators(df)
            
            # Run Strategy
            df = strategy.calculate_signals(df)
            
            # Check last row for signal
            latest = df.iloc[-1]
            if latest['signal_3k']:
                signals_found.append({
                    'code': code,
                    'date': latest['date'],
                    'close': latest['close'],
                    'sl': latest['sl_3k'],
                    'reason': f"Consecutive 3 Bulls + Gap ({latest['body_pct']:.2f}%)"
                })
                
        except Exception as e:
            continue
            
    # 3. Handle results
    print("\n" + "="*50)
    print(f"✅ SCAN COMPLETE. Found {len(signals_found)} 3K Signals.")
    print("="*50)
    
    if signals_found:
        df_results = pd.DataFrame(signals_found)
        print(df_results.to_string(index=False))
        
        # Save to lab
        os.makedirs("strategy_lab", exist_ok=True)
        filename = f"strategy_lab/scan_3k_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        df_results.to_csv(filename, index=False)
        print(f"\nResults saved to: {filename}")
    else:
        print("No immediate signals found today.")

if __name__ == "__main__":
    run_3k_scanner()
