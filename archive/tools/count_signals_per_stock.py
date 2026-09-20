# archive/tools/count_signals_per_stock.py
"""
[计数] 统计每只标的在 B(C0原版) / D(C0+重置) 两种配置下的 GAP H2 信号数量,
输出信号总数最多的前 N 只, 用于"单股成册"选样。

注: 仅计数信号 (calculate_signals), 不跑交易模拟, 比完整回测快。
"""
import os
import sys
import time
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed

warnings_mod = __import__('warnings')
warnings_mod.filterwarnings('ignore')


def _find_project_root(start: str) -> str:
    p = os.path.abspath(start)
    while p and p != os.path.dirname(p):
        if os.path.isdir(os.path.join(p, 'core')) and os.path.isfile(os.path.join(p, 'core', 'paths.py')):
            return p
        p = os.path.dirname(p)
    return p


project_root = _find_project_root(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from core.paths import ensure_importable
ensure_importable()

from backtest_gap_h2_new_exit import CONFIGS, _make_strategy
from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from config import settings


def _count_one(code):
    try:
        df = get_stock_data(code, limit=None)
        if df is None or len(df) < 100:
            return None
        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        df = add_indicators(df)
        res = {'code': code, 'B': 0, 'D': 0}
        for cfg in CONFIGS:
            if cfg['exit'] != 'new':
                continue  # 只关心新出场两配置 B(orig) / D(enh)
            strat = _make_strategy(cfg)
            sdf = strat.calculate_signals(df.copy())
            sig_col = 'signal_gap_h2'
            n = int(sdf[sig_col].fillna(False).values.sum())
            if cfg['label'].startswith('B'):
                res['B'] = n
            elif cfg['label'].startswith('D'):
                res['D'] = n
        if res['B'] == 0 and res['D'] == 0:
            return None
        return res
    except Exception as e:
        return {'code': code, 'B': 0, 'D': 0, 'err': str(e)}


def main(top=20):
    codes = get_stock_list()
    if not codes:
        print("[!] 无股票列表")
        return
    n = len(codes)
    print(f"[*] 计数 {n} 只标的 (B/D GAP H2 信号) ...")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=getattr(settings, 'MAX_WORKERS', 4)) as exe:
        futs = {exe.submit(_count_one, c): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [{done}/{n}]", flush=True)
            r = f.result()
            if r:
                results.append(r)
    print(f"[*] 完成, 耗时 {time.time()-t0:.1f}s")

    df = pd.DataFrame(results)
    df['total'] = df['B'] + df['D']
    df = df.sort_values('total', ascending=False).head(top)
    print(f"\n[*] TOP {top} (按 B+D 信号总数):")
    print(f"{'code':<14}{'B':>5}{'D':>5}{'total':>7}")
    for _, row in df.iterrows():
        print(f"{row['code']:<14}{int(row['B']):>5}{int(row['D']):>5}{int(row['total']):>7}")
    out = os.path.join(project_root, 'archive', 'top_bd_signal_stocks.csv')
    df.to_csv(out, index=False)
    print(f"[*] 已写入: {out}")


if __name__ == '__main__':
    main(top=20)
