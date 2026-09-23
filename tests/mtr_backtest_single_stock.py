# -*- coding: utf-8 -*-
"""
MTR 单股回测 (603650) —— OLD 生产逻辑 vs NEW 信号K/版本B入场
==============================================================
复用项目既有方法:
  - 结构判定: core.strategies.mtr_structural_v35.MTRStructuralEngineV35
  - 退出模拟: core.backtest_engine.simulate_trade_unified (统一成本口径)
  - Wilson CI: core.backtest_engine.wilson_ci

对比口径 (公平):
  OLD: 生产原逻辑 —— 信号K = TL后首根阳线收上半部(close_loc>=0.5);
                         入场 = 信号K最高价 Buy Stop。
  NEW: 用户 2026-09-23 修订选 B
        - 信号K = ①TL后首根 close>EMA20 ②连续阳>=2 ③close_loc>=0.8 (前置H1->TL有close<EMA20)
        - 入场 = 信号后「先回调确认、再突破高1」逐根挂 Buy Stop (版本B)

约束: 只读本地库, 不改生产代码, 不写库。产物写入 archive/mtr_backtest/。
"""
import sqlite3
import sys
import os
import json
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.append(os.getcwd())
from core.strategies.mtr_structural_v35 import MTRStructuralEngineV35
from core.backtest_engine import simulate_trade_unified, wilson_ci

# ============================================================
# 参数
# ============================================================
PROJECT_ROOT = os.getcwd()
DB_PATH = os.path.join(PROJECT_ROOT, "data", "baostock.db")
OUT_DIR = os.path.join(PROJECT_ROOT, "archive", "mtr_backtest")
SYMBOL = "603650"
ADJ = "qfq"                      # 前复权
NEW_SIGNAL_WINDOW = 40           # TL后找NEW信号K的窗口(根)
B_SCAN_WINDOW = 250              # 信号后找"回调+突破"的最大窗口(根)
RISK_MULT = 2.0                 # TP = entry + 2R
MAX_HOLD = 60                    # 最大持仓根数

# ============================================================
# 数据
# ============================================================
def load_symbol(db_path, symbol, adj):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only=1")
    df = pd.read_sql_query(
        "SELECT trade_date AS date, open, high, low, close, volume FROM daily_bars "
        "WHERE symbol=? AND adjust=? ORDER BY trade_date",
        conn, params=(symbol, adj),
    )
    conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def add_indicators(df):
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
# 结构宇宙 (复用生产引擎)
# ============================================================
def collect_structures(df):
    """逐根调用生产匹配器, 收集去重后的 MTR 结构 (H0/L1/H1/TL)。"""
    engine = MTRStructuralEngineV35(ema_period=20)
    swings = engine.find_swing_points(df, window=5)
    structures = {}
    n = len(df)
    start = 120  # 预热, 避免早期数据不足
    for ci in range(start, n):
        res = engine.match_mtr_pattern(df, swings, ci)
        if not res or res.get("stage") != "SETUP_READY":
            continue
        pts = res["points"]
        key = (pts["L1"].index, pts["H1"].index)
        # 同一(L1,H1)取最新(最终成型)的TL
        structures[key] = res
    return list(structures.values())


# ============================================================
# 信号K / 入场 (OLD vs NEW 版本B)
# ============================================================
def find_old_signal(df, tl_idx):
    """生产原逻辑: TL后16根内首根 阳线+收上半部。复用引擎自带函数。"""
    engine = MTRStructuralEngineV35(ema_period=20)
    sb = engine._find_signal_bar(df, type("T", (), {"index": tl_idx})())
    return sb


def find_new_signal(df, tl_idx):
    """
    NEW 信号K (版本B): TL后 NEW_SIGNAL_WINDOW 根内, 首个同时满足:
      ① close > EMA20
      ② 连续阳线 >= 2 (本根与前一根皆阳)
      ③ close_loc = (close-low)/(high-low) >= 0.8
    前置: H1->TL 段内已有 close<EMA20 (TL 本身 close<EMA, 已满足, 不额外判)。
    返回信号K索引, 否则 None。
    """
    close = df["close"].values
    open_ = df["open"].values
    high = df["high"].values
    low = df["low"].values
    ema = df["ema20"].values
    end = min(len(df), tl_idx + 1 + NEW_SIGNAL_WINDOW)
    for k in range(tl_idx + 1, end):
        c, o, h, lo = close[k], open_[k], high[k], low[k]
        if h <= lo:
            continue
        close_loc = (c - lo) / (h - lo)
        is_bull = c > o
        prev_bull = (k - 1 >= 0) and (close[k - 1] > open_[k - 1])
        if is_bull and prev_bull and close_loc >= 0.8 and c > ema[k]:
            return k
    return None


