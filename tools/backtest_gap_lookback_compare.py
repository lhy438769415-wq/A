# -*- coding: utf-8 -*-
"""
[Strategy Lab] Gap 策略 LOOKBACK_WINDOW 对比回测 (60 vs 100)

目的: 对比将突破规模判定窗口从默认的 60 根 K线 调整到 100 根后，
      Structural Gap 策略在全市场日线数据上的表现差异。

方法:
  1. 先用 LOOKBACK_WINDOW=60 (默认) 全量回测
  2. 再用 LOOKBACK_WINDOW=100 全量回测
  3. 对比胜率、EV、信号数量、盈亏比等关键指标

用法:
  python tools/backtest_gap_lookback_compare.py [--limit N] [--bars N]
"""

import os, sys, io, time, json, argparse

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


def evaluate_trade(df: pd.DataFrame, signal_idx: int) -> dict:
    """
    模拟由突破单触发后的后续走势，判定先打止损还是先打止盈。
    复用 backtest_struct_gap.py 的核心逻辑。
    """
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_struct_gap']
        sl_price = sig_row['sl_struct_gap']
        tp_price = sig_row['tp_struct_gap']

        if pd.isna(entry_price) or pd.isna(sl_price) or pd.isna(tp_price):
            return {'status': 'ERROR', 'reason': 'NaN价格参数'}

        # 提取回调 PA 特征
        pb_df = pd.DataFrame()
        if 'bars_since_breakout' in df.columns:
            breakout_indices = df.index[df['is_breakout'] == True]
            valid_bo = breakout_indices[breakout_indices <= df.index[signal_idx]]
            if len(valid_bo) > 0:
                pb_start_idx = valid_bo[-1]
                loc_start = df.index.get_loc(pb_start_idx)
                if loc_start < signal_idx:
                    pb_df = df.iloc[loc_start:signal_idx]

        # 回调特征
        pb_bars = signal_idx - df.index.get_loc(pb_df.index[0]) if not pb_df.empty else 0
        max_consec_bear = 0
        if not pb_df.empty:
            is_bear = pb_df['close'] < pb_df['open']
            consec_bear = is_bear.groupby((~is_bear).cumsum()).sum()
            max_consec_bear = int(consec_bear.max()) if not consec_bear.empty else 0

        # 缺口宽度
        gap_top = sig_row.get('struct_gap_top_exact', entry_price)
        if pd.isna(gap_top):
            gap_top = entry_price
        gap_size_pct = round((gap_top - sl_price) / sl_price * 100, 2) if sl_price > 0 else 0

        sig_quality = sig_row.get('sig_bar_quality', 0)

        # 回测主体
        post_signal = df.iloc[signal_idx + 1:]
        if post_signal.empty:
            return {'status': 'PENDING', 'reason': '数据结尾'}

        trade_status = 'WAITING_TRIGGER'
        actual_entry = entry_price
        entry_date = None

        for i, row in post_signal.iterrows():
            if trade_status == 'WAITING_TRIGGER':
                if row['low'] < sl_price:
                    return {'status': 'INVALIDATED', 'reason': '未入场即跌穿'}
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row.get('date', i)
                    trade_status = 'IN_TRADE'
                    if row['low'] <= sl_price:
                        return _build_result('LOSS', actual_entry, sl_price, entry_date, entry_date,
                                             sig_quality, pb_bars, max_consec_bear, gap_size_pct)
                    if row['high'] >= tp_price:
                        return _build_result('WIN', actual_entry, tp_price, entry_date, entry_date,
                                             sig_quality, pb_bars, max_consec_bear, gap_size_pct)
            elif trade_status == 'IN_TRADE':
                exit_date = row.get('date', i)
                if row['low'] <= sl_price:
                    actual_exit = min(sl_price, row['open'])
                    return _build_result('LOSS', actual_entry, actual_exit, entry_date, exit_date,
                                         sig_quality, pb_bars, max_consec_bear, gap_size_pct)
                if row['high'] >= tp_price:
                    return _build_result('WIN', actual_entry, tp_price, entry_date, exit_date,
                                         sig_quality, pb_bars, max_consec_bear, gap_size_pct)

        if trade_status == 'IN_TRADE':
            return {'status': 'HOLDING', 'entry_price': actual_entry, 'reason': '持仓中',
                    'sig_quality': sig_quality, 'pb_bars': pb_bars,
                    'pb_consec_bear': max_consec_bear, 'gap_size_pct': gap_size_pct}
        return {'status': 'PENDING', 'reason': '未触发'}
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}


