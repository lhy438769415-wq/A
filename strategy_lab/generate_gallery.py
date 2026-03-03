import sys
import os
import pandas as pd
import mplfinance as mpf
import logging
import numpy as np
import matplotlib
matplotlib.use('Agg') # Force non-interactive backend

# Ensure root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import data_manager
from core import data_provider
from strategy_lab.pattern_bullish_pinbar import BullishPinbarStrategy

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plots')
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def plot_pattern(code, df, signal_idx, match_type):
    start_pos = max(0, signal_idx - 30)
    end_pos = min(len(df), signal_idx + 10)
    
    subset = df.iloc[start_pos:end_pos].copy()
    
    if subset.empty or len(subset) < 5:
        print(f"Subset too small for {code}")
        return

    if 'date' in subset.columns:
        subset['date'] = pd.to_datetime(subset['date'])
        subset.set_index('date', inplace=True)
    elif 'trade_date' in subset.columns:
        subset['trade_date'] = pd.to_datetime(subset['trade_date'])
        subset.set_index('trade_date', inplace=True)
    
    relative_idx = signal_idx - start_pos
    if relative_idx >= len(subset): relative_idx = len(subset) - 1
    
    signal_date = subset.index[relative_idx]
    
    match_type_safe = match_type.replace('/', '_').replace(' ', '_')
    filename = f"{match_type_safe}_{code}_{signal_date.strftime('%Y%m%d')}.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    try:
        title = f"{code} {match_type} {signal_date.date()}"
    except:
        title = f"{code} {match_type}"
    
    ema20 = subset['ema20']
    
    # Proven List-Based Logic
    marker_series = [float('nan')] * len(subset)
    marker_price = subset.iloc[relative_idx]['low'] * 0.98
    marker_series[relative_idx] = marker_price

    ap = [
        mpf.make_addplot(ema20, color='orange', width=1.0),
        mpf.make_addplot(marker_series, type='scatter', markersize=100, marker='^', color='g')
    ]
    
    print(f"Attempting to save: {filepath}")
    
    try:
        mpf.plot(
            subset, 
            type='candle', 
            style='yahoo', 
            addplot=ap, 
            title=title,
            volume=True,
            savefig=dict(fname=filepath, dpi=100, bbox_inches='tight')
        )
        print("Success save.")
    except Exception as e:
        logger.error(f"Failed to plot {code}: {e}")
        import traceback
        traceback.print_exc()

def run():
    strategy = BullishPinbarStrategy()
    # Explicit list from debug_criteria.py
    # sz.000785 (Found 5)
    # sh.601666 (Found 1)
    # sz.002705 (Found 1)
    # sh.603105 (Found 3)
    # sh.600838 (Found 2)
    scan_list = ['sz.000785', 'sh.601666', 'sz.002705', 'sh.603105', 'sh.600838']
    
    print(f"Generating gallery for: {scan_list}")
    
    for code in scan_list:
        df = data_manager.get_stock_data(code, limit=250)
        if df is None or len(df) < 50: 
            print(f"No data for {code}")
            continue
            
        try:
            if 'date' not in df.columns: df['date'] = pd.to_datetime(df['trade_date'])
            else: df['date'] = pd.to_datetime(df['date'])
        except: pass

        df_res = strategy.detect(df)
        
        if 'signal_pinbar' not in df_res.columns: continue
        signals = df_res[df_res['signal_pinbar']]
        
        if not signals.empty:
            for idx in signals.index:
                match_type = df_res.loc[idx, 'match_type']
                # Fix: Get integer location for iloc slicing
                try:
                    int_loc = df_res.index.get_loc(idx)
                    # If duplicate index, get_loc returns slice/array. Take first/last?
                    # Assuming unique daily data for now.
                    if isinstance(int_loc, (slice, np.ndarray)):
                        int_loc = int_loc.start if isinstance(int_loc, slice) else int_loc[0]
                    
                    print(f"Found {match_type} in {code} at label {idx} (pos {int_loc})")
                    plot_pattern(code, df_res, int_loc, match_type)
                except Exception as e:
                    print(f"Index lookup failed for {idx}: {e}")

if __name__ == "__main__":
    run()
