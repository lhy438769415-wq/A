# -*- coding: utf-8 -*-
"""
[Strategy Lab] Gap 策略 LOOKBACK_WINDOW 多参数扫描回测

目的: 在 30~120 范围内扫描最优 LOOKBACK_WINDOW，
      并提取每个参数下的深度 PA 特征分布，
      用于分析突破窗口大小与策略表现的关系。

用法:
  python tools/backtest_gap_lookback_sweep.py [--limit N] [--bars N]
"""

import os, sys, io, time, json, argparse
import warnings
warnings.filterwarnings('ignore')

# Windows 终端 UTF-8 编码修复
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

# 确保项目根目录在 sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from config import settings
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def evaluate_trade(df, signal_idx):
    """回测单笔交易，返回状态和盈亏"""
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_struct_gap']
        sl_price = sig_row['sl_struct_gap']
        tp_price = sig_row['tp_struct_gap']

        if pd.isna(entry_price) or pd.isna(sl_price) or pd.isna(tp_price):
            return None

        # 提取回调期 PA 特征
        pb_bars = 0
        max_consec_bear = 0
        gap_size_pct = 0
        sig_quality = sig_row.get('sig_bar_quality', 0)

        # 缺口宽度
        gap_top = sig_row.get('struct_gap_top_exact', entry_price)
        if pd.isna(gap_top):
            gap_top = entry_price
        gap_size_pct = round((gap_top - sl_price) / sl_price * 100, 2) if sl_price > 0 else 0

        # 回调周期
        if 'bars_since_breakout' in df.columns:
            breakout_indices = df.index[df['is_breakout'] == True]
            valid_bo = breakout_indices[breakout_indices <= df.index[signal_idx]]
            if len(valid_bo) > 0:
                pb_start_loc = df.index.get_loc(valid_bo[-1])
                if pb_start_loc < signal_idx:
                    pb_bars = signal_idx - pb_start_loc
                    pb_df = df.iloc[pb_start_loc:signal_idx]
                    if not pb_df.empty:
                        is_bear = pb_df['close'] < pb_df['open']
                        consec = is_bear.groupby((~is_bear).cumsum()).sum()
                        max_consec_bear = int(consec.max()) if not consec.empty else 0

        # 突破日的涨幅 (突破力度)
        bo_magnitude = 0
        if len(valid_bo) > 0:
            bo_loc = df.index.get_loc(valid_bo[-1])
            bo_row = df.iloc[bo_loc]
            if bo_row['open'] > 0:
                bo_magnitude = (bo_row['close'] - bo_row['open']) / bo_row['open'] * 100

        # ATR 倍数 (突破K线的振幅 / ATR)
        bo_atr_ratio = 0
        if len(valid_bo) > 0:
            bo_loc = df.index.get_loc(valid_bo[-1])
            bo_row = df.iloc[bo_loc]
            if bo_row.get('atr', 0) > 0:
                bo_atr_ratio = (bo_row['high'] - bo_row['low']) / bo_row['atr']

        # 模拟交易
        post_signal = df.iloc[signal_idx + 1:]
        if post_signal.empty:
            return None

        trade_status = 'WAITING'
        actual_entry = entry_price

        for _, row in post_signal.iterrows():
            if trade_status == 'WAITING':
                if row['low'] < sl_price:
                    return {'status': 'INVALIDATED'}
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    trade_status = 'IN_TRADE'
                    if row['low'] <= sl_price:
                        risk = actual_entry - sl_price
                        return {'status': 'LOSS', 'rr': (sl_price - actual_entry) / risk if risk > 0 else -1,
                                'sig_quality': sig_quality, 'pb_bars': pb_bars,
                                'consec_bear': max_consec_bear, 'gap_size_pct': gap_size_pct,
                                'bo_magnitude': bo_magnitude, 'bo_atr_ratio': bo_atr_ratio}
                    if row['high'] >= tp_price:
                        risk = actual_entry - sl_price
                        return {'status': 'WIN', 'rr': (tp_price - actual_entry) / risk if risk > 0 else 0,
                                'sig_quality': sig_quality, 'pb_bars': pb_bars,
                                'consec_bear': max_consec_bear, 'gap_size_pct': gap_size_pct,
                                'bo_magnitude': bo_magnitude, 'bo_atr_ratio': bo_atr_ratio}
            elif trade_status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    risk = actual_entry - sl_price
                    return {'status': 'LOSS', 'rr': (min(sl_price, row['open']) - actual_entry) / risk if risk > 0 else -1,
                            'sig_quality': sig_quality, 'pb_bars': pb_bars,
                            'consec_bear': max_consec_bear, 'gap_size_pct': gap_size_pct,
                            'bo_magnitude': bo_magnitude, 'bo_atr_ratio': bo_atr_ratio}
                if row['high'] >= tp_price:
                    risk = actual_entry - sl_price
                    return {'status': 'WIN', 'rr': (tp_price - actual_entry) / risk if risk > 0 else 0,
                            'sig_quality': sig_quality, 'pb_bars': pb_bars,
                            'consec_bear': max_consec_bear, 'gap_size_pct': gap_size_pct,
                            'bo_magnitude': bo_magnitude, 'bo_atr_ratio': bo_atr_ratio}

        if trade_status == 'IN_TRADE':
            return {'status': 'HOLDING'}
        return None
    except:
        return None


