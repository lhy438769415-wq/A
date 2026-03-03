import sys
import os
import pandas as pd
import mplfinance as mpf
import logging
from datetime import datetime

# Ensure root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import data_manager
from core import data_provider
from strategy_lab.pattern_bullish_pinbar import BullishPinbarStrategy

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plots')
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def plot_pattern(code, df, signal_idx, match_type):
    """
    Generate a plot for a specific signal context.
    Parameters:
        signal_idx: The index (integer position) of the signal bar
    """
    # Slice data: 30 bars before, 10 bars after (if available)
    start_pos = max(0, signal_idx - 30)
    end_pos = min(len(df), signal_idx + 10)
    
    subset = df.iloc[start_pos:end_pos].copy()
    if subset.empty or len(subset) < 5:
        logger.warning(f"Skipping plot for {code} (Insufficient data in window)")
        return

    # Check mapping
    if 'date' in subset.columns:
        subset['date'] = pd.to_datetime(subset['date'])
        subset.set_index('date', inplace=True)
    elif 'trade_date' in subset.columns:
        subset['trade_date'] = pd.to_datetime(subset['trade_date'])
        subset.set_index('trade_date', inplace=True)
    
    # Ensure signal_idx is relative to subset start
    relative_idx = signal_idx - start_pos
    if relative_idx >= len(subset):
         relative_idx = len(subset) - 1
         
    match_name = data_provider.get_stock_name(code)
    try:
        # Title handling for chinese characters might fail in some envs, strip name if needed
        # Or just use code
        title = f"{code} - {match_type}"
    except:
        title = f"{code} Signal"
    
    # Add EMA20 (handle NaN)
    ema20 = subset['ema20']
    
    # Add Marker
    # Identify the signal bar date
    signal_date = subset.index[relative_idx]
    
    # Create marker series (all NaN except signal)
    marker_series = [float('nan')] * len(subset)
    # Price for marker (below low)
    # Use integer indexing for safety
    marker_price = subset.iloc[relative_idx]['low'] * 0.98
    marker_series[relative_idx] = marker_price

    # Plot
    ap = [
        mpf.make_addplot(ema20, color='orange', width=1.0),
        mpf.make_addplot(marker_series, type='scatter', markersize=100, marker='^', color='g')
    ]
    
    filename = f"{match_type.replace('/', '_').replace(' ', '_')}_{code}_{signal_date.strftime('%Y%m%d')}.png"
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    mpf.plot(
        subset, 
        type='candle', 
        style='yahoo', 
        addplot=ap, 
        title=title,
        volume=True,
        savefig=dict(fname=filepath, dpi=100, bbox_inches='tight')
    )
    logger.info(f"📸 Saved plot: {filename}")

def run_scan_and_plot():
    strategy = BullishPinbarStrategy()
    
    # Expand range: Use a broader list or all if feasible.
    # For demo, let's use a manually defined diverse list + some active ones
    scan_list = [
        'sh.600000', 'sh.600036', 'sh.601318', 'sz.000001', # Banks
        'sz.002594', 'sz.000858', 'sh.600519', # Consumer/Tech
        'sz.300750', 'sh.601138', 'sh.600438'  # Volatile ones
    ]
    
    # Also fetch active list from DB if possible
    db_stocks = data_manager.get_stock_list()
    # Randomly sample 1500 stocks from DB to widen net
    # Randomly sample 500 stocks from DB to widen net
    import random
    if db_stocks:
        random_sample = random.sample(db_stocks, min(500, len(db_stocks)))
        scan_list = list(set(scan_list + random_sample))
    
    logger.info(f"🔍 Scanning {len(scan_list)} stocks with STRICT criteria (Wick>66%, Shaved Head)...")
    
    found_counts = {'Double Bottom': 0, 'EMA20 Pullback': 0, 'Micro DB/Confluence': 0}
    
    for code in scan_list:
        try:
            df = data_manager.get_stock_data(code, limit=250)
            if df is None or len(df) < 50: continue
            
            # Ensure 'date' is datetime
            if 'date' not in df.columns and 'trade_date' in df.columns:
                 df['date'] = pd.to_datetime(df['trade_date'])
            elif 'date' in df.columns:
                 df['date'] = pd.to_datetime(df['date'])
            
            df_res = strategy.detect(df)
            if 'signal_pinbar' not in df_res.columns: continue

            signals = df_res[df_res['signal_pinbar']]
            
            if signals.empty: continue
            
            # Iterate signals
            for idx in signals.index:
                match_type = df_res.loc[idx, 'match_type']
                
                # Check limit: Don't plot too many of same type
                if found_counts.get(match_type, 0) >= 3: 
                    continue # Skip if we have enough examples for this type
                    
                # Plot
                plot_pattern(code, df_res, idx, match_type)
                found_counts[match_type] = found_counts.get(match_type, 0) + 1
                
        except Exception as e:
            logger.error(f"Error processing {code}: {e}")

    logger.info(f"✅ Visualization Complete. Found: {found_counts}")

if __name__ == "__main__":
    run_scan_and_plot()
