# tools/backtest_gap_pinbar_ema20.py
"""
[实验性回测] Gap + Pinbar + 首次刺破 EMA20 策略 (独立内联逻辑)

核心变更 vs 生产版 structural_gap_strategy:
  1. 买入模式统一为：回调期间首次刺破 EMA20 的 Pinbar（下影线>=40%, 收盘位置>=50%, close > EMA20）
  2. TP 公式改为：TP = 2 * Gap_Floor - Prior_Swing_Low (起涨区间上翻)
  3. 完整保留三条生命周期过滤规则 (防守端 + 收益端 + 时限端)

本脚本不依赖任何生产策略类，信号逻辑完全内联。
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
from config import settings

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================
# 策略参数
# ============================================================
LOOKBACK_WINDOW = 60
MAX_PULLBACK_WINDOW = 40
MIN_PULLBACK_WINDOW = 2
PINBAR_LOWER_WICK_MIN = 0.40
PINBAR_CLOSE_LOC_MIN = 0.50
LIFECYCLE_TIMEOUT_BARS = 30  # 生命周期时限端：30 根 K 线

# ============================================================
# 信号计算 (完全内联, 向量化)
# ============================================================
def calculate_signals_inline(df: pd.DataFrame) -> pd.DataFrame:
    """向量化计算 Gap + Pinbar + 首次刺破 EMA20 信号（不依赖任何策略类）"""
    if len(df) < LOOKBACK_WINDOW + 10:
        df['signal'] = False
        return df

    required = ['atr', 'ema20']
    if not all(c in df.columns for c in required):
        df['signal'] = False
        return df

    # === 第一步：识别结构性突破 ===
    is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
    gap_floor_raw = df['high'].rolling(min_periods=1, window=LOOKBACK_WINDOW).max().shift(2)
    df['is_breakout'] = is_hh_hl & (df['low'] > gap_floor_raw - 1e-3)

    # === 第二步：锚定历史数据 ===
    prior_swing_low_raw = df['low'].rolling(min_periods=1, window=LOOKBACK_WINDOW).min().shift(2)

    _gap_floor = np.where(df['is_breakout'], gap_floor_raw, np.nan)
    _gap_floor = pd.Series(_gap_floor, index=df.index).ffill()

    _prior_swing_low = np.where(df['is_breakout'], prior_swing_low_raw, np.nan)
    _prior_swing_low = pd.Series(_prior_swing_low, index=df.index).ffill()

    # === 第三步：缺口存活监控 ===
    bars_since_breakout = df['is_breakout'].cumsum()
    bar_count = df.groupby(bars_since_breakout).cumcount()
    group_min_low = df['low'].groupby(bars_since_breakout).expanding().min().droplevel(0)
    gap_open = group_min_low > (_gap_floor - 1e-3)

    # === 第四步：信号确认 (Pinbar + 首次刺破 EMA20) ===
    in_window = (bar_count >= MIN_PULLBACK_WINDOW) & (bar_count <= MAX_PULLBACK_WINDOW)
    in_window = in_window & (bars_since_breakout > 0)

    bar_range = (df['high'] - df['low']).replace(0, np.nan)
    lower_wick = df[['open', 'close']].min(axis=1) - df['low']
    sig_quality = (df['close'] - df['low']) / bar_range
    is_pinbar = (lower_wick / bar_range >= PINBAR_LOWER_WICK_MIN) & (sig_quality >= PINBAR_CLOSE_LOC_MIN)

    is_pierced = df['low'] <= df['ema20']
    pierce_count_prev = is_pierced.groupby(bars_since_breakout).cumsum().shift(1).fillna(0)
    is_first_pierce = (pierce_count_prev == 0) & is_pierced
    close_above_ema = df['close'] > df['ema20']

    # TP = 2 * Gap_Floor - Prior_Swing_Low
    target = 2 * _gap_floor - _prior_swing_low
    group_max_high = df['high'].groupby(bars_since_breakout).expanding().max().droplevel(0)
    mm_not_reached = (group_max_high < target) | target.isna()
    mm_not_reached = mm_not_reached.fillna(True)

    signal_raw = in_window & gap_open & is_pinbar & is_first_pierce & close_above_ema & mm_not_reached

    # 去重：每次突破仅取首次信号
    already = signal_raw.groupby(bars_since_breakout).cumsum().shift(1).fillna(0) > 0
    df['signal'] = signal_raw & ~already

    # 输出定单参数
    df['entry'] = np.where(df['signal'], df['high'], np.nan)
    df['sl'] = np.where(df['signal'], _gap_floor, np.nan)
    df['tp'] = np.where(df['signal'], target, np.nan)
    df['sig_quality'] = sig_quality.round(3)
    df['bars_since_breakout_grp'] = bars_since_breakout

    # 回测特征提取用
    df['_gap_floor'] = _gap_floor
    df['_prior_swing_low'] = _prior_swing_low

    return df


# ============================================================
# 交易评估 (含完整生命周期三条规则)
# ============================================================
def evaluate_trade(df: pd.DataFrame, signal_idx: int) -> dict:
    """
    模拟 Buy Stop 交易流程，含完整生命周期过滤：
    1. 防守端：缺口被回填 (low < gap_floor) → 撤单
    2. 收益端：止盈先达 (high >= tp 但 entry 未触发) → 作废
    3. 时限端：超过 LIFECYCLE_TIMEOUT_BARS 根 K 线未触发 → 超时失效
    """
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry']
        sl_price = sig_row['sl']
        tp_price = sig_row['tp']
        gap_floor = sig_row['_gap_floor']

        if pd.isna(entry_price) or pd.isna(sl_price) or pd.isna(tp_price):
            return {'status': 'ERROR', 'reason': 'NaN in trade params'}

        # 回调特征提取
        bsg = sig_row['bars_since_breakout_grp']
        pb_mask = (df['bars_since_breakout_grp'] == bsg) & (df.index < df.index[signal_idx])
        pb_df = df.loc[pb_mask]
        pb_bars = len(pb_df)

        if not pb_df.empty:
            is_bear = pb_df['close'] < pb_df['open']
            consec_groups = is_bear.groupby((~is_bear).cumsum()).sum()
            max_consec_bear = int(consec_groups.max()) if not consec_groups.empty else 0
        else:
            max_consec_bear = 0

        sig_q = float(sig_row.get('sig_quality', 0))
        base_info = {
            'sig_quality': sig_q,
            'pb_bars': pb_bars,
            'pb_consec_bear': max_consec_bear,
        }

        post_signal = df.iloc[signal_idx + 1:]
        if post_signal.empty:
            return {**base_info, 'status': 'PENDING', 'reason': 'EOF'}

        trade_status = 'WAITING_TRIGGER'
        bars_waited = 0
        entry_date = None
        actual_entry = None

        for i_pos in range(len(post_signal)):
            row = post_signal.iloc[i_pos]
            row_date = row['date'] if 'date' in row else post_signal.index[i_pos]

            if trade_status == 'WAITING_TRIGGER':
                bars_waited += 1

                # 规则1: 防守端 — 缺口被回填
                if row['low'] < gap_floor - 1e-3:
                    return {**base_info, 'status': 'INVALIDATED', 'reason': 'gap_filled'}

                # 规则2: 收益端 — 止盈先达（Entry 未触发但 TP 已到达）
                if row['high'] >= tp_price:
                    return {**base_info, 'status': 'VOIDED', 'reason': 'tp_reached_before_entry'}

                # 规则3: 时限端 — 超时失效
                if bars_waited > LIFECYCLE_TIMEOUT_BARS:
                    return {**base_info, 'status': 'TIMEOUT', 'reason': 'timeout_30bars'}

                # 正常触发入场
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row_date
                    trade_status = 'IN_TRADE'

                    # 极端日内：同日触发入场又扫损（保守假设先扫损）
                    if row['low'] <= sl_price:
                        return {
                            **base_info, 'status': 'LOSS',
                            'entry_date': entry_date, 'exit_date': entry_date,
                            'entry_price': actual_entry, 'exit_price': sl_price,
                            'reason': 'same_day_stop',
                        }
                    if row['high'] >= tp_price:
                        return {
                            **base_info, 'status': 'WIN',
                            'entry_date': entry_date, 'exit_date': entry_date,
                            'entry_price': actual_entry, 'exit_price': tp_price,
                            'reason': 'same_day_tp',
                        }

            elif trade_status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    actual_exit = min(sl_price, row['open'])
                    return {
                        **base_info, 'status': 'LOSS',
                        'entry_date': entry_date, 'exit_date': row_date,
                        'entry_price': actual_entry, 'exit_price': actual_exit,
                        'reason': 'stop_loss',
                    }
                if row['high'] >= tp_price:
                    return {
                        **base_info, 'status': 'WIN',
                        'entry_date': entry_date, 'exit_date': row_date,
                        'entry_price': actual_entry, 'exit_price': tp_price,
                        'reason': 'take_profit',
                    }

        if trade_status == 'IN_TRADE':
            return {**base_info, 'status': 'HOLDING', 'entry_date': entry_date, 'entry_price': actual_entry, 'reason': 'holding_eof'}
        return {**base_info, 'status': 'PENDING', 'reason': 'not_triggered_eof'}

    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}


# ============================================================
# 单股回测
# ============================================================
def backtest_single(code: str, timeframe: str = 'daily', bars_limit: int = 1500) -> list:
    try:
        if timeframe == 'weekly':
            df = get_stock_data_weekly(code, limit=bars_limit)
        else:
            df = get_stock_data(code, limit=bars_limit)

        if df is None or len(df) < 100:
            return []

        df = add_indicators(df)
        df = calculate_signals_inline(df)

        signal_indices = [i for i, v in enumerate(df['signal']) if v]
        results = []
        for idx in signal_indices:
            trade = evaluate_trade(df, idx)
            sig_date = df.iloc[idx]['date'] if 'date' in df.columns else str(df.index[idx])
            if hasattr(sig_date, 'strftime'):
                sig_date = sig_date.strftime('%Y-%m-%d')
            trade['code'] = code
            trade['signal_date'] = str(sig_date)

            if trade['status'] in ('WIN', 'LOSS'):
                profit = trade['exit_price'] - trade['entry_price']
                risk = trade['entry_price'] - trade.get('sl', df.iloc[idx]['sl'])
                if pd.isna(risk) or risk <= 0:
                    risk = trade['entry_price'] - df.iloc[idx]['sl']
                trade['profit'] = profit
                trade['rr'] = profit / risk if risk > 0 else 0

            results.append(trade)
        return results
    except Exception as e:
        return []


def _worker_daily(code):
    return backtest_single(code, 'daily', 1500)

def _worker_weekly(code):
    return backtest_single(code, 'weekly', 800)


# ============================================================
# 统计报表
# ============================================================
def print_report(all_trades: list, timeframe_label: str):
    total = len(all_trades)
    if total == 0:
        print(f"\n  [{timeframe_label}] 未产生任何信号。")
        return

    wins = [t for t in all_trades if t['status'] == 'WIN']
    losses = [t for t in all_trades if t['status'] == 'LOSS']
    invalidated = [t for t in all_trades if t['status'] == 'INVALIDATED']
    voided = [t for t in all_trades if t['status'] == 'VOIDED']
    timeout = [t for t in all_trades if t['status'] == 'TIMEOUT']
    holding = [t for t in all_trades if t['status'] == 'HOLDING']
    pending = [t for t in all_trades if t['status'] == 'PENDING']

    completed = len(wins) + len(losses)

    print("\n" + "=" * 60)
    print(f"  Gap + Pinbar + EMA20 [实验性回测] ({timeframe_label})")
    print("=" * 60)
    print(f"  总信号数          : {total}")
    print(f"  ------------------------------------------")
    print(f"  [生命周期过滤结果]")
    print(f"    缺口回填撤单    : {len(invalidated)}")
    print(f"    止盈先达作废    : {len(voided)}")
    print(f"    超时失效        : {len(timeout)}")
    print(f"  ------------------------------------------")
    print(f"  持仓中/待触发     : {len(holding) + len(pending)}")
    print(f"  已结案实战交易    : {completed}")
    print("-" * 60)

    if completed > 0:
        win_rate = len(wins) / completed * 100
        avg_rr_win = np.mean([t['rr'] for t in wins]) if wins else 0
        avg_rr_loss = np.mean([t['rr'] for t in losses]) if losses else 0
        ev = win_rate / 100 * avg_rr_win + (1 - win_rate / 100) * avg_rr_loss
        total_r = sum(t.get('rr', 0) for t in wins) + sum(t.get('rr', 0) for t in losses)

        print(f"  [+] 胜率           : {win_rate:.2f}% ({len(wins)} W / {len(losses)} L)")
        print(f"  [+] 获利单平均 R   : +{avg_rr_win:.3f} R")
        print(f"  [-] 亏损单平均 R   : {avg_rr_loss:.3f} R")
        print(f"  [*] 累计净 R       : {total_r:.2f} R")
        print(f"  ================================================")
        print(f"  [*] 单笔数学期望 EV: {ev:+.4f} R/单")
        print(f"  ================================================")

        # 概率切割矩阵
        if completed >= 5:
            df_res = pd.DataFrame([t for t in all_trades if t['status'] in ('WIN', 'LOSS')])
            df_res['is_win'] = df_res['status'] == 'WIN'

            print(f"\n  --- 概率切割矩阵 ---")

            # 维度1: Pinbar 质量
            if 'sig_quality' in df_res.columns:
                df_res['sq_tier'] = pd.cut(df_res['sig_quality'],
                    bins=[-np.inf, 0.6, 0.8, 0.95, np.inf],
                    labels=['<0.6', '0.6-0.8', '0.8-0.95', '>0.95'])
                print(f"  [维度1] Pinbar 收盘位置质量:")
                for tier, grp in df_res.groupby('sq_tier', observed=True):
                    if len(grp) > 0:
                        wr = grp['is_win'].mean() * 100
                        avg_r = grp['rr'].mean()
                        print(f"    {tier:>10s}: n={len(grp):>4d}  WR={wr:5.1f}%  avg_R={avg_r:+.3f}")

            # 维度2: 回调周期
            if 'pb_bars' in df_res.columns:
                df_res['pb_tier'] = pd.cut(df_res['pb_bars'],
                    bins=[-np.inf, 4, 10, 20, np.inf],
                    labels=['<=4', '5-10', '11-20', '>20'])
                print(f"  [维度2] 回调周期:")
                for tier, grp in df_res.groupby('pb_tier', observed=True):
                    if len(grp) > 0:
                        wr = grp['is_win'].mean() * 100
                        avg_r = grp['rr'].mean()
                        print(f"    {tier:>10s}: n={len(grp):>4d}  WR={wr:5.1f}%  avg_R={avg_r:+.3f}")

            # 维度3: 连阴
            if 'pb_consec_bear' in df_res.columns:
                df_res['cb_tier'] = pd.cut(df_res['pb_consec_bear'],
                    bins=[-np.inf, 1, 2, np.inf],
                    labels=['<=1', '2', '>=3'])
                print(f"  [维度3] 回调期连续阴线数:")
                for tier, grp in df_res.groupby('cb_tier', observed=True):
                    if len(grp) > 0:
                        wr = grp['is_win'].mean() * 100
                        avg_r = grp['rr'].mean()
                        print(f"    {tier:>10s}: n={len(grp):>4d}  WR={wr:5.1f}%  avg_R={avg_r:+.3f}")

            # 极品 vs 毒性
            best = df_res[(df_res['sig_quality'] > 0.8) & (df_res.get('pb_consec_bear', 0) < 3)]
            worst = df_res[(df_res['sig_quality'] <= 0.6)]
            if len(best) > 0:
                print(f"\n  [+] 极品组合 (质量>0.8 且 连阴<3): n={len(best)}, WR={best['is_win'].mean()*100:.1f}%, avg_R={best['rr'].mean():+.3f}")
            if len(worst) > 0:
                print(f"  [-] 低质组合 (质量<=0.6): n={len(worst)}, WR={worst['is_win'].mean()*100:.1f}%, avg_R={worst['rr'].mean():+.3f}")

    else:
        print("  无足够结案交易进行统计。")

    print("=" * 60)


# ============================================================
# 主入口
# ============================================================
def run(limit: int = 0, bars_limit_daily: int = 1500, bars_limit_weekly: int = 800):
    all_codes = get_stock_list()
    if not all_codes:
        print("[!] 无法获取股票列表")
        return

    if limit > 0:
        all_codes = all_codes[:limit]

    n = len(all_codes)
    print(f"[*] 准备扫描 {n} 只标的")
    print(f"[*] 策略: Gap + Pinbar + 首次刺破EMA20 | TP = 2*GapFloor - PriorSwingLow")
    print(f"[*] 生命周期: 防守端(gap_fill) + 收益端(tp_before_entry) + 时限端({LIFECYCLE_TIMEOUT_BARS}bars)")

    # --- 日线回测 ---
    print(f"\n{'='*60}")
    print(f"  [Phase 1/2] 日线回测 (回溯 ~{bars_limit_daily/250:.1f} 年)...")
    print(f"{'='*60}")
    daily_trades = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as exe:
        futures = {exe.submit(_worker_daily, c): c for c in all_codes}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  [daily] {done}/{n} ...", flush=True)
            res = f.result()
            if res:
                daily_trades.extend(res)
    t1 = time.time()
    print(f"  [daily] 完成, 耗时 {t1 - t0:.1f}s, 信号 {len(daily_trades)} 笔")
    print_report(daily_trades, "日线")

    # --- 周线回测 ---
    print(f"\n{'='*60}")
    print(f"  [Phase 2/2] 周线回测 (回溯 ~{bars_limit_weekly/52:.1f} 年)...")
    print(f"{'='*60}")
    weekly_trades = []
    t2 = time.time()
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as exe:
        futures = {exe.submit(_worker_weekly, c): c for c in all_codes}
        done = 0
        for f in as_completed(futures):
            done += 1
            if done % 200 == 0:
                print(f"  [weekly] {done}/{n} ...", flush=True)
            res = f.result()
            if res:
                weekly_trades.extend(res)
    t3 = time.time()
    print(f"  [weekly] 完成, 耗时 {t3 - t2:.1f}s, 信号 {len(weekly_trades)} 笔")
    print_report(weekly_trades, "周线")

    # --- 对照基准 ---
    print(f"\n{'='*60}")
    print(f"  [参考基准] 现有生产策略 (日线): EV = +0.0604 R/单")
    print(f"{'='*60}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gap+Pinbar+EMA20 实验性回测")
    parser.add_argument('--limit', type=int, default=0, help='扫描股票数量限制 (0=全库)')
    args = parser.parse_args()
    run(limit=args.limit)
