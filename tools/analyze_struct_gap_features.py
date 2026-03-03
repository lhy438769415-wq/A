import pandas as pd
import numpy as np
import os

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(project_root, 'data', 'struct_gap_features.csv')

df = pd.read_csv(data_path)

print("=== Structural Gap Feature Analysis ===")
print(f"Total Samples: {len(df)}")
print(f"Baseline Win Rate: {df['result'].mean()*100:.2f}%")
print("---------------------------------------")

def analyze_feature(df, column, q=5):
    try:
        # qcut produces quantiles
        df[f'{column}_bin'] = pd.qcut(df[column], q=q, duplicates='drop')
        grouped = df.groupby(f'{column}_bin')['result'].agg(['count', 'mean'])
        grouped['mean'] = (grouped['mean'] * 100).round(2)
        grouped.rename(columns={'mean': 'win_rate(%)', 'count': 'samples'}, inplace=True)
        print(f"\n[Feature Analysis] {column}")
        print(grouped)
    except Exception as e:
        print(f"\nCould not bin {column}: {e}")

features = ['gap_size_atr', 'retracement_depth', 'sig_bar_quality']
for feat in features:
    analyze_feature(df, feat, q=5)

# Also test combinations (e.g. good quality signal bar + deep retracement)
print("\n=== Combination Analysis ===")
# Example: What if gap size is large (> median) AND sig_bar is good (> median)?
med_gap = df['gap_size_atr'].median()
med_sig = df['sig_bar_quality'].median()
med_retract = df['retracement_depth'].median()

cond_1 = (df['gap_size_atr'] > med_gap) & (df['sig_bar_quality'] >= med_sig)
print(f"Large Gap + Good Signal Bar:")
print(f"Win Rate: {df[cond_1]['result'].mean()*100:.2f}%, Samples: {len(df[cond_1])}")

cond_2 = (df['retracement_depth'] < med_retract) & (df['sig_bar_quality'] >= med_sig)
print(f"\nShallow Retracement (Close to floor) + Good Signal:")
print(f"Win Rate: {df[cond_2]['result'].mean()*100:.2f}%, Samples: {len(df[cond_2])}")

cond_3 = (df['gap_size_atr'] > med_gap) & (df['retracement_depth'] > med_retract) & (df['sig_bar_quality'] >= med_sig)
print(f"\nLarge Gap + High Retracement (far from floor) + Good Signal:")
if len(df[cond_3]) > 0:
    print(f"Win Rate: {df[cond_3]['result'].mean()*100:.2f}%, Samples: {len(df[cond_3])}")

# Let's find the optimal Top 3 Rules based on quartile cutoffs
print("\n=== Optimal Feature Threshold Exploration ===")
for q in [0.75, 0.8, 0.9]:
    thresh_sig = df['sig_bar_quality'].quantile(q)
    cond = df['sig_bar_quality'] >= thresh_sig
    print(f"Top {100-q*100:.0f}% Signal Bar (Quality >= {thresh_sig:.2f}): Win Rate {df[cond]['result'].mean()*100:.2f}%, Samples: {len(df[cond])}")

df['risk'] = df['entry_struct_gap'] - df['sl_price'] if 'entry_struct_gap' in df.columns else (df['tp_price'] - df['sl_price'])/3 # rough est
# Calculate a basic RR proxy to see expected value if needed.

