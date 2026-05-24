# tools/backtest_gap_h2.py
"""
[回测] Gap + High 2 (两腿回调) 策略

信号状态机：突破缺口 → LHLL(第一次回调) → HH(High 1) → LHLL(第二次回调) → Buy Stop
生命周期：防守端(gap_fill) + 收益端(tp_before_entry) + 时限端(30bars)
"""
import os, sys, time
import pandas as pd
import numpy as np
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data, get_stock_data_weekly, get_stock_list
from core.calculator import add_indicators
from core.strategies.gap_h2_strategy import GapH2Strategy
from config import settings

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')

LIFECYCLE_TIMEOUT_BARS = 30


def evaluate_trade(df, signal_idx):
    """含完整三条生命周期过滤的交易模拟"""
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_gap_h2']
        sl_price = sig_row['sl_gap_h2']
        tp_price = sig_row['tp_gap_h2']
        gap_floor = sig_row['gap_h2_floor_exact']

        if pd.isna(entry_price) or pd.isna(sl_price) or pd.isna(tp_price):
            return {'status': 'ERROR', 'reason': 'NaN'}

        # 回调特征
        bsg = sig_row['bars_since_breakout_h2']
        pb_df = df[(df['bars_since_breakout_h2'] == bsg) & (df.index < df.index[signal_idx])]
        pb_bars = len(pb_df)
        sig_q = float(sig_row.get('sig_bar_quality_h2', 0))
        base = {'sig_quality': sig_q, 'pb_bars': pb_bars}

        post = df.iloc[signal_idx + 1:]
        if post.empty:
            return {**base, 'status': 'PENDING', 'reason': 'EOF'}

        status = 'WAITING'
        bars_waited = 0
        entry_date = actual_entry = None

        for i in range(len(post)):
            row = post.iloc[i]
            row_date = row['date'] if 'date' in row else post.index[i]

            if status == 'WAITING':
                bars_waited += 1
                if row['low'] < gap_floor - 1e-3:
                    return {**base, 'status': 'INVALIDATED', 'reason': 'gap_filled'}
                if row['high'] >= tp_price:
                    return {**base, 'status': 'VOIDED', 'reason': 'tp_before_entry'}
                if bars_waited > LIFECYCLE_TIMEOUT_BARS:
                    return {**base, 'status': 'TIMEOUT', 'reason': 'timeout'}
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row_date
                    status = 'IN_TRADE'
                    if row['low'] <= sl_price:
                        return {**base, 'status': 'LOSS', 'entry_date': entry_date, 'exit_date': entry_date,
                                'entry_price': actual_entry, 'exit_price': sl_price, 'reason': 'same_day_stop'}
                    if row['high'] >= tp_price:
                        return {**base, 'status': 'WIN', 'entry_date': entry_date, 'exit_date': entry_date,
                                'entry_price': actual_entry, 'exit_price': tp_price, 'reason': 'same_day_tp'}
            elif status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    return {**base, 'status': 'LOSS', 'entry_date': entry_date, 'exit_date': row_date,
                            'entry_price': actual_entry, 'exit_price': min(sl_price, row['open']), 'reason': 'stop_loss'}
                if row['high'] >= tp_price:
                    return {**base, 'status': 'WIN', 'entry_date': entry_date, 'exit_date': row_date,
                            'entry_price': actual_entry, 'exit_price': tp_price, 'reason': 'take_profit'}

        if status == 'IN_TRADE':
            return {**base, 'status': 'HOLDING', 'entry_date': entry_date, 'entry_price': actual_entry, 'reason': 'eof'}
        return {**base, 'status': 'PENDING', 'reason': 'not_triggered'}
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}


def backtest_single(code, timeframe='daily', limit=1500):
    try:
        df = get_stock_data_weekly(code, limit=limit) if timeframe == 'weekly' else get_stock_data(code, limit=limit)
        if df is None or len(df) < 100:
            return []
        df = add_indicators(df)
        strategy = GapH2Strategy()
        df = strategy.calculate_signals(df)

        sig_col = 'signal_gap_h2'
        indices = [i for i, v in enumerate(df[sig_col]) if v]
        results = []
        for idx in indices:
            trade = evaluate_trade(df, idx)
            sig_date = df.iloc[idx]['date'] if 'date' in df.columns else str(df.index[idx])
            trade['code'] = code
            trade['signal_date'] = str(sig_date)[:10]
            if trade['status'] in ('WIN', 'LOSS'):
                profit = trade['exit_price'] - trade['entry_price']
                risk = trade['entry_price'] - df.iloc[idx]['sl_gap_h2']
                trade['rr'] = profit / risk if risk > 0 else 0
            results.append(trade)
        return results
    except:
        return []


