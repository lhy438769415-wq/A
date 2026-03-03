# tools/recent_3k_report.py
"""
3K策略近期信号汇总 + 全市场缺口测试盈亏统计
功能:
  1. 从数据库中实时扫描全市场最近的3K信号和缺口测试确认信号
  2. 整理成表格输出
  3. 统计全市场盈亏比分布
"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
logging.basicConfig(level=logging.WARNING)

from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
from tools.backtest_3k_002046 import score_3k_signal, score_gap_test


def scan_recent_signals(lookback_days: int = 60, limit_stocks: int = None):
    """
    扫描全市场最近N天内的3K信号和缺口测试确认信号
    """
    all_codes = get_stock_list()
    if not all_codes:
        print("❌ 数据库为空")
        return [], []

    if limit_stocks:
        all_codes = all_codes[:limit_stocks]

    strategy = ThreeKStrategy()
    sig_3k_list = []
    gap_test_list = []
    total = len(all_codes)

    for i, code in enumerate(all_codes):
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{total}...")
        try:
            df = get_stock_data(code, limit=300)
            if df is None or len(df) < 100:
                continue

            df = add_indicators(df)
            df = strategy.calculate_signals(df)

            # 只看最近 lookback_days 根K线
            recent = df.tail(lookback_days)

            # 3K信号
            sigs = recent[recent['signal_3k'] == True]
            for idx, row in sigs.iterrows():
                iloc_pos = df.index.get_loc(idx)
                score = score_3k_signal(df, iloc_pos)
                k1_high = df.iloc[iloc_pos - 2]['high'] if iloc_pos >= 2 else 0

                sig_3k_list.append({
                    '代码': code,
                    '日期': str(row['date']),
                    'Close': round(row['close'], 2),
                    '3K评分': score['total'],
                    'K1_High': round(k1_high, 2),
                })

            # 缺口测试确认信号
            gt_col = 'signal_3k_gap_test'
            if gt_col in recent.columns:
                gts = recent[recent[gt_col] == True]
                for idx, row in gts.iterrows():
                    iloc_pos = df.index.get_loc(idx)
                    entry = row.get('entry_3k_gap_test', np.nan)
                    sl = row.get('sl_3k_gap_test', np.nan)
                    tp = row.get('tp_3k_gap_test', np.nan)

                    risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                    reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
                    rr = reward / risk if risk > 0 else 0

                    # 后续走势判定 (最多看20根)
                    future = df.iloc[iloc_pos + 1: iloc_pos + 21]
                    outcome = 'N/A'
                    if len(future) > 0 and not np.isnan(entry) and not np.isnan(sl):
                        for _, fb in future.iterrows():
                            if fb['low'] <= sl:
                                outcome = '止损 ❌'
                                break
                            if not np.isnan(tp) and fb['high'] >= tp:
                                outcome = '止盈 ✅'
                                break
                        if outcome == 'N/A' and len(future) > 0:
                            last_close = future.iloc[-1]['close']
                            pnl_pct = (last_close - entry) / entry * 100
                            outcome = f'持有 {pnl_pct:+.1f}%'

                    # 回调评分
                    gt_score = score_gap_test(df, iloc_pos, k1_high if 'k1_high' in dir() else 0)

                    gap_test_list.append({
                        '代码': code,
                        '日期': str(row['date']),
                        'Entry': round(entry, 2) if not np.isnan(entry) else 'N/A',
                        'SL': round(sl, 2) if not np.isnan(sl) else 'N/A',
                        'TP': round(tp, 2) if not np.isnan(tp) else 'N/A',
                        'R:R': f'1:{rr:.1f}' if rr > 0 else 'N/A',
                        '回调评分': gt_score['total'],
                        '结果': outcome,
                    })

        except Exception as e:
            continue

    return sig_3k_list, gap_test_list


def print_report(sig_3k_list, gap_test_list, lookback_days):
    """打印格式化报告"""
    print("=" * 90)
    print(f"  3K 策略近期信号汇总 (最近 {lookback_days} 个交易日)")
    print("=" * 90)

    # === 3K信号 ===
    if sig_3k_list:
        sig_df = pd.DataFrame(sig_3k_list)
        sig_df = sig_df.sort_values('日期', ascending=False)
        print(f"\n📌 3K信号 (共 {len(sig_df)} 个):")
        print("-" * 90)
        pd.set_option('display.float_format', '{:.2f}'.format)
        print(sig_df.to_string(index=False))
    else:
        print("\n📌 最近无3K信号")

    # === 缺口测试确认信号 ===
    if gap_test_list:
        gt_df = pd.DataFrame(gap_test_list)
        gt_df = gt_df.sort_values('日期', ascending=False)
        print(f"\n📌 缺口测试确认信号 (共 {len(gt_df)} 个):")
        print("-" * 90)
        print(gt_df.to_string(index=False))

        # 胜率统计
        wins = gt_df['结果'].str.contains('止盈').sum()
        losses = gt_df['结果'].str.contains('止损').sum()
        holds = gt_df['结果'].str.contains('持有').sum()
        total_decided = wins + losses
        win_rate = wins / total_decided * 100 if total_decided > 0 else 0

        print(f"\n📊 盈亏统计:")
        print(f"  止盈: {wins}  |  止损: {losses}  |  持有中: {holds}  |  未知: {len(gt_df)-wins-losses-holds}")
        if total_decided > 0:
            print(f"  胜率: {win_rate:.0f}% ({wins}/{total_decided})")
    else:
        print("\n📌 最近无缺口测试确认信号")

    print("\n" + "=" * 90)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='3K策略近期信号汇总')
    parser.add_argument('--days', type=int, default=60, help='回溯交易日数 (默认60)')
    parser.add_argument('--limit', type=int, default=None, help='限制扫描股票数量')
    args = parser.parse_args()

    print(f"🚀 扫描全市场最近 {args.days} 个交易日的3K信号...\n")
    sig_3k, gap_tests = scan_recent_signals(lookback_days=args.days, limit_stocks=args.limit)
    print_report(sig_3k, gap_tests, args.days)
