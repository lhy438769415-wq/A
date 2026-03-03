import sys
import os
import pandas as pd
import logging
from datetime import datetime, timedelta

# Ensure root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import data_manager
from strategy_lab.pattern_bullish_pinbar import BullishPinbarStrategy

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def run_verification():
    """
    Load data for a few diverse stocks and check signal frequency.
    """
    logger.info("🧪 Strategy Lab: Long Lower Wick Pattern Verification")
    
    # Expanded stock list for better signal coverage
    test_codes = [
        # 银行
        'sh.600000', 'sh.601318', 'sh.601138', 'sh.601166',
        # 消费
        'sz.000858', 'sz.000651', 'sh.600519',
        # 科技
        'sz.002594', 'sz.300750', 'sz.002475',
        # 新能源
        'sh.601012', 'sz.002129',
        # 周期
        'sh.601899', 'sz.000725'
    ]
    
    strategy = BullishPinbarStrategy()
    
    total_signals = 0
    type_counts = {}  # Track signal types
    
    for code in test_codes:
        # 🔒 OFFLINE ONLY: Read from local database, NO network!
        df = data_manager.get_stock_data_offline(code, limit=200)
        
        if df is None or df.empty:
            logger.warning(f"Skipping {code} (No data)")
            continue
            
        # Run detection
        df_res = strategy.detect(df)
        
        # Filter for signals
        signals = df_res[df_res['signal_pinbar']]
        
        from core import data_provider
        name = data_provider.get_stock_name(code)
        logger.info(f"Checking {name} ({code})... Found {len(signals)} signals in last 200 days.")
        
        if not signals.empty:
            for _, row in signals.iterrows():
                date_str = row['date'] if 'date' in row else row.get('trade_date', 'N/A')
                match_type = row.get('match_type', 'Unknown')
                
                # Count types
                type_counts[match_type] = type_counts.get(match_type, 0) + 1
                
                logger.info(f"  👉 [{match_type}] {date_str}: Close={row['close']:.2f}, Wick={row['lower_wick_pct']*100:.1f}%")
            total_signals += len(signals)
            
    if total_signals == 0:
        logger.warning("⚠️ No signals found. Criteria might be too strict.")
    else:
        logger.info(f"✅ Protocol validated. Total signals found: {total_signals}")
        logger.info("📊 Signal Type Distribution:")
        for t, c in type_counts.items():
            logger.info(f"   - {t}: {c}")

if __name__ == "__main__":
    run_verification()
