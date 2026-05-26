"""
Gap + High 2 (两腿回调) 策略回测
==================================
基于项目 baostock.db 全 A 股日线数据，跨市场组合回测。

策略核心 (Al Brooks Price Action):
  1. 突破缺口: HH+HL K 线突破 60 周期最高点 (shift 2)
  2. 两腿回调状态机: LHLL -> HH -> LHLL = 信号
  3. 缺口存活: 所有回调低点不得击穿 Gap Floor
  4. 高潮规避: 分组最高价 < TP
  5. 入场: Buy Stop 挂在信号 K 线最高点
  6. SL = Gap Floor, TP = 2 * Gap_Floor - Prior_Swing_Low

组合管理: 单仓 FIFO (同时仅持有一个仓位)
"""

import sqlite3
import pandas as pd
import numpy as np
import json
import sys
import os
from datetime import datetime
from collections import defaultdict

# ============================================================
# 参数配置
# ============================================================
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "baostock.db")
INITIAL_CASH = 1_000_000

# 策略参数 (与 gap_h2_strategy.py 一致)
LOOKBACK_WINDOW = 60
MAX_PULLBACK_WINDOW = 40
MIN_PULLBACK_WINDOW = 2

# A 股交易规则
LOT_SIZE = 100
BUY_COMMISSION = 0.0003   # 买入佣金 3bps
SELL_COMMISSION = 0.0003   # 卖出佣金 3bps
SELL_TAX = 0.0005          # 印花税 5bps (仅卖出)

# 评估窗口
EVAL_START = "2023-07-01"
EVAL_END = "2026-05-22"

# ============================================================
# 数据加载
# ============================================================
def load_daily_bars(db_path):
    """从 SQLite 加载全部日线数据, 返回 dict[symbol] -> DataFrame"""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        "SELECT symbol, trade_date as date, open, high, low, close, volume "
        "FROM daily_bars ORDER BY symbol, date",
        conn,
    )
    conn.close()

    df["date"] = pd.to_datetime(df["date"])
    data = {}
    for symbol, group in df.groupby("symbol"):
        g = group.sort_values("date").drop_duplicates(subset="date", keep="first").reset_index(drop=True)
        if len(g) >= LOOKBACK_WINDOW + 10:
            data[symbol] = g
    return data


def add_indicators(df):
    """计算 EMA(20) + ATR(14), 与 calculator.py 方法一致"""
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(14, min_periods=1).mean()
    return df


