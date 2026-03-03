# tools/backtest_3k_002046.py
"""
3K策略全量回测 - 国机精工 sz.002046
功能:
  1. 统计所有3K信号和缺口测试确认信号
  2. 多维度评分 (参考MTR评分体系)
  3. 回调过程多空力量分析
  4. 胜率/盈亏比统计
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import logging
logging.basicConfig(level=logging.WARNING)

from core.data_provider import get_stock_data
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy


# ===== 3K 多维度评分 (100分满分, 参考MTR五维度体系) =====
def score_3k_signal(df: pd.DataFrame, sig_idx: int) -> dict:
    """
    对单个3K信号进行多维度评分

    维度1: 3K强度 (30分) — K线实体、缺口大小、通道角度
    维度2: 突破质量 (20分) — 是否突破前期高点、成交放量
    维度3: 缺口规模 (15分) — K1-K3之间缺口的ATR占比
    维度4: 位置评估 (20分) — 在区间中的位置(底部加分)
    维度5: 趋势基础 (15分) — EMA斜率、趋势深度
    """
    scores = {}
    k1_idx = sig_idx - 2
    k2_idx = sig_idx - 1
    k3_idx = sig_idx

    if k1_idx < 0 or k3_idx >= len(df):
        return {'total': 0, 'details': {}}

    k1, k2, k3 = df.iloc[k1_idx], df.iloc[k2_idx], df.iloc[k3_idx]
    atr = k3.get('atr', 1.0)
    if atr == 0:
        atr = 1.0

    # --- 维度1: 3K强度 (30分) ---
    # 平均实体率 (0~15分)
    avg_body = np.mean([k1.get('body_pct', 0), k2.get('body_pct', 0), k3.get('body_pct', 0)])
    body_score = min(avg_body / 0.8 * 15, 15)

    # 平均K线高度/ATR (0~10分)
    heights = [(k1['high'] - k1['low']) / atr, (k2['high'] - k2['low']) / atr, (k3['high'] - k3['low']) / atr]
    avg_height = np.mean(heights)
    height_score = min(avg_height / 2.0 * 10, 10)

    # 上影线惩罚 (0~5分, 影线越小越好)
    avg_upper_wick = np.mean([k1.get('upper_wick_pct', 0), k2.get('upper_wick_pct', 0), k3.get('upper_wick_pct', 0)])
    wick_score = max(5 - avg_upper_wick / 0.2 * 5, 0)

    scores['3K强度'] = round(body_score + height_score + wick_score, 1)

    # --- 维度2: 突破质量 (20分) ---
    # 是否突破前期高点
    lookback = max(0, k1_idx - 40)
    prior_high = df.iloc[lookback:k1_idx]['high'].max() if k1_idx > lookback else k1['high']
    breakout = k3['close'] > prior_high
    breakout_score = 12 if breakout else 0

    # 3K整体涨幅/ATR
    total_move = (k3['close'] - k1['open']) / atr
    move_score = min(total_move / 5.0 * 8, 8)

    scores['突破质量'] = round(breakout_score + move_score, 1)

    # --- 维度3: 缺口规模 (15分) ---
    gap1 = (k2['low'] - k1['high']) / atr  # K1-K2间缺口
    gap2 = (k3['low'] - k2['high']) / atr  # K2-K3间缺口
    total_gap = gap1 + gap2
    gap_score = min(max(total_gap / 2.0 * 15, 0), 15)
    scores['缺口规模'] = round(gap_score, 1)

    # --- 维度4: 位置评估 (20分) ---
    loc_pct = k3.get('location_pct', 0.5)
    # 底部(0~30%) → 20分, 中部(30~70%) → 12分, 顶部(70~100%) → 5分
    if loc_pct <= 0.3:
        pos_score = 20
    elif loc_pct <= 0.7:
        pos_score = 12
    else:
        pos_score = 5
    scores['位置评估'] = pos_score

    # --- 维度5: 趋势基础 (15分) ---
    # EMA20斜率 (正向=趋势向上)
    if 'ema20' in df.columns and k3_idx >= 5:
        ema_now = df.iloc[k3_idx]['ema20']
        ema_5ago = df.iloc[k3_idx - 5]['ema20']
        slope = (ema_now - ema_5ago) / atr
        slope_score = min(max(slope / 2.0 * 8, 0), 8)
    else:
        slope_score = 0

    # 收盘是否在EMA20之上
    above_ema = 7 if k3['close'] > k3.get('ema20', k3['close']) else 0

    scores['趋势基础'] = round(slope_score + above_ema, 1)

    total = sum(scores.values())
    return {'total': round(total, 1), 'details': scores}


def score_gap_test(df: pd.DataFrame, test_idx: int, k1_high: float) -> dict:
    """
    对回调过程进行多空力量评分

    维度A: 回调深度 (0~10) — 回调越浅越好
    维度B: 回调持续时间 (0~10) — 越短越好
    维度C: 阴线占比 (0~10) — 阴线越少=多头越强
    维度D: 反转力度 (0~10) — 确认阳线的质量
    """
    scores = {}
    # 找到3K信号位置 (往前找)
    sig_mask = df['signal_3k'].iloc[:test_idx]
    if not sig_mask.any():
        return {'total': 0, 'details': {}}

    sig_idx = sig_mask[sig_mask].index[-1]
    sig_pos = df.index.get_loc(sig_idx)
    test_pos = df.index.get_loc(df.index[test_idx]) if isinstance(test_idx, int) else test_idx

    pullback = df.iloc[sig_pos:test_pos + 1]
    if len(pullback) < 3:
        return {'total': 0, 'details': {}}

    k3_high = df.iloc[sig_pos]['high']
    atr = df.iloc[test_pos].get('atr', 1.0)

    # A: 回调深度 (越浅越好)
    pullback_low = pullback['low'].min()
    depth = (k3_high - pullback_low) / atr if atr > 0 else 5
    depth_score = max(10 - depth * 2, 0)
    scores['回调深度'] = round(depth_score, 1)

    # B: 回调时间 (越短越好)
    duration = len(pullback) - 1  # 排除3K信号日
    time_score = max(10 - duration * 0.5, 0)
    scores['回调时间'] = round(time_score, 1)

    # C: 阴线占比 (阴线越少=空头越弱)
    bear_count = (~pullback['is_bullish']).sum()
    bear_pct = bear_count / max(len(pullback), 1)
    bear_score = max(10 - bear_pct * 15, 0)
    scores['多头控制'] = round(bear_score, 1)

    # D: 反转K线力度
    confirm_bar = df.iloc[test_pos]
    confirm_body = confirm_bar.get('body_pct', 0)
    confirm_height = (confirm_bar['high'] - confirm_bar['low']) / atr if atr > 0 else 0
    rev_score = min(confirm_body * 5 + confirm_height * 2, 10)
    scores['反转力度'] = round(rev_score, 1)

    total = sum(scores.values())
    return {'total': round(total, 1), 'details': scores}


def run_backtest():
    print("=" * 80)
    print("  3K策略全量回测: 国机精工 sz.002046")
    print("=" * 80)

    df = get_stock_data("sz.002046")
    if df is None or df.empty:
        print("❌ 数据库中未找到数据")
        return

    df = add_indicators(df)
    strategy = ThreeKStrategy()
    df = strategy.calculate_signals(df)

    print(f"📊 数据范围: {df['date'].iloc[0]} ~ {df['date'].iloc[-1]} ({len(df)}根K线)")

    # === 统计1: 3K信号 ===
    sig_3k = df[df['signal_3k'] == True].copy()
    print(f"\n{'='*80}")
    print(f"📌 3K信号总数: {len(sig_3k)}")
    print(f"{'='*80}")

    all_results = []
    for pos, (idx, row) in enumerate(sig_3k.iterrows()):
        iloc_pos = df.index.get_loc(idx)
        score = score_3k_signal(df, iloc_pos)
        k1_high = df.iloc[iloc_pos - 2]['high'] if iloc_pos >= 2 else 0
        prior_swing_low = df['low'].iloc[max(0, iloc_pos-42):max(1, iloc_pos-2)].min()
        prior_swing_high = df['high'].iloc[max(0, iloc_pos-42):max(1, iloc_pos-2)].max()

        # 缺口中点和测量目标
        k3_low = row['low']
        gap_mid = (k1_high + k3_low) / 2
        meas_target = 2 * gap_mid - prior_swing_low

        # 检查后续走势 (是否在20根K线内达到目标)
        future = df.iloc[iloc_pos + 1: iloc_pos + 21]
        if len(future) > 0:
            max_future = future['high'].max()
            min_future = future['low'].min()
            hit_target = max_future >= meas_target
            # 关键: 缺口保持 = 回调最低点 > K1高点 且 > 前期波段高点
            gap_stayed_open = (min_future > k1_high) and (min_future > prior_swing_high)
        else:
            max_future = np.nan
            hit_target = False
            gap_stayed_open = False

        result = {
            '序号': pos + 1,
            '日期': row['date'],
            'Close': row['close'],
            'K1_High': k1_high,
            '前期高点': prior_swing_high,
            'SL': row.get('sl_3k', np.nan),
            '前期低点': prior_swing_low,
            '测量目标': meas_target,
            '3K评分': score['total'],
            '缺口保持': '✅' if gap_stayed_open else '❌',
            '达到目标': '✅' if hit_target else '❌',
            '20日最高': max_future,
            '20日最低': min_future if len(future) > 0 else np.nan,
        }
        result.update({f'_{k}': v for k, v in score['details'].items()})
        all_results.append(result)

    results_df = pd.DataFrame(all_results)

    # 打印信号列表
    print("\n📋 3K信号列表:")
    print("-" * 80)
    cols_show = ['序号', '日期', 'Close', 'K1_High', '前期高点', '3K评分', '缺口保持', '20日最低', '测量目标', '达到目标']
    if len(results_df) > 0:
        pd.set_option('display.float_format', '{:.2f}'.format)
        print(results_df[cols_show].to_string(index=False))

    # === 统计2: 缺口测试确认信号 ===
    gap_tests = df[df.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True].copy()
    print(f"\n{'='*80}")
    print(f"📌 缺口测试确认信号总数: {len(gap_tests)}")
    print(f"{'='*80}")

    gt_results = []
    for pos, (idx, row) in enumerate(gap_tests.iterrows()):
        iloc_pos = df.index.get_loc(idx)
        entry = row.get('entry_3k_gap_test', np.nan)
        sl = row.get('sl_3k_gap_test', np.nan)
        tp = row.get('tp_3k_gap_test', np.nan)

        # 回调评分
        gt_score = score_gap_test(df, iloc_pos, 0)

        # 检查盈亏结果 (后续20根K线)
        future = df.iloc[iloc_pos + 1: iloc_pos + 21]
        outcome = 'N/A'
        if len(future) > 0 and not np.isnan(entry) and not np.isnan(sl) and not np.isnan(tp):
            max_h = future['high'].max()
            min_l = future['low'].min()
            # 先判断止损还是止盈
            for _, fb in future.iterrows():
                if fb['low'] <= sl:
                    outcome = '止损 ❌'
                    break
                if fb['high'] >= tp:
                    outcome = '止盈 ✅'
                    break
            if outcome == 'N/A':
                # 未触发止损止盈，看最终P&L
                last_close = future.iloc[-1]['close']
                pnl_pct = (last_close - entry) / entry * 100
                outcome = f'持有 {pnl_pct:+.1f}%'

        risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
        reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
        rr = reward / risk if risk > 0 else 0

        gt_results.append({
            '序号': pos + 1,
            '日期': row['date'],
            'Entry': entry,
            'SL': sl,
            'TP': tp,
            'R:R': f'1:{rr:.1f}',
            '回调评分': gt_score['total'],
            '结果': outcome,
        })
        # 详细评分
        for k, v in gt_score['details'].items():
            gt_results[-1][f'_{k}'] = v

    gt_df = pd.DataFrame(gt_results)
    if len(gt_df) > 0:
        print("\n📋 缺口测试确认信号列表:")
        print("-" * 80)
        cols_gt = ['序号', '日期', 'Entry', 'SL', 'TP', 'R:R', '回调评分', '结果']
        print(gt_df[cols_gt].to_string(index=False))

    # === 统计3: 汇总统计 ===
    print(f"\n{'='*80}")
    print("📊 汇总统计")
    print(f"{'='*80}")

    if len(results_df) > 0:
        gap_held = (results_df['缺口保持'] == '✅').sum()
        hit_tg = (results_df['达到目标'] == '✅').sum()
        avg_score = results_df['3K评分'].mean()
        print(f"  3K信号总数:       {len(results_df)}")
        print(f"  缺口保持开放:     {gap_held}/{len(results_df)} ({gap_held/len(results_df)*100:.0f}%)")
        print(f"  达到测量目标:     {hit_tg}/{len(results_df)} ({hit_tg/len(results_df)*100:.0f}%)")
        print(f"  平均3K评分:       {avg_score:.1f}/100")

        # 评分维度分布
        detail_cols = [c for c in results_df.columns if c.startswith('_')]
        if detail_cols:
            print(f"\n  📊 3K评分维度分析 (平均值):")
            for c in detail_cols:
                avg = results_df[c].mean()
                label = c[1:]  # 去掉前缀_
                max_pts = {'3K强度': 30, '突破质量': 20, '缺口规模': 15, '位置评估': 20, '趋势基础': 15}.get(label, 10)
                bar = '█' * int(avg / max_pts * 15) + '░' * (15 - int(avg / max_pts * 15))
                print(f"    {label:8s}: {avg:5.1f}/{max_pts} {bar}")

    if len(gt_df) > 0:
        wins = gt_df['结果'].str.contains('止盈').sum()
        losses = gt_df['结果'].str.contains('止损').sum()
        total_trades = wins + losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        print(f"\n  缺口测试信号总数: {len(gt_df)}")
        print(f"  止盈/止损/持有:   {wins}/{losses}/{len(gt_df)-total_trades}")
        if total_trades > 0:
            print(f"  胜率:             {win_rate:.0f}%")

        # 回调评分维度
        gt_detail_cols = [c for c in gt_df.columns if c.startswith('_')]
        if gt_detail_cols:
            print(f"\n  📊 回调力量评分维度 (平均值):")
            for c in gt_detail_cols:
                avg = gt_df[c].mean()
                label = c[1:]
                bar = '█' * int(avg) + '░' * (10 - int(avg))
                print(f"    {label:8s}: {avg:5.1f}/10 {bar}")

    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), '..', 'strategy_lab', 'backtest_3k_002046.csv')
    if len(results_df) > 0:
        results_df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n💾 详细结果已保存至: {output_path}")

    return results_df, gt_df


if __name__ == '__main__':
    run_backtest()