def _build_result(status, entry_price, exit_price, entry_date, exit_date,
                  sig_quality, pb_bars, pb_consec_bear, gap_size_pct):
    """构建标准化交易结果"""
    risk = entry_price - exit_price if status == 'LOSS' else 0
    return {
        'status': status,
        'entry_price': entry_price,
        'exit_price': exit_price,
        'entry_date': entry_date,
        'exit_date': exit_date,
        'profit': exit_price - entry_price,
        'rr': (exit_price - entry_price) / (entry_price - exit_price) if status == 'LOSS' and entry_price != exit_price else
              (exit_price - entry_price) / max(entry_price * 0.01, 1) if status == 'WIN' else 0,
        'sig_quality': sig_quality,
        'pb_bars': pb_bars,
        'pb_consec_bear': pb_consec_bear,
        'gap_size_pct': gap_size_pct
    }


def backtest_single_stock_with_lookback(args_tuple) -> list:
    """
    用指定的 LOOKBACK_WINDOW 回测单只股票。
    args_tuple = (code, bars_limit, lookback_window)
    """
    code, bars_limit, lookback_window = args_tuple

    try:
        # 动态导入，以确保多进程安全
        from core.data_provider import get_stock_data
        from core.calculator import add_indicators
        from core.strategies.structural_gap_strategy import StructuralGapStrategy

        df = get_stock_data(code, limit=bars_limit)
        if df is None or len(df) < max(lookback_window + 50, 150):
            return []

        df = add_indicators(df)

        # 创建策略实例并覆盖 LOOKBACK_WINDOW
        strategy = StructuralGapStrategy()
        strategy.LOOKBACK_WINDOW = lookback_window
        df = strategy.calculate_signals(df)

        # 提取信号
        if 'signal_struct_gap_confirm' not in df.columns:
            return []
        signal_indices = [i for i, val in enumerate(df['signal_struct_gap_confirm']) if val]

        results = []
        for idx in signal_indices:
            trade_res = evaluate_trade(df, idx)
            if trade_res['status'] in ['ERROR', 'SKIP', 'FILTERED']:
                continue

            sig_date = df.iloc[idx].get('date', df.index[idx])
            if hasattr(sig_date, 'strftime'):
                sig_date = sig_date.strftime('%Y-%m-%d')
            trade_res['code'] = code
            trade_res['signal_date'] = str(sig_date)
            trade_res['lookback'] = lookback_window

            # 计算精确 R:R
            if trade_res['status'] in ['WIN', 'LOSS']:
                entry_p = trade_res['entry_price']
                sl_p = df.iloc[idx]['sl_struct_gap']
                risk = entry_p - sl_p
                if risk > 0:
                    trade_res['rr'] = (trade_res['exit_price'] - entry_p) / risk
                else:
                    trade_res['rr'] = 0

            results.append(trade_res)

        return results
    except Exception as e:
        return []


def run_comparison_backtest(limit=0, bars_limit=1500):
    """运行 60 vs 100 对比回测"""
    logger.info("🚀 初始化 Structural Gap LOOKBACK 对比回测引擎...")
    all_codes = get_stock_list()
    if not all_codes:
        logger.error("无法获取股票列表")
        return

    if limit > 0:
        all_codes = all_codes[:limit]

    logger.info(f"📊 准备扫描 {len(all_codes)} 只标的 (回溯约 {bars_limit/250:.1f} 年)")

    results_all = {}

    for lookback in [60, 100]:
        logger.info(f"\n{'='*60}")
        logger.info(f"🔬 开始 LOOKBACK_WINDOW = {lookback} 回测...")
        logger.info(f"{'='*60}")

        all_trades = []
        start_time = time.time()

        # 构建多进程参数
        task_args = [(code, bars_limit, lookback) for code in all_codes]
        workers = min(settings.MAX_WORKERS, 6)

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(backtest_single_stock_with_lookback, args): args[0]
                       for args in task_args}

            completed = 0
            for future in as_completed(futures):
                completed += 1
                if completed % 200 == 0:
                    print(f"  ⏳ [LB={lookback}] 进度: {completed}/{len(all_codes)}", end='\r')
                try:
                    trades = future.result()
                    if trades:
                        all_trades.extend(trades)
                except Exception as e:
                    pass

        elapsed = time.time() - start_time
        logger.info(f"  ✅ LB={lookback} 完成! 耗时 {elapsed:.1f}s, 信号总数: {len(all_trades)}")
        results_all[lookback] = all_trades

    # ====== 对比分析 ======
    print_comparison_report(results_all, len(all_codes), bars_limit)

    # 保存原始数据到 JSON
    output_path = os.path.join(project_root, 'strategy_lab', 'gap_lookback_compare_results.json')
    save_data = {}
    for lb, trades in results_all.items():
        save_data[str(lb)] = [
            {k: (str(v) if hasattr(v, 'strftime') else v) for k, v in t.items()}
            for t in trades if t['status'] in ['WIN', 'LOSS']
        ]
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"📦 原始数据已保存: {output_path}")

    return results_all


