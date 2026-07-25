import sys
import os
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# 确保核心路径在 python path 中
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.data_provider import get_stock_data
from core.strategies.mtr_strategy import MTRStrategy
from core.calculator import add_indicators

def plot_structural_mtr(symbol="sz.000001", target_date="2024-11-13"):
    print(f"🎨 Generating Structural Chart for {symbol} around {target_date}...")
    
    # 1. Get and Process Data
    df = get_stock_data(symbol, limit=800)
    if df is None: return
    df = add_indicators(df)
    strategy = MTRStrategy()
    df = strategy.calculate_signals(df)
    
    df['date_dt'] = pd.to_datetime(df['date'])
    t_dt = pd.to_datetime(target_date)
    
    # 2. Find the row with the signal
    # We look for the closest signal to target_date
    signals = df[df['signal_mtr'] == True]
    if signals.empty:
        print("No MTR signal found in data.")
        return
        
    # Get the signal closest to our target
    row = signals.iloc[(signals['date_dt'] - t_dt).abs().argsort()[:1]].iloc[0]
    print(f"Plotting signal found on: {row['date']}")
    
    # 3. Define Plot Range (around the whole sequence)
    h0_idx = int(row['mtr_H0_idx'])
    sig_idx = df.index.get_loc(row.name)
    
    plot_start = max(0, h0_idx - 20)
    plot_end = min(len(df) - 1, sig_idx + 10)
    slice_df = df.iloc[plot_start:plot_end+1].copy()
    
    # 4. Plotting
    plt.figure(figsize=(15, 8))
    plt.style.use('dark_background')
    
    # Plot Close Price
    plt.plot(slice_df['date_dt'], slice_df['close'], color='#4a90e2', alpha=0.6, label='Close')
    plt.plot(slice_df['date_dt'], slice_df['ema20'], color='#f5a623', alpha=0.4, linestyle='--', label='EMA20')
    
    # Annotate 5 Key Points
    points = ['H0', 'L1', 'H1', 'TL', 'H2']
    colors = ['#ff4d4f', '#52c41a', '#ff4d4f', '#52c41a', '#ff4d4f']
    
    for pt, color in zip(points, colors):
        p_idx = int(row[f'mtr_{pt}_idx'])
        p_price = row[f'mtr_{pt}_price']
        p_date = df['date_dt'].iloc[p_idx]
        
        plt.scatter(p_date, p_price, color=color, s=100, zorder=5, edgecolor='white')
        plt.annotate(pt, (p_date, p_price), textcoords="offset points", xytext=(0,10), 
                     ha='center', fontsize=12, fontweight='bold', color=color)
        print(f"Marked {pt} at {df['date'].iloc[p_idx]} : {p_price}")

    plt.title(f"MTR V32.0 Structural Audit: {symbol} @ {row['date']}", fontsize=14)
    plt.grid(alpha=0.1)
    plt.legend()
    
    # Save
    filename = f"mtr_v32_audit_{symbol}.png"
    plt.savefig(filename)
    plt.close()
    print(f"✅ Chart saved as {filename}")

if __name__ == "__main__":
    # 生成平安银行和万科的结构图
    plot_structural_mtr("sz.000001", "2024-11-13")
    plot_structural_mtr("sz.000002", "2024-11-13")
