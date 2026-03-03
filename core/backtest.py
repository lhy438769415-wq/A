
import pandas as pd
import numpy as np
import logging

class VectorBacktester:
    def __init__(self, data_manager):
        self.dm = data_manager
        self.logger = logging.getLogger(__name__)

    def run_backtest(self, full_code, strategy_func=None):
        """
        Run a simple vector backtest for Price Action verification.
        For now, we simulate a simple 'Buy if Close > Open' (Bull Bar) strategy 
        just to verify the infrastructure.
        """
        try:
            # 1. Get Data (1 Year)
            df = self.dm.get_stock_data(full_code, limit=250)
            if df is None or df.empty:
                return {"error": "No data"}

            df = df.copy()
            df['return'] = df['close'].pct_change()
            
            # 2. Vector Logic (Placeholder for real strategy)
            # Example: Buy if Bull Bar (Close > Open)
            df['signal'] = np.where(df['close'] > df['open'], 1, 0)
            
            # 3. Calculate PnL
            # Strategy Return = Signal(t-1) * Return(t)
            df['strategy_return'] = df['signal'].shift(1) * df['return']
            
            # 4. Metrics
            total_return = (1 + df['strategy_return'].fillna(0)).prod() - 1
            win_days = len(df[df['strategy_return'] > 0])
            total_days = len(df[df['strategy_return'] != 0])
            win_rate = win_days / total_days if total_days > 0 else 0
            
            return {
                "symbol": full_code,
                "total_return": f"{total_return*100:.2f}%",
                "win_rate": f"{win_rate*100:.2f}%",
                "days": len(df)
            }
            
        except Exception as e:
            self.logger.error(f"Backtest failed: {e}")
            return {"error": str(e)}