# ============================================================
# Gap+H2 信号生成 (忠实复现 gap_h2_strategy.py 向量化逻辑)
# ============================================================
def generate_signals(df):
    """
    向量化计算 Gap+H2 信号。
    返回 DataFrame: [date, entry_level, sl, tp]
    """
    n = len(df)
    if n < LOOKBACK_WINDOW + 10:
        return pd.DataFrame()

    # --- Step 1: 突破检测 ---
    is_hh_hl = (df["high"] > df["high"].shift(1)) & (df["low"] > df["low"].shift(1))
    gap_floor_raw = df["high"].rolling(min_periods=1, window=LOOKBACK_WINDOW).max().shift(2)
    df["_is_breakout"] = is_hh_hl & (df["low"] > gap_floor_raw - 1e-3)

    # --- Step 2: 锚定历史数据 ---
    prior_swing_low_raw = df["low"].rolling(min_periods=1, window=LOOKBACK_WINDOW).min().shift(2)

    _gf = np.where(df["_is_breakout"], gap_floor_raw, np.nan)
    gap_floor = pd.Series(_gf, index=df.index).ffill()

    _psl = np.where(df["_is_breakout"], prior_swing_low_raw, np.nan)
    prior_swing_low = pd.Series(_psl, index=df.index).ffill()

    # --- Step 3: 缺口存活 ---
    bk_cumsum = df["_is_breakout"].cumsum()
    bar_count = df.groupby(bk_cumsum).cumcount()
    group_min_low = df["low"].groupby(bk_cumsum).expanding().min().droplevel(0)
    gap_alive = group_min_low > (gap_floor - 1e-3)

    # --- Step 4: 两腿回调状态机 ---
    in_window = (bar_count >= MIN_PULLBACK_WINDOW) & (bar_count <= MAX_PULLBACK_WINDOW)
    in_window = in_window & (bk_cumsum > 0)

    is_lhll = (df["high"] < df["high"].shift(1)) & (df["low"] < df["low"].shift(1))
    is_hh = df["high"] > df["high"].shift(1)

    # Phase 1: 首根 LHLL
    lhll_cum = is_lhll.groupby(bk_cumsum).cumsum()
    phase1_done = lhll_cum >= 1

    # Phase 2: HH after phase 1 (High 1)
    is_hh_after_pb1 = is_hh & phase1_done
    hh_cum = is_hh_after_pb1.groupby(bk_cumsum).cumsum()
    phase2_done = hh_cum >= 1

    # Phase 3: LHLL after phase 2 -> 信号
    is_lhll_after_h1 = is_lhll & phase2_done
    lhll_cum2 = is_lhll_after_h1.groupby(bk_cumsum).cumsum()
    prev_lhll = lhll_cum2.groupby(bk_cumsum).shift(1).fillna(0)
    is_second_pullback = (prev_lhll == 0) & (lhll_cum2 >= 1)

    # --- 高潮规避器 ---
    target = 2 * gap_floor - prior_swing_low
    group_max_high = df["high"].groupby(bk_cumsum).expanding().max().droplevel(0)
    mm_not_reached = ((group_max_high < target) | target.isna()).fillna(True)

    # --- 组装信号 ---
    signal_raw = in_window & gap_alive & is_second_pullback & mm_not_reached
    dedup = signal_raw.groupby(bk_cumsum).cumsum().shift(1).fillna(0) > 0
    signal_final = signal_raw & ~dedup

    sig_mask = signal_final
    if not sig_mask.any():
        return pd.DataFrame()

    rows = df.loc[sig_mask].copy()
    rows["entry_level"] = rows["high"].values
    rows["sl"] = gap_floor[sig_mask].values
    rows["tp"] = target[sig_mask].values

    # 过滤: SL > 0, TP > entry_level, entry_level > SL
    valid = (rows["sl"] > 0) & (rows["tp"] > rows["entry_level"]) & (rows["entry_level"] > rows["sl"])
    rows = rows[valid]

    if rows.empty:
        return pd.DataFrame()
    return rows[["date", "entry_level", "sl", "tp"]].reset_index(drop=True)


# ============================================================
# 单笔交易模拟
# ============================================================
def simulate_trade(symbol, signal_date, entry_level, sl, tp, df, eval_end_dt):
    """
    独立模拟一笔交易。
    返回 dict 或 None (信号未触发)。
    """
    # 找到 signal_date 的索引
    mask = df["date"] == signal_date
    if not mask.any():
        return None
    sig_idx = df.index[mask][0]

    # 入场 = signal 的下一根 K 线
    if sig_idx + 1 >= len(df):
        return None
    entry_idx = sig_idx + 1
    entry_row = df.iloc[entry_idx]
    entry_date = entry_row["date"]

    # Buy Stop 执行逻辑
    if entry_row["open"] >= entry_level:
        fill_price = entry_row["open"]
    elif entry_row["high"] >= entry_level:
        fill_price = entry_level
    else:
        return None  # 价格未触及 Buy Stop

    # 向前遍历, 检查 SL/TP (SL 优先)
    exit_price = None
    exit_date = None
    triggered = None

    for j in range(entry_idx + 1, len(df)):
        row = df.iloc[j]
        # T+1 自动满足 (j > entry_idx)

        if row["date"] > eval_end_dt:
            break  # 超出评估窗口, 强制平仓

        # 缺口穿透: open <= SL
        if row["open"] <= sl:
            exit_price = row["open"]
            exit_date = row["date"]
            triggered = "SL"
            break
        # 缺口穿透: open >= TP
        if row["open"] >= tp:
            exit_price = row["open"]
            exit_date = row["date"]
            triggered = "TP"
            break
        # 日内检查 (SL 优先)
        if row["low"] <= sl:
            exit_price = sl
            exit_date = row["date"]
            triggered = "SL"
            break
        if row["high"] >= tp:
            exit_price = tp
            exit_date = row["date"]
            triggered = "TP"
            break

    # 如果到达评估窗口末尾仍未出场 → 强制平仓
    if exit_price is None:
        # 找到 eval_end_dt 之前最后一根可用的 K 线
        for j in range(len(df) - 1, entry_idx, -1):
            if df.iloc[j]["date"] <= eval_end_dt:
                exit_price = df.iloc[j]["close"]
                exit_date = df.iloc[j]["date"]
                triggered = "FORCE_CLOSE"
                break
        else:
            return None

    holding_bars = 0
    # 计算 holding_bars: 在 df 中从 entry_idx 到 exit_idx 的 bar 数
    exit_mask = df["date"] == exit_date
    if exit_mask.any():
        exit_idx_val = df.index[exit_mask][0]
        holding_bars = exit_idx_val - entry_idx

    # PnL (含手续费, 不含仓位)
    buy_cost_per_share = fill_price * (1 + BUY_COMMISSION)
    sell_proceeds_per_share = exit_price * (1 - SELL_COMMISSION - SELL_TAX)
    pnl_pct = (sell_proceeds_per_share / buy_cost_per_share - 1) * 100

    return {
        "symbol": symbol,
        "signal_date": str(signal_date)[:10],
        "entry_date": str(entry_date)[:10],
        "exit_date": str(exit_date)[:10],
        "entry_price": round(fill_price, 4),
        "exit_price": round(exit_price, 4),
        "pnl_pct": round(pnl_pct, 2),
        "holding_bars": holding_bars,
        "triggered": triggered,
    }


