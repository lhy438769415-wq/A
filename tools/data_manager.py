"""
[Facade] Data Manager
Redirects calls to the new core/data_provider and core/database modules.
Maintained for backward compatibility.
"""

import sys
import os
# 确保从子目录运行时能正确导入 core 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import get_db_connection, init_db
import core.data_provider as dp
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Wrapper functions to ensure runtime lookup (supporting mocks)
def get_all_codes():
    return dp.get_stock_list()

def fetch_daily_snapshot(code, limit=None):
    """
    获取股票数据（纯离线模式）
    
    注意：此函数只读取本地数据库，不会发起网络请求。
    如需实时数据，请先运行 data_manager.py 同步。
    """
    return dp.get_stock_data(code, limit)  # ✅ 纯离线读取

def get_intraday_data(code):
    return dp.fetch_minute_bars(code)

def update_daily_data(max_workers=None):
    if max_workers:
        return dp.update_daily_data_batch(max_workers)
    return dp.update_daily_data_batch()

# Aliases matching original interface name but pointing to wrappers
get_stock_list = get_all_codes
get_stock_data = fetch_daily_snapshot  # ✅ 现在指向离线函数
fetch_minute_bars = get_intraday_data
update_daily_data_batch = update_daily_data

# ==========================================
# 🔒 OFFLINE ONLY (For Strategy Lab / Backtest)
# ==========================================
def get_stock_data_offline(code, limit=None):
    """
    Pure offline data read from local DB.
    NO network requests will be made.
    Use this for backtesting and Strategy Lab experiments.
    """
    return dp.get_stock_data(code, limit)  # This is pure DB read

if __name__ == "__main__":
    update_daily_data()