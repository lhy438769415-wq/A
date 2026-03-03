# tools/scan_3k_market.py
"""
全市场3K策略扫描 — 纯本地数据库
扫描所有股票，找出满足五步逻辑的3K信号和缺口测试确认信号
"""
import sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import warnings
warnings.simplefilter('ignore')

from core.database import get_db_connection
from core.data_provider import get_stock_data
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
from config import settings

def get_all_symbols():
    """从本地数据库获取所有股票代码"""
    with get_db_connection() as conn:
        cur = conn.execute("SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]

def scan_market():
    start = time.time()
    symbols = get_all_symbols()
    total = len(symbols)
    print(f"全市场3K策略扫描 — {total} 只股票")
    print("=" * 80)

    strategy = ThreeKStrategy()
    all_3k = []       # 所有 signal_3k
    all_gap_test = []  # 所有 signal_3k_gap_test
    errors = 0
    processed = 0

    for i, sym in enumerate(symbols):
        if (i + 1) % 200 == 0:
            elapsed = time.time() - start
            pct = (i + 1) / total * 100
            print(f"  进度: {i+1}/{total} ({pct:.0f}%) | 耗时: {elapsed:.0f}s | 3K信号: {len(all_3k)} | 确认: {len(all_gap_test)}")

        try:
            code = f"sz.{sym}" if sym.startswith(('0', '3')) else f"sh.{sym}"
            df = get_stock_data(code)
            if df is None or len(df) < 60:
                continue

            df = add_indicators(df)
            df = strategy.calculate_signals(df)
            processed += 1

            # 收集 signal_3k
            sigs = df[df['signal_3k'] == True]
            for _, r in sigs.iterrows():
                idx = df.index.get_loc(r.name)
                k1_high = df.iloc[idx - 2]['high'] if idx >= 2 else 0
                psh = df['high'].rolling(40).max().shift(3).iloc[idx]
                psl = df['low'].rolling(40).min().shift(3).iloc[idx]
                all_3k.append({
                    '代码': sym,
                    '日期': r['date'],
                    'Close': r['close'],
                    'K1_High': k1_high,
                    'K3_Low': r['low'],
                    'PSH': psh,
                    'PSL': psl,
                    '测量目标': 2 * ((k1_high + r['low']) / 2) - psl,
                })

            # 收集 signal_3k_gap_test
            gts = df[df.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True]
            for _, r in gts.iterrows():
                entry = r.get('entry_3k_gap_test', np.nan)
                sl = r.get('sl_3k_gap_test', np.nan)
                tp = r.get('tp_3k_gap_test', np.nan)
                risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                rr = (tp - entry) / risk if risk > 0 and not np.isnan(tp) else 0
                all_gap_test.append({
                    '代码': sym,
                    '日期': r['date'],
                    'Entry': entry,
                    'SL': sl,
                    'TP': tp,
                    'R:R': f'1:{rr:.1f}' if rr > 0 else 'N/A',
                })

        except Exception:
            errors += 1
            continue

    elapsed = time.time() - start

    # === 输出结果 ===
    print(f"\n{'='*80}")
    print(f"扫描完成: {processed}/{total} 只股票 | 耗时: {elapsed:.0f}s | 错误: {errors}")
    print(f"{'='*80}")

    # 3K信号
    df_3k = pd.DataFrame(all_3k)
    print(f"\n📌 3K信号总数: {len(df_3k)}")
    if len(df_3k) > 0:
        pd.set_option('display.float_format', '{:.2f}'.format)
        pd.set_option('display.max_rows', 100)
        pd.set_option('display.width', 200)
        print(df_3k.to_string(index=False))

        # 按年份分布
        df_3k['年份'] = df_3k['日期'].str[:4]
        print(f"\n按年份分布:")
        print(df_3k['年份'].value_counts().sort_index().to_string())

    # 缺口测试确认
    df_gt = pd.DataFrame(all_gap_test)
    print(f"\n📌 缺口测试确认信号: {len(df_gt)}")
    if len(df_gt) > 0:
        print(df_gt.to_string(index=False))

    # 保存
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'strategy_lab')
    os.makedirs(out_dir, exist_ok=True)
    if len(df_3k) > 0:
        df_3k.to_csv(os.path.join(out_dir, 'scan_3k_market.csv'), index=False, encoding='utf-8-sig')
    if len(df_gt) > 0:
        df_gt.to_csv(os.path.join(out_dir, 'scan_3k_gap_test_market.csv'), index=False, encoding='utf-8-sig')
    print(f"\n💾 结果已保存至 strategy_lab/")

if __name__ == '__main__':
    scan_market()
