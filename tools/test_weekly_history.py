import sys
import os

# 确保能正确引入 core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

import pandas as pd
from core.data_provider import get_stock_list, get_stock_data_weekly
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
import traceback

def main():
    codes = get_stock_list()
    st = ThreeKStrategy()

    total_3k = 0
    total_gt = 0
    
    stocks_with_gt = []

    print(f"📅 开始全局回测全市场 {len(codes)} 只股票的【全部历史】周线 3K 信号...")
    print(f"由于需要计算所有历史 K 线的 EMA 和 ATR 等指标，预计需要几十秒至一分钟，请稍候...\n")

    for i, code in enumerate(codes):
        if i % 200 == 0 and i > 0:
            print(f"⏳ 进度: 已扫描 {i} / {len(codes)} 只股票... (累计 3K: {total_3k}, GT: {total_gt})")
            
        try:
            # limit=None 获取所有历史周线数据（最多 15 年）
            df = get_stock_data_weekly(code, limit=None) 
            if df is None or len(df) < 60:
                continue
                
            df = add_indicators(df)
            df = st.calculate_signals(df)
            
            c3 = df['signal_3k'].sum()
            if 'signal_3k_gap_test' in df.columns:
                cgt = df['signal_3k_gap_test'].sum()
            else:
                cgt = 0
                
            total_3k += c3
            total_gt += cgt
            
            if cgt > 0:
                stocks_with_gt.append((code, cgt))
                
        except Exception as e:
            # 如果某只破发股没有足够的历史算不出均线，直接跳过
            pass

    print(f"\n========================================================")
    print(f"✅ 全局历史（15年跨度）回测结束!")
    print(f"📈 3K雏形信号总数 (全部历史): {total_3k} 个")
    print(f"🎯 缺口测试确认信号总数 (全部历史): {total_gt} 个")
    print(f"========================================================\n")
    
    if stocks_with_gt:
        stocks_with_gt.sort(key=lambda x: x[1], reverse=True)
        print("💡 产生过【缺口测试确认】也就是最终买点最多的前 20 只周线股票:")
        for code, count in stocks_with_gt[:20]:
            print(f"  - {code}: 出现过 {count} 次")

if __name__ == '__main__':
    main()
