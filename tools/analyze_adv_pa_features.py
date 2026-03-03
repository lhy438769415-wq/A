import pandas as pd
import numpy as np
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(project_root, 'data', 'adv_pullback_features.csv')

def analyze():
    try:
        df = pd.read_csv(data_path)
    except FileNotFoundError:
        print("Data not ready")
        return

    print("=== Advanced Pullback PA Feature Analysis ===")
    print(f"Total Samples: {len(df)}")
    print(f"Baseline Win Rate: {df['result'].mean()*100:.2f}%\n")

    def print_qcut(col, q=4):
        try:
            df[f'{col}_bin'] = pd.qcut(df[col], q=q, duplicates='drop')
            grouped = df.groupby(f'{col}_bin')['result'].agg(['count', 'mean'])
            grouped['mean'] = (grouped['mean'] * 100).round(2)
            grouped.rename(columns={'mean': 'win_rate(%)', 'count': 'samples'}, inplace=True)
            print(f"-- {col} --")
            print(grouped)
            print()
        except Exception as e:
            print(f"Skipping {col} due to {e}")
            
    # Continuous vars
    for col in ['bear_bar_pct', 'overlap_pct', 'pct_below_ema20']:
        print_qcut(col)
        
    # Discrete vars
    grouped_consec = df.groupby('max_consec_bear')['result'].agg(['count', 'mean'])
    grouped_consec['mean'] = (grouped_consec['mean'] * 100).round(2)
    print(f"-- max_consec_bear --")
    print(grouped_consec)
    print()

    # Advanced combo
    print("\n[Combo] Filter out toxic pullbacks:")
    # Too many consecutive bears, low overlap (uninterrupted selling) -> Bad?
    heavy_sell = (df['max_consec_bear'] >= 3) | (df['bear_bar_pct'] > 0.6)
    print(f"Toxic Pullback (Consec >= 3 or BearPct > 60%): Win Rate = {df[heavy_sell]['result'].mean()*100:.2f}%, N={len(df[heavy_sell])}")
    print(f"Safe Pullback: Win Rate = {df[~heavy_sell]['result'].mean()*100:.2f}%, N={len(df[~heavy_sell])}")

if __name__ == "__main__":
    analyze()
