import pandas as pd
import logging
import sqlite3
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.database import get_db_connection
from core.calculator import add_indicators, add_momentum_features
from core.data_provider import get_stock_data

logger = logging.getLogger(__name__)

class FeaturePipeline:
    """
    [V1.9 Data Engineering]
    ETL Pipeline to calculate and persist Abu Indicators to DB.
    Removes "Raw CSV/DataFrame Calculation" dependency at scanning time.
    """

    @staticmethod
    def process_symbol(symbol: str, lookback: int = 500) -> bool:
        """
        1. Read Raw OHLCV (Offline)
        2. Calculate Indicators (add_indicators + add_momentum_features)
        3. Persist to 'abu_indicators' table
        """
        try:
            # 1. Read Raw Data
            # Note: get_stock_data_offline needs full code "sz.xxxxxx", but symbol usually is just digits here
            # We try to infer or pass just digits if get_stock_data_offline supports it, 
            # but usually it expects 'sh.600000'.
            # Let's standardize input to be safe. 
            pass 
            
            # Actually, let's use a lower level read to ensure we get what we need.
            # Or use data_provider if it's robust.
            # Let's assume input 'symbol' is 6 digits.
            prefix = 'sh' if symbol.startswith('6') else 'sz'
            full_code = f"{prefix}.{symbol}"
            
            df = get_stock_data(full_code, limit=lookback)
            if df is None or df.empty:
                return False

            # 2. Calculate Features
            df = add_indicators(df)
            df = add_momentum_features(df)

            # 3. Extract & Format for DB
            # Schema: symbol, trade_date, ema_20, atr, trend_slope, wick_pct, gap_down_count, relative_vol, linreg_res
            required_cols = [
                'trade_date', 'ema_20', 'atr', 'trend_slope', 
                'wick_pct', 'gap_down_count', 'relative_vol', 'linreg_res'
            ]
            
            # Initial checks if columns exist (calculator might handle them differently)
            for col in required_cols:
                if col not in df.columns:
                    df[col] = 0.0 # Default fallback
            
            # Filter valid rows (e.g. drop NaN from rolling windows)
            df_valid = df.dropna(subset=['ema_20', 'atr']) 
            
            if df_valid.empty:
                return False

            records = []
            for _, row in df_valid.iterrows():
                records.append((
                    symbol,
                    row['trade_date'],
                    round(row.get('ema_20', 0), 3),
                    round(row.get('atr', 0), 3),
                    round(row.get('trend_slope', 0), 5),
                    round(row.get('wick_pct', 0), 2),
                    int(row.get('gap_down_count_20', 0)), # Map from calc name to DB name
                    round(row.get('relative_vol', 0), 2),
                    round(row.get('linreg_res', 0), 3)
                ))

            # 4. Batch Write
            return FeaturePipeline._write_batch(records)

        except Exception as e:
            logger.error(f"❌ Feature Pipeline failed for {symbol}: {e}")
            return False

    @staticmethod
    def _write_batch(records: List[tuple]) -> bool:
        if not records:
            return True
            
        sql = """
        REPLACE INTO abu_indicators 
        (symbol, trade_date, ema_20, atr, trend_slope, wick_pct, gap_down_count, relative_vol, linreg_res)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        try:
            with get_db_connection() as conn:
                conn.executemany(sql, records)
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"❌ DB Write failed: {e}")
            return False

    @staticmethod
    def run_batch_job(symbols: List[str], max_workers: int = 4):
        """
        Run ETL for a list of symbols (Digits only)
        """
        logger.info(f"🚀 Starting Feature ETL Job for {len(symbols)} symbols...")
        count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(FeaturePipeline.process_symbol, sym): sym for sym in symbols}
            
            for future in as_completed(futures):
                if future.result():
                    count += 1
                if count % 50 == 0:
                    logger.info(f"ETL Progress: {count}/{len(symbols)}")
        
        logger.info(f"✅ Feature ETL Completed. Processed {count} stocks.")