def _worker_daily(code):
    return backtest_single(code, 'daily', 1500)

def _worker_weekly(code):
    return backtest_single(code, 'weekly', 800)


def print_report(trades, label):
    total = len(trades)
    if total == 0:
        print(f"\n  [{label}] 无信号。")
        return

    wins = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    inv = [t for t in trades if t['status'] == 'INVALIDATED']
    void = [t for t in trades if t['status'] == 'VOIDED']
    tout = [t for t in trades if t['status'] == 'TIMEOUT']
    hold = [t for t in trades if t['status'] in ('HOLDING', 'PENDING')]
    done = len(wins) + len(losses)

    print(f"\n{'='*60}")
    print(f"  Gap + H2 (两腿回调) 回测 ({label})")
    print(f"{'='*60}")
    print(f"  总信号数          : {total}")
    print(f"  [生命周期过滤]")
    print(f"    缺口回填撤单    : {len(inv)}")
    print(f"    止盈先达作废    : {len(void)}")
    print(f"    超时失效        : {len(tout)}")
    print(f"  持仓中/待触发     : {len(hold)}")
    print(f"  已结案交易        : {done}")
    print(f"{'-'*60}")

    if done > 0:
        wr = len(wins) / done * 100
        avg_w = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_l = np.mean([t['rr'] for t in losses]) if losses else 0
        ev = wr / 100 * avg_w + (1 - wr / 100) * avg_l
        total_r = sum(t.get('rr', 0) for t in wins) + sum(t.get('rr', 0) for t in losses)

        print(f"  胜率              : {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
        print(f"  获利单平均 R      : +{avg_w:.3f}")
        print(f"  亏损单平均 R      : {avg_l:.3f}")
        print(f"  累计净 R          : {total_r:+.2f}")
        print(f"  {'='*44}")
        print(f"  单笔数学期望 EV   : {ev:+.4f} R/单")
        print(f"  {'='*44}")

        if done >= 5:
            df_r = pd.DataFrame([t for t in trades if t['status'] in ('WIN', 'LOSS')])
            df_r['is_win'] = df_r['status'] == 'WIN'

            # 按年份
            if 'signal_date' in df_r.columns:
                df_r['year'] = df_r['signal_date'].str[:4]
                print(f"\n  [年份分布]")
                for yr, g in df_r.groupby('year'):
                    w = g['is_win'].mean() * 100
                    r = g['rr'].mean()
                    print(f"    {yr}: n={len(g):>3d}  WR={w:5.1f}%  avg_R={r:+.3f}")

            # 回调周期
            if 'pb_bars' in df_r.columns:
                df_r['pb_tier'] = pd.cut(df_r['pb_bars'], bins=[-np.inf, 5, 10, 20, np.inf],
                                         labels=['<=5', '6-10', '11-20', '>20'])
                print(f"\n  [回调周期]")
                for tier, g in df_r.groupby('pb_tier', observed=True):
                    if len(g) > 0:
                        print(f"    {tier:>10s}: n={len(g):>3d}  WR={g['is_win'].mean()*100:5.1f}%  avg_R={g['rr'].mean():+.3f}")
    print(f"{'='*60}")


def run(limit=0):
    codes = get_stock_list()
    if not codes:
        print("[!] 无法获取股票列表")
        return
    if limit > 0:
        codes = codes[:limit]
    n = len(codes)
    print(f"[*] 扫描 {n} 只标的")
    print(f"[*] 策略: Gap + H2 (LHLL -> HH -> LHLL)")
    print(f"[*] TP = 2*GapFloor - PriorSwingLow | 生命周期: 3条全保留")

    # 日线
    print(f"\n[Phase 1/2] 日线回测...")
    dt = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as exe:
        futs = {exe.submit(_worker_daily, c): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [daily] {done}/{n}", flush=True)
            r = f.result()
            if r:
                dt.extend(r)
    print(f"  日线完成, 耗时 {time.time()-t0:.1f}s, 信号 {len(dt)} 笔")
    print_report(dt, "日线")

    # 周线
    print(f"\n[Phase 2/2] 周线回测...")
    wt = []
    t1 = time.time()
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as exe:
        futs = {exe.submit(_worker_weekly, c): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [weekly] {done}/{n}", flush=True)
            r = f.result()
            if r:
                wt.extend(r)
    print(f"  周线完成, 耗时 {time.time()-t1:.1f}s, 信号 {len(wt)} 笔")
    print_report(wt, "周线")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=0)
    run(limit=p.parse_args().limit)