# ============================================================
# 组合模拟 + 权益曲线
# ============================================================
def run_portfolio(trades, all_data, eval_start_dt, eval_end_dt, initial_cash):
    """
    单仓 FIFO 组合模拟。
    trades: 已按 entry_date 排序的潜在交易列表。
    返回 (equity_curve, executed_trades)。
    """
    # 构建日期 -> 股票收盘价 查找表
    sym_close = {}
    for symbol, df in all_data.items():
        sym_close[symbol] = dict(zip(df["date"], df["close"]))

    # Phase 1: 预选非重叠交易 (FIFO — 入场日期不得早于上一笔出场日期)
    selected = []
    skip_until = eval_start_dt - pd.Timedelta(days=1)
    for t in trades:
        entry_dt = pd.Timestamp(t["entry_date"])
        exit_dt = pd.Timestamp(t["exit_date"])
        if entry_dt > eval_end_dt:
            break
        if entry_dt <= skip_until:
            continue  # 与上一笔交易重叠, 跳过
        selected.append(t)
        skip_until = exit_dt

    # 合并所有交易日 (取并集)
    all_dates = sorted(set(
        d for df in all_data.values() for d in df["date"]
        if eval_start_dt <= d <= eval_end_dt
    ))

    # Phase 2: 逐日模拟
    cash = float(initial_cash)
    position = 0
    entry_price = 0.0
    current_symbol = ""
    last_close = 0.0
    current_trade = None

    equity_curve = []
    executed = []
    sel_idx = 0

    for date in all_dates:
        date_str = str(date)[:10]

        # --- 出场检查 (先出场再入场) ---
        if position > 0 and current_trade is not None:
            if pd.Timestamp(current_trade["exit_date"]) == date:
                exit_price = current_trade["exit_price"]
                proceeds = position * exit_price * (1 - SELL_COMMISSION - SELL_TAX)
                pnl = proceeds - position * entry_price * (1 + BUY_COMMISSION)
                pnl_pct = (proceeds / (position * entry_price * (1 + BUY_COMMISSION)) - 1) * 100

                current_trade["size"] = position
                current_trade["pnl"] = round(pnl, 2)
                current_trade["pnl_pct"] = round(pnl_pct, 2)
                current_trade["side"] = "long"

                cash += proceeds
                cash = round(cash, 2)
                position = 0
                current_symbol = ""
                executed.append(current_trade)
                current_trade = None

        # --- 入场检查 (仅使用预选的非重叠交易) ---
        if position == 0 and sel_idx < len(selected):
            s = selected[sel_idx]
            if pd.Timestamp(s["entry_date"]) == date:
                cost_per_share = s["entry_price"] * (1 + BUY_COMMISSION)
                size = int(cash / cost_per_share)
                size = (size // LOT_SIZE) * LOT_SIZE
                if size > 0:
                    cash -= size * cost_per_share
                    position = size
                    entry_price = s["entry_price"]
                    current_symbol = s["symbol"]
                    current_trade = dict(s)
                    sel_idx += 1
                else:
                    sel_idx += 1  # 资金不足, 跳过

        # --- 记录权益 ---
        if position > 0:
            close_val = sym_close.get(current_symbol, {}).get(date)
            if close_val is not None:
                last_close = close_val
            value = cash + position * last_close
        else:
            value = cash

        equity_curve.append({"date": date_str, "value": round(value, 2)})

    # --- 期末强制平仓 ---
    if position > 0 and current_trade is not None:
        last_date_str = str(all_dates[-1])[:10]
        if current_trade["exit_date"] != last_date_str:
            close_val = sym_close.get(current_symbol, {}).get(all_dates[-1])
            if close_val is not None:
                exit_price = close_val
                proceeds = position * exit_price * (1 - SELL_COMMISSION - SELL_TAX)
                pnl = proceeds - position * entry_price * (1 + BUY_COMMISSION)
                pnl_pct = (proceeds / (position * entry_price * (1 + BUY_COMMISSION)) - 1) * 100

                current_trade["exit_date"] = last_date_str
                current_trade["exit_price"] = round(exit_price, 4)
                current_trade["triggered"] = "END_FORCE_CLOSE"
                current_trade["size"] = position
                current_trade["pnl"] = round(pnl, 2)
                current_trade["pnl_pct"] = round(pnl_pct, 2)
                current_trade["side"] = "long"
                current_trade["holding_bars"] += 1

                cash += proceeds
                cash = round(cash, 2)
                executed.append(current_trade)

    return equity_curve, executed


# ============================================================
# 导出标准 3 文件
# ============================================================
def export_results(equity_curve, trade_history, prefix, initial_cash, start, end):
    """写入 equity.csv, trades.csv, summary.json"""
    import csv
    from pathlib import Path
    import math

    out_dir = Path(os.path.dirname(os.path.abspath(__file__)))

    # --- equity.csv ---
    eq_path = out_dir / f"{prefix}_equity.csv"
    with eq_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "value"])
        for point in equity_curve:
            writer.writerow([point["date"], point["value"]])

    # --- trades.csv ---
    tr_path = out_dir / f"{prefix}_trades.csv"
    fields = [
        "entry_date", "exit_date", "side", "size",
        "entry_price", "exit_price", "pnl", "pnl_pct",
        "holding_bars", "symbol", "symbol_name", "display_symbol",
    ]
    with tr_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for t in trade_history:
            writer.writerow([t.get(k, "") for k in fields])

    # --- summary.json ---
    # Slice equity to eval window
    eval_curve = [p for p in equity_curve if start <= p["date"] <= end]
    if not eval_curve:
        eval_curve = equity_curve

    base_value = eval_curve[0]["value"] if eval_curve else initial_cash
    final_value = eval_curve[-1]["value"] if eval_curve else initial_cash
    total_return = final_value / base_value - 1.0

    # Daily returns for Sharpe
    returns = []
    prev = None
    for p in eval_curve:
        v = p["value"]
        if prev is not None and prev != 0:
            returns.append(v / prev - 1.0)
        prev = v

    # Max drawdown
    peak = None
    max_dd = 0.0
    for p in eval_curve:
        v = p["value"]
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            dd = (v / peak - 1.0) * 100
            if dd < max_dd:
                max_dd = dd

    total_trades = len(trade_history)
    wins = sum(1 for t in trade_history if t.get("pnl", 0) > 0)
    win_rate = wins / total_trades * 100 if total_trades else 0.0

    # Annual return
    n_periods = len(returns)
    annual_return = None
    if n_periods > 0 and (1 + total_return) > 0:
        annual_return = ((1 + total_return) ** (252.0 / n_periods) - 1.0) * 100.0

    # Sharpe
    sharpe = None
    if len(returns) > 1:
        mean_ret = sum(returns) / len(returns)
        var = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = mean_ret / std * math.sqrt(252)

    summary = {
        "total_return_pct": round(total_return * 100, 2),
        "annual_return_pct": round(annual_return, 2) if annual_return is not None else None,
        "max_drawdown_pct": round(abs(max_dd), 2),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "win_rate_pct": round(win_rate, 2),
        "total_trades": total_trades,
    }

    meta = {
        "strategy_name": "Gap + High 2 (两腿回调)",
        "symbol": "全 A 股 (3308 只)",
        "start": start,
        "end": end,
        "initial_cash": initial_cash,
        "window_start_value": base_value,
        "final_value": final_value,
        "market": "china_a",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    sm_path = out_dir / f"{prefix}_summary.json"
    sm_path.write_text(
        json.dumps({"meta": meta, "summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return eq_path, tr_path, sm_path


# ============================================================
# 仪表盘渲染
# ============================================================
def render_dashboard(prefix):
    """使用 skill 模板渲染 HTML 仪表盘"""
    skill_ref = os.path.join(
        os.path.expanduser("~"),
        ".workbuddy",
        "plugins",
        "marketplaces",
        "experts",
        "plugins",
        "strategy-backtest-expert",
        "skills",
        "quant-backtest-lab",
        "reference",
    )
    if not os.path.isdir(skill_ref):
        print(f"  WARNING: Skill reference not found at {skill_ref}, skipping dashboard")
        return None

    sys.path.insert(0, skill_ref)

    try:
        from render_dashboard import build_dashboard_data, render_dashboard as _render
    except ImportError as e:
        print(f"  WARNING: Cannot import render_dashboard: {e}")
        return None

    out_dir = os.path.dirname(os.path.abspath(__file__))
    eq_csv = os.path.join(out_dir, f"{prefix}_equity.csv")
    tr_csv = os.path.join(out_dir, f"{prefix}_trades.csv")
    sm_json = os.path.join(out_dir, f"{prefix}_summary.json")

    report_data = build_dashboard_data(
        equity_csv=eq_csv,
        trades_csv=tr_csv,
        summary_json=sm_json,
        language="zh",
    )

    html_path = os.path.join(out_dir, "index.html")
    _render(report_data, output_path=html_path)
    return html_path


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] Gap+H2 回测启动")
    print(f"  数据库: {DB_PATH}")
    print(f"  评估窗口: {EVAL_START} ~ {EVAL_END}")
    print(f"  初始资金: {INITIAL_CASH:,.0f}")
    print()

    # 1. 加载数据
    print("[1/5] 加载日线数据...")
    all_data = load_daily_bars(DB_PATH)
    print(f"  股票数: {len(all_data)}")
    total_rows = sum(len(df) for df in all_data.values())
    print(f"  总行数: {total_rows:,}")

    # 2. 计算指标 + 生成信号
    print("[2/5] 计算指标 & 生成 Gap+H2 信号...")
    all_potential_trades = []
    eval_start_dt = pd.Timestamp(EVAL_START)
    eval_end_dt = pd.Timestamp(EVAL_END)

    for symbol, df in all_data.items():
        df = add_indicators(df)
        sigs = generate_signals(df)
        if sigs.empty:
            continue
        for _, row in sigs.iterrows():
            signal_date = row["date"]
            if signal_date < eval_start_dt or signal_date > eval_end_dt:
                continue
            trade = simulate_trade(
                symbol,
                signal_date,
                row["entry_level"],
                row["sl"],
                row["tp"],
                df,
                eval_end_dt,
            )
            if trade is not None:
                all_potential_trades.append(trade)

    # 按入场日期排序, 同日取最优 R:R
    all_potential_trades.sort(
        key=lambda t: (
            t["entry_date"],
            -((t["exit_price"] - t["entry_price"]) / max(t["entry_price"] - 0.001, 0.001)),
        )
    )
    # 去重同日信号 (保留 R:R 最高的)
    seen_dates = set()
    unique_trades = []
    for t in all_potential_trades:
        if t["entry_date"] not in seen_dates:
            unique_trades.append(t)
            seen_dates.add(t["entry_date"])

    print(f"  原始信号: {len(all_potential_trades)}")
    print(f"  去重后: {len(unique_trades)}")
    print(f"  SL出场: {sum(1 for t in unique_trades if t['triggered'] == 'SL')}")
    print(f"  TP出场: {sum(1 for t in unique_trades if t['triggered'] == 'TP')}")
    print(f"  强制平仓: {sum(1 for t in unique_trades if 'FORCE' in t['triggered'] or 'END' in t['triggered'])}")

    # 3. 组合回测
    print("[3/5] 运行组合回测 (单仓 FIFO)...")
    equity_curve, executed_trades = run_portfolio(
        unique_trades, all_data, eval_start_dt, eval_end_dt, INITIAL_CASH
    )
    print(f"  实际执行: {len(executed_trades)} 笔")
    if executed_trades:
        wins = sum(1 for t in executed_trades if t.get("pnl", 0) > 0)
        print(f"  盈利: {wins}, 亏损: {len(executed_trades) - wins}")

    # 4. 导出结果
    print("[4/5] 导出标准文件...")
    export_results(equity_curve, executed_trades, "gap_h2", INITIAL_CASH, EVAL_START, EVAL_END)
    print("  gap_h2_equity.csv")
    print("  gap_h2_trades.csv")
    print("  gap_h2_summary.json")

    # 5. 渲染仪表盘
    print("[5/5] 渲染仪表盘...")
    html_path = render_dashboard("gap_h2")
    if html_path:
        print(f"  {html_path}")

    elapsed = (datetime.now() - t0).total_seconds()
    print(f"\n完成! 耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