def entry_version_b(df, sig_idx, sl):
    """
    版本B入场: 信号后先等一次"没创新高"的K(=回调确认), 锁定前高=ref,
    之后任一根 high>ref 即成交 @ref。等待期 low<=sl 撤单。
    返回 (status, entry_price, entry_idx) ——
      status in {'FILLED','SKIP_NO_PULLBACK','INVALIDATED'}
    """
    high = df["high"].values
    low = df["low"].values
    running_max = float(high[sig_idx])
    pullback = False
    ref = None
    end = min(len(df), sig_idx + 1 + B_SCAN_WINDOW)
    for k in range(sig_idx + 1, end):
        lo = float(low[k])
        hi = float(high[k])
        if lo <= sl:
            return ("INVALIDATED", None, None)
        if not pullback:
            if hi > running_max:
                running_max = hi
            else:
                pullback = True
                ref = running_max
        else:
            if hi > ref:
                return ("FILLED", ref, k)
    # 走到窗口末仍无成交
    if pullback and ref is None:
        return ("SKIP_NO_BREAKOUT", None, None)
    return ("SKIP_NO_PULLBACK", None, None)


# ============================================================
# 统计
# ============================================================
def compute_bars_held(df, entry_date, exit_date):
    """回测引擎返回 bars_held=0 (写死缺陷), 这里用日期差补算真实持仓根数。"""
    try:
        ei = df.index[df["date"] == pd.Timestamp(entry_date)]
        xi = df.index[df["date"] == pd.Timestamp(exit_date)]
        if len(ei) and len(xi):
            return int(xi[0] - ei[0])
    except Exception:
        pass
    return 0


