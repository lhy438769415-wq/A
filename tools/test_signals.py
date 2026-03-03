import pandas as pd
from core.data_provider import get_stock_list, get_stock_data_weekly
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
import traceback

codes = get_stock_list()
st = ThreeKStrategy()

c3 = 0
cgt = 0

print(f"开始诊断全市场 {len(codes)} 只股票的最近 4 周 3K 信号...")

for code in codes:
    try:
        df = get_stock_data_weekly(code, 200)
        if df is None or len(df) < 60:
            continue
            
        df = add_indicators(df)
        df = st.calculate_signals(df)
        
        recent = df.tail(4)
        c3 += recent['signal_3k'].sum()
        cgt += recent.get('signal_3k_gap_test', pd.Series(dtype=bool)).sum()
        
    except Exception as e:
        print(f"Error at {code}: {e}")
        traceback.print_exc()
        break

print(f"诊断结束! 3K初现信号总数: {c3}, 缺口测试确认信号总数: {cgt}")