def print_comparison_report(results_all: dict, total_stocks: int, bars_limit: int):
    """打印两组回测的对比报告"""
    print("\n" + "=" * 70)
    print("📊 Structural Gap 策略 LOOKBACK_WINDOW 对比回测报告")
    print("=" * 70)
    print(f"• 扫描标的: {total_stocks} 只 | 回溯: ~{bars_limit / 250:.1f} 年")
    print(f"• 对比参数: LOOKBACK_WINDOW = 60 (默认) vs 100 (实验)")
    print("=" * 70)

    metrics = {}

    for lookback in [60, 100]:
        trades = results_all.get(lookback, [])
        wins = [t for t in trades if t['status'] == 'WIN']
        losses = [t for t in trades if t['status'] == 'LOSS']
        invalidated = [t for t in trades if t['status'] == 'INVALIDATED']
        holding = [t for t in trades if t['status'] == 'HOLDING']
        pending = [t for t in trades if t['status'] == 'PENDING']

        completed = len(wins) + len(losses)
        total = len(trades)

        win_rate = len(wins) / completed * 100 if completed > 0 else 0
        avg_rr_win = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_rr_loss = np.mean([t['rr'] for t in losses]) if losses else 0
        ev = (win_rate / 100 * avg_rr_win + (1 - win_rate / 100) * avg_rr_loss) if completed > 0 else 0
        total_r = sum(t['rr'] for t in wins) + sum(t['rr'] for t in losses) if completed > 0 else 0

        # 缺口宽度统计
        gap_sizes = [t.get('gap_size_pct', 0) for t in trades if t['status'] in ['WIN', 'LOSS'] and t.get('gap_size_pct', 0) > 0]
        avg_gap_size = np.mean(gap_sizes) if gap_sizes else 0

        # 回调周期统计
        pb_bars_list = [t.get('pb_bars', 0) for t in trades if t['status'] in ['WIN', 'LOSS'] and t.get('pb_bars', 0) > 0]
        avg_pb_bars = np.mean(pb_bars_list) if pb_bars_list else 0

        metrics[lookback] = {
            'total_signals': total,
            'completed': completed,
            'wins': len(wins),
            'losses': len(losses),
            'invalidated': len(invalidated),
            'holding': len(holding),
            'pending': len(pending),
            'win_rate': win_rate,
            'avg_rr_win': avg_rr_win,
            'avg_rr_loss': avg_rr_loss,
            'ev': ev,
            'total_r': total_r,
            'avg_gap_size': avg_gap_size,
            'avg_pb_bars': avg_pb_bars
        }

    # 打印对比表
    print(f"\n{'指标':<30s} {'LB=60 (默认)':<20s} {'LB=100 (实验)':<20s} {'变化':>10s}")
    print("-" * 80)

    def _row(label, key, fmt=".2f", pct=False):
        v60 = metrics[60][key]
        v100 = metrics[100][key]
        suffix = "%" if pct else ""
        delta = v100 - v60
        delta_str = f"{delta:+{fmt}}{suffix}" if isinstance(v60, float) else f"{delta:+d}"
        print(f"  {label:<28s} {v60:<20{fmt}}{suffix}  {v100:<20{fmt}}{suffix}  {delta_str:>10s}")

    def _row_int(label, key):
        v60 = metrics[60][key]
        v100 = metrics[100][key]
        delta = v100 - v60
        print(f"  {label:<28s} {v60:<20d}  {v100:<20d}  {delta:>+10d}")

    _row_int("总信号数", "total_signals")
    _row_int("已结案交易", "completed")
    _row_int("✅ 盈利笔数", "wins")
    _row_int("❌ 亏损笔数", "losses")
    _row_int("🚫 未入场作废", "invalidated")
    print("-" * 80)
    _row("胜率 (%)", "win_rate", ".2f", True)
    _row("获利单平均 R", "avg_rr_win", ".3f")
    _row("亏损单平均 R", "avg_rr_loss", ".3f")
    _row("单笔 EV (R)", "ev", ".4f")
    _row("累计净 R", "total_r", ".2f")
    print("-" * 80)
    _row("平均缺口宽度 (%)", "avg_gap_size", ".2f", True)
    _row("平均回调周期 (根)", "avg_pb_bars", ".1f")

    # 结论
    print("\n" + "=" * 70)
    print("📋 对比结论:")
    print("=" * 70)

    ev_60 = metrics[60]['ev']
    ev_100 = metrics[100]['ev']
    wr_60 = metrics[60]['win_rate']
    wr_100 = metrics[100]['win_rate']
    sig_60 = metrics[60]['total_signals']
    sig_100 = metrics[100]['total_signals']

    # 信号数量变化
    sig_delta_pct = (sig_100 - sig_60) / sig_60 * 100 if sig_60 > 0 else 0
    print(f"  1. 信号数量: LB=100 比 LB=60 {'减少' if sig_delta_pct < 0 else '增加'} {abs(sig_delta_pct):.1f}%")
    print(f"     (更高的突破门槛 → {'更少但更精的信号' if sig_delta_pct < 0 else '意外多了更多信号'})")

    # 胜率变化
    wr_delta = wr_100 - wr_60
    print(f"  2. 胜率: {'提升' if wr_delta > 0 else '下降'} {abs(wr_delta):.2f} 个百分点 ({wr_60:.2f}% → {wr_100:.2f}%)")

    # EV 变化
    ev_delta = ev_100 - ev_60
    print(f"  3. 数学期望(EV): {'提升' if ev_delta > 0 else '下降'} {abs(ev_delta):.4f} R/单 ({ev_60:.4f} → {ev_100:.4f})")

    # 综合判断
    if ev_100 > ev_60 and wr_100 > wr_60:
        verdict = "🌟 LB=100 全面优于 LB=60，建议升级！"
    elif ev_100 > ev_60:
        verdict = "👍 LB=100 的 EV 更优，但胜率略降，可考虑升级"
    elif wr_100 > wr_60:
        verdict = "👍 LB=100 胜率更高，但 EV 略降（可能盈亏比下降）"
    else:
        verdict = "⚠️ LB=100 在 EV 和胜率上均不及 LB=60，建议保持默认"

    print(f"\n  ✅ 综合判断: {verdict}")
    print("=" * 70)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gap 策略 LOOKBACK 对比回测')
    parser.add_argument('--limit', type=int, default=0, help='限制扫描股票数量 (0=全量)')
    parser.add_argument('--bars', type=int, default=1500, help='每只股票回溯的K线根数')
    parser.add_argument('--output', type=str, default='', help='输出报告文件路径')
    args = parser.parse_args()

    # 如果指定了输出文件，同时写入终端和文件
    report_file = args.output or os.path.join(project_root, 'strategy_lab', 'gap_lookback_compare_report.txt')

    # 使用 Tee 模式：同时输出终端和文件
    class TeeWriter:
        def __init__(self, terminal, filepath):
            self.terminal = terminal
            self.file = open(filepath, 'w', encoding='utf-8')
        def write(self, msg):
            try:
                self.terminal.write(msg)
            except:
                pass
            self.file.write(msg)
        def flush(self):
            try:
                self.terminal.flush()
            except:
                pass
            self.file.flush()
        def close(self):
            self.file.close()

    tee = TeeWriter(sys.stdout, report_file)
    sys.stdout = tee

    run_comparison_backtest(limit=args.limit, bars_limit=args.bars)

    tee.close()
    # 恢复 stdout
    sys.stdout = tee.terminal
    print(f"\n[Report saved to: {report_file}]")