def summarize(trades):
    """trades: list of sim result dicts (已触发, WIN/LOSS/HOLDING)."""
    n = len(trades)
    wins = sum(1 for t in trades if t["status"] == "WIN")
    ci = wilson_ci(wins, n)
    net_R = [t["net_R"] for t in trades]
    gross_R = [t["gross_R"] for t in trades]
    bars = [t.get("bars_held", 0) for t in trades]
    ev = round(sum(net_R) / n, 4) if n else 0.0
    return {
        "trades": n,
        "wins": wins,
        "win_rate_pct": round(wins / n * 100, 2) if n else 0.0,
        "win_ci_95": ci,
        "ev_net_R": ev,
        "avg_net_R": round(sum(net_R) / n, 4) if n else 0.0,
        "avg_gross_R": round(sum(gross_R) / n, 4) if n else 0.0,
        "avg_bars_held": round(sum(bars) / n, 1) if n else 0.0,
    }


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] MTR 单股回测启动  symbol={SYMBOL} adj={ADJ}")
    os.makedirs(OUT_DIR, exist_ok=True)

    df = load_symbol(DB_PATH, SYMBOL, ADJ)
    if df.empty:
        alt = load_symbol(DB_PATH, SYMBOL, "hfq")
        df = alt if not alt.empty else df
        print(f"  qfq 为空, 改用可用复权, 行数={len(df)}")
    print(f"  数据行数: {len(df)}  ({df['date'].iloc[0]:%Y-%m-%d} ~ {df['date'].iloc[-1]:%Y-%m-%d})")
    df = add_indicators(df)

    print("[1/4] 收集 MTR 结构 (复用生产引擎)...")
    structures = collect_structures(df)
    print(f"  结构数: {len(structures)}")

    old_rows = []
    new_rows = []
    rows_out = []

    old_invalid = 0
    new_invalid = 0
    new_skip_pullback = 0
    new_skip_breakout = 0

    for res in structures:
        pts = res["points"]
        l1 = pts["L1"]
        h1 = pts["H1"]
        tl = pts["TL"]
        l1_idx, h1_idx, tl_idx = l1.index, h1.index, tl.index
        sl = min(l1.price, tl.price) - 0.01

        # ---- OLD ----
        old_sb = find_old_signal(df, tl_idx)
        old_status = "NO_SIGNAL"
        old_entry = None
        old_sim = None
        if old_sb:
            old_entry = float(old_sb["high"])
            old_sim = simulate_trade_unified(
                df, old_sb["idx"], old_entry, sl,
                risk_mult=RISK_MULT, max_hold=MAX_HOLD,
            )
            old_status = old_sim["status"]
            if old_sim["status"] in ("WIN", "LOSS"):
                old_sim["bars_held"] = compute_bars_held(
                    df, old_sim["entry_date"], old_sim["exit_date"])
                old_rows.append(old_sim)
            elif old_sim["status"] == "INVALIDATED":
                old_invalid += 1

        # ---- NEW (版本B) ----
        new_sig_idx = find_new_signal(df, tl_idx)
        new_status = "NO_SIGNAL"
        new_entry = None
        new_sim = None
        if new_sig_idx is not None:
            estat, new_entry, entry_idx = entry_version_b(df, new_sig_idx, sl)
            if estat == "FILLED":
                new_sim = simulate_trade_unified(
                    df, entry_idx - 1, new_entry, sl,
                    risk_mult=RISK_MULT, max_hold=MAX_HOLD,
                )
                new_status = new_sim["status"]
                if new_sim["status"] in ("WIN", "LOSS"):
                    new_sim["bars_held"] = compute_bars_held(
                        df, new_sim["entry_date"], new_sim["exit_date"])
                    new_rows.append(new_sim)
                elif new_sim["status"] == "INVALIDATED":
                    new_invalid += 1
            elif estat == "INVALIDATED":
                new_status = "INVALIDATED_WAIT"
                new_invalid += 1
            elif estat == "SKIP_NO_PULLBACK":
                new_status = "SKIP_NO_PULLBACK"
                new_skip_pullback += 1
            elif estat == "SKIP_NO_BREAKOUT":
                new_status = "SKIP_NO_BREAKOUT"
                new_skip_breakout += 1

        rows_out.append({
            "l1_date": str(df["date"].iloc[l1_idx])[:10],
            "h1_date": str(df["date"].iloc[h1_idx])[:10],
            "tl_date": str(df["date"].iloc[tl_idx])[:10],
            "sl": round(sl, 2),
            "old_sig_date": str(df["date"].iloc[old_sb["idx"]])[:10] if old_sb else "",
            "old_entry": round(old_entry, 2) if old_entry else "",
            "old_status": old_status,
            "old_net_R": old_sim["net_R"] if old_sim else "",
            "new_sig_date": str(df["date"].iloc[new_sig_idx])[:10] if new_sig_idx is not None else "",
            "new_entry": round(new_entry, 2) if new_entry else "",
            "new_status": new_status,
            "new_net_R": new_sim["net_R"] if new_sim else "",
        })

    print("[2/4] 汇总统计...")
    old_summary = summarize(old_rows)
    new_summary = summarize(new_rows)

    print("[3/4] 写出产物...")
    # 明细 csv
    import csv
    sig_csv = os.path.join(OUT_DIR, f"mtr_{SYMBOL}_signals.csv")
    with open(sig_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()) if rows_out else [])
        w.writeheader()
        for r in rows_out:
            w.writerow(r)
    print(f"  {sig_csv}")

    # 汇总 json
    summary = {
        "meta": {
            "symbol": SYMBOL, "adj": ADJ,
            "n_structures": len(structures),
            "old_signal_logic": "TL后首根阳线收上半部; 入场=信号K最高价",
            "new_signal_logic": "TL后首根close>EMA20 & 连阳>=2 & close_loc>=0.8; 入场=版本B(先回调再高1)",
            "sl": "min(L1,TL)-0.01", "tp": "2R", "max_hold": MAX_HOLD,
            "new_signal_window": NEW_SIGNAL_WINDOW, "b_scan_window": B_SCAN_WINDOW,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "old": old_summary,
        "new": new_summary,
        "old_no_entry_invalidated": old_invalid,
        "new_no_entry_invalidated": new_invalid,
        "new_skip_no_pullback": new_skip_pullback,
        "new_skip_no_breakout": new_skip_breakout,
    }
    sum_json = os.path.join(OUT_DIR, f"mtr_{SYMBOL}_summary.json")
    with open(sum_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  {sum_json}")

    print("[4/4] 控制台报告")
    print("=" * 64)
    print(f"结构总数: {len(structures)}")
    print("-" * 64)
    print("OLD 生产逻辑:")
    print(f"  触发交易: {old_summary['trades']}  等待期破SL撤单: {old_invalid}")
    if old_summary["trades"]:
        print(f"  净胜率: {old_summary['win_rate_pct']}%  Wilson95%: {old_summary['win_ci_95']}")
        print(f"  EV(净R): {old_summary['ev_net_R']}  平均净R: {old_summary['avg_net_R']}")
        print(f"  平均持仓: {old_summary['avg_bars_held']} 根")
    print("-" * 64)
    print("NEW 版本B (信号K+先回调再高1):")
    print(f"  触发交易: {new_summary['trades']}  等待期破SL撤单: {new_invalid}")
    print(f"  踏空(无回调): {new_skip_pullback}  回调后未突破: {new_skip_breakout}")
    if new_summary["trades"]:
        print(f"  净胜率: {new_summary['win_rate_pct']}%  Wilson95%: {new_summary['win_ci_95']}")
        print(f"  EV(净R): {new_summary['ev_net_R']}  平均净R: {new_summary['avg_net_R']}")
        print(f"  平均持仓: {new_summary['avg_bars_held']} 根")
    print("=" * 64)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"完成! 耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