def backtest_single_stock_sweep(args_tuple):
    """用指定的 lookback 回测单只股票"""
    code, bars_limit, lookback = args_tuple
    try:
        from core.data_provider import get_stock_data
        from core.calculator import add_indicators
        from core.strategies.structural_gap_strategy import StructuralGapStrategy

        df = get_stock_data(code, limit=bars_limit)
        if df is None or len(df) < max(lookback + 50, 150):
            return []

        df = add_indicators(df)
        strategy = StructuralGapStrategy()
        strategy.LOOKBACK_WINDOW = lookback
        df = strategy.calculate_signals(df)

        if 'signal_struct_gap_confirm' not in df.columns:
            return []

        signal_indices = [i for i, v in enumerate(df['signal_struct_gap_confirm']) if v]

        results = []
        for idx in signal_indices:
            res = evaluate_trade(df, idx)
            if res and res['status'] in ['WIN', 'LOSS']:
                res['lookback'] = lookback
                results.append(res)
        return results
    except:
        return []


def run_sweep(limit=0, bars_limit=1500):
    """运行多参数扫描"""
    all_codes = get_stock_list()
    if not all_codes:
        logger.error("无法获取股票列表")
        return

    if limit > 0:
        all_codes = all_codes[:limit]

    # 扫描范围: 30, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 120
    lookback_values = [30, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 120]

    logger.info(f"=== Gap LOOKBACK 多参数扫描 ===")
    logger.info(f"标的: {len(all_codes)} 只 | 范围: {lookback_values}")

    all_results = {}
    workers = min(settings.MAX_WORKERS, 6)

    for lb in lookback_values:
        start = time.time()
        task_args = [(code, bars_limit, lb) for code in all_codes]
        trades = []

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(backtest_single_stock_sweep, a): a[0] for a in task_args}
            done = 0
            for f in as_completed(futures):
                done += 1
                if done % 500 == 0:
                    print(f"  [LB={lb}] {done}/{len(all_codes)}", end='\r')
                try:
                    r = f.result()
                    if r:
                        trades.extend(r)
                except:
                    pass

        elapsed = time.time() - start
        all_results[lb] = trades
        wins = [t for t in trades if t['status'] == 'WIN']
        losses = [t for t in trades if t['status'] == 'LOSS']
        n = len(wins) + len(losses)
        wr = len(wins) / n * 100 if n > 0 else 0
        avg_w = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_l = np.mean([t['rr'] for t in losses]) if losses else 0
        ev = wr/100 * avg_w + (1-wr/100) * avg_l if n > 0 else 0
        total_r = sum(t['rr'] for t in trades)

        logger.info(f"  LB={lb:>3d} | 信号={n:>5d} | 胜率={wr:>6.2f}% | 平均W={avg_w:>+.3f}R | 平均L={avg_l:>+.3f}R | EV={ev:>+.4f}R | 累计={total_r:>+.1f}R | {elapsed:.1f}s")

    # === 生成详细报告 ===
    report_path = os.path.join(project_root, 'strategy_lab', 'gap_lookback_sweep_report.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("=" * 90 + "\n")
        f.write("Gap 策略 LOOKBACK_WINDOW 多参数扫描报告\n")
        f.write(f"扫描标的: {len(all_codes)} 只 | 回溯: ~{bars_limit/250:.1f} 年\n")
        f.write("=" * 90 + "\n\n")

        # 主表
        f.write(f"{'LB':>5s} | {'信号数':>6s} | {'胜率':>8s} | {'平均W(R)':>10s} | {'平均L(R)':>10s} | {'EV(R/单)':>10s} | {'累计R':>10s} | {'平均缺口%':>8s} | {'平均回调':>6s} | {'突破涨幅%':>8s}\n")
        f.write("-" * 90 + "\n")

        best_ev = -999
        best_lb = 60

        for lb in lookback_values:
            trades = all_results[lb]
            wins = [t for t in trades if t['status'] == 'WIN']
            losses = [t for t in trades if t['status'] == 'LOSS']
            n = len(wins) + len(losses)
            wr = len(wins) / n * 100 if n > 0 else 0
            avg_w = np.mean([t['rr'] for t in wins]) if wins else 0
            avg_l = np.mean([t['rr'] for t in losses]) if losses else 0
            ev = wr/100 * avg_w + (1-wr/100) * avg_l if n > 0 else 0
            total_r = sum(t['rr'] for t in trades)

            # PA 特征均值
            gap_sizes = [t.get('gap_size_pct', 0) for t in trades if t.get('gap_size_pct', 0) > 0]
            pb_bars_list = [t.get('pb_bars', 0) for t in trades if t.get('pb_bars', 0) > 0]
            bo_mags = [t.get('bo_magnitude', 0) for t in trades if t.get('bo_magnitude', 0) > 0]

            avg_gap = np.mean(gap_sizes) if gap_sizes else 0
            avg_pb = np.mean(pb_bars_list) if pb_bars_list else 0
            avg_bo = np.mean(bo_mags) if bo_mags else 0

            marker = " ◀ BEST" if ev > best_ev else ""
            if ev > best_ev:
                best_ev = ev
                best_lb = lb

            f.write(f"{lb:>5d} | {n:>6d} | {wr:>7.2f}% | {avg_w:>+10.3f} | {avg_l:>+10.3f} | {ev:>+10.4f} | {total_r:>+10.1f} | {avg_gap:>8.2f} | {avg_pb:>6.1f} | {avg_bo:>8.2f}{marker}\n")

        f.write("-" * 90 + "\n")
        f.write(f"\n最优 LOOKBACK = {best_lb} (EV = {best_ev:+.4f} R/单)\n")

        # === 深度 PA 特征分析 ===
        f.write("\n\n" + "=" * 90 + "\n")
        f.write("深度 PA 特征分析: 各 LB 下胜/负样本的微观结构差异\n")
        f.write("=" * 90 + "\n\n")

        for lb in lookback_values:
            trades = all_results[lb]
            wins = [t for t in trades if t['status'] == 'WIN']
            losses = [t for t in trades if t['status'] == 'LOSS']
            if not wins or not losses:
                continue

            f.write(f"\n--- LB = {lb} ---\n")

            # 信号 K 线质量
            w_sq = [t.get('sig_quality', 0) for t in wins]
            l_sq = [t.get('sig_quality', 0) for t in losses]
            f.write(f"  信号K线质量: WIN={np.mean(w_sq):.3f} vs LOSS={np.mean(l_sq):.3f} (差={np.mean(w_sq)-np.mean(l_sq):+.3f})\n")

            # 回调周期
            w_pb = [t.get('pb_bars', 0) for t in wins]
            l_pb = [t.get('pb_bars', 0) for t in losses]
            f.write(f"  回调周期: WIN={np.mean(w_pb):.1f} vs LOSS={np.mean(l_pb):.1f} (差={np.mean(w_pb)-np.mean(l_pb):+.1f})\n")

            # 连阴数
            w_cb = [t.get('consec_bear', 0) for t in wins]
            l_cb = [t.get('consec_bear', 0) for t in losses]
            f.write(f"  连续阴线: WIN={np.mean(w_cb):.1f} vs LOSS={np.mean(l_cb):.1f} (差={np.mean(w_cb)-np.mean(l_cb):+.1f})\n")

            # 缺口宽度
            w_gs = [t.get('gap_size_pct', 0) for t in wins]
            l_gs = [t.get('gap_size_pct', 0) for t in losses]
            f.write(f"  缺口宽度: WIN={np.mean(w_gs):.2f}% vs LOSS={np.mean(l_gs):.2f}% (差={np.mean(w_gs)-np.mean(l_gs):+.2f}%)\n")

            # 突破涨幅
            w_bm = [t.get('bo_magnitude', 0) for t in wins]
            l_bm = [t.get('bo_magnitude', 0) for t in losses]
            f.write(f"  突破涨幅: WIN={np.mean(w_bm):.2f}% vs LOSS={np.mean(l_bm):.2f}% (差={np.mean(w_bm)-np.mean(l_bm):+.2f}%)\n")

            # 突破 ATR 倍数
            w_ar = [t.get('bo_atr_ratio', 0) for t in wins]
            l_ar = [t.get('bo_atr_ratio', 0) for t in losses]
            f.write(f"  突破ATR倍数: WIN={np.mean(w_ar):.2f}x vs LOSS={np.mean(l_ar):.2f}x (差={np.mean(w_ar)-np.mean(l_ar):+.2f}x)\n")

    logger.info(f"\n报告已保存: {report_path}")

    # === 保存 JSON ===
    json_path = os.path.join(project_root, 'strategy_lab', 'gap_lookback_sweep_data.json')
    summary = {}
    for lb in lookback_values:
        trades = all_results[lb]
        wins = [t for t in trades if t['status'] == 'WIN']
        losses = [t for t in trades if t['status'] == 'LOSS']
        n = len(wins) + len(losses)
        wr = len(wins) / n * 100 if n > 0 else 0
        avg_w = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_l = np.mean([t['rr'] for t in losses]) if losses else 0
        ev = wr/100 * avg_w + (1-wr/100) * avg_l if n > 0 else 0

        summary[str(lb)] = {
            'signals': n, 'wins': len(wins), 'losses': len(losses),
            'win_rate': round(wr, 2), 'avg_win_r': round(float(avg_w), 4),
            'avg_loss_r': round(float(avg_l), 4), 'ev': round(float(ev), 4),
            'total_r': round(float(sum(t['rr'] for t in trades)), 2)
        }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info(f"数据已保存: {json_path}")

    return all_results, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gap LOOKBACK 多参数扫描')
    parser.add_argument('--limit', type=int, default=0, help='限制股票数量 (0=全量)')
    parser.add_argument('--bars', type=int, default=1500, help='回溯K线数')
    args = parser.parse_args()

    run_sweep(limit=args.limit, bars_limit=args.bars)
