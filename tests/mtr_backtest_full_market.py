# -*- coding: utf-8 -*-
"""
MTR 全市场回测 —— OLD 生产逻辑 vs NEW 信号K/版本B入场
==============================================================
复用项目既有方法 (与 tests/mtr_backtest_single_stock.py 完全同源的逻辑):
  - 结构判定: core.strategies.mtr_structural_v35.MTRStructuralEngineV35 (逐根 match_mtr_pattern)
  - 退出模拟: core.backtest_engine.simulate_trade_unified (统一成本口径)
  - Wilson CI: core.backtest_engine.wilson_ci

对比口径 (公平, 与单股脚本一致):
  OLD: TL后首根阳线收上半部(close_loc>=0.5); 入场=信号K最高价 Buy Stop。
  NEW: 用户 2026-09-23 修订选 B
        - 信号K = ①TL后首根 close>EMA20 ②连续阳>=2 ③close_loc>=0.8
        - 入场 = 信号后「先回调确认、再突破高1」逐根挂 Buy Stop (版本B)

范围: 全量日线 data/baostock.db 所有标的 (取行数最多的复权版本)。
约束: 只读本地库, 不改生产代码, 不写库。产物写入 archive/mtr_backtest/。

用法:
  MTR_LIMIT=0  python tests/mtr_backtest_full_market.py        # 全市场
  MTR_LIMIT=150 python tests/mtr_backtest_full_market.py        # 仅前150只(探速)
"""
import sqlite3
import sys
import os
import json
import csv
import time
import traceback
from datetime import datetime
from collections import defaultdict

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
LIMIT = int(os.environ.get("MTR_LIMIT", "0") or "0")   # 0 = 全市场
NEW_SIGNAL_WINDOW = 40           # TL后找NEW信号K的窗口(根)
B_SCAN_WINDOW = 250              # 信号后找"回调+突破"的最大窗口(根)
RISK_MULT = 2.0                 # TP = entry + 2R
MAX_HOLD = 60                    # 最大持仓根数
MIN_BARS = 130                   # 少于此行数的标的跳过 (warmup=120)


# ============================================================
# 数据
# ============================================================
def get_symbol_adjust_map(db_path):
    """返回 {symbol: 行数最多的adjust}。一次查询全表。"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only=1")
    rows = conn.execute(
        "SELECT symbol, adjust, COUNT(*) AS c FROM daily_bars GROUP BY symbol, adjust"
    ).fetchall()
    conn.close()
    best = {}
    for sym, adj, c in rows:
        if sym not in best or c > best[sym][1]:
            best[sym] = (adj, c)
    return best


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
# 结构宇宙 (复用生产引擎, 逐根扫描 —— 与单股脚本同源)
# ============================================================
def collect_structures(df):
    engine = MTRStructuralEngineV35(ema_period=20)
    swings = engine.find_swing_points(df, window=5)
    structures = {}
    n = len(df)
    start = 120
    for ci in range(start, n):
        res = engine.match_mtr_pattern(df, swings, ci)
        if not res or res.get("stage") != "SETUP_READY":
            continue
        pts = res["points"]
        key = (pts["L1"].index, pts["H1"].index)
        structures[key] = res
    return list(structures.values())


# ============================================================
# 信号K / 入场 (OLD vs NEW 版本B)
# ============================================================
def find_old_signal(df, tl_idx):
    engine = MTRStructuralEngineV35(ema_period=20)
    sb = engine._find_signal_bar(df, type("T", (), {"index": tl_idx})())
    return sb


def find_new_signal(df, tl_idx):
    """NEW 信号K = TL 后【首次收站 EMA20】的那根 K 线，再回验：
    ① 该根自身为阳线 ② 它前一根也是阳线(连续阳线>=2) ③ 收在K线顶部(close_loc>=0.8)。
    关键顺序（2026-09-23 用户纠正）：先锁「首次收站EMA20」这根，再判连阳——
    绝不允许顺延到后面第三次才站上的 K 线当信号K；若首次站上的那根连阳不达标，
    本结构【无信号】返回 None。
    H1->TL 段内已有 close<EMA20（TL 本身 close<EMA 已保证），不额外判。"""
    close = df["close"].values
    open_ = df["open"].values
    high = df["high"].values
    low = df["low"].values
    ema = df["ema20"].values
    end = min(len(df), tl_idx + 1 + NEW_SIGNAL_WINDOW)
    # 第一步：先定位 TL 后【首次收站 EMA20】的那根 K 线（不看连阳/收盘位置）
    first_above = None
    for k in range(tl_idx + 1, end):
        if close[k] > ema[k]:
            first_above = k
            break
    if first_above is None:
        return None
    # 第二步：回验这根信号K —— 连续阳线(自身+前一根均阳) 且 收在K线顶部
    if first_above - 1 < 0:
        return None
    is_bull = close[first_above] > open_[first_above]
    prev_bull = close[first_above - 1] > open_[first_above - 1]
    h, lo = high[first_above], low[first_above]
    if h <= lo:
        return None
    close_loc = (close[first_above] - lo) / (h - lo)
    if is_bull and prev_bull and close_loc >= 0.8:
        return first_above
    return None


def entry_version_b(df, sig_idx, sl):
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
    if pullback and ref is None:
        return ("SKIP_NO_BREAKOUT", None, None)
    return ("SKIP_NO_PULLBACK", None, None)


def compute_bars_held(df, entry_date, exit_date):
    try:
        ei = df.index[df["date"] == pd.Timestamp(entry_date)]
        xi = df.index[df["date"] == pd.Timestamp(exit_date)]
        if len(ei) and len(xi):
            return int(xi[0] - ei[0])
    except Exception:
        pass
    return 0


# ============================================================
# 统计
# ============================================================
def summarize(trades):
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
# 单只标的回测
# ============================================================
def backtest_symbol(symbol, adj, df):
    df = add_indicators(df)
    structures = collect_structures(df)
    old_rows, new_rows = [], []
    old_invalid = new_invalid = new_skip_pb = new_skip_bo = 0
    out_rows = []

    for res in structures:
        pts = res["points"]
        l1, h1, tl = pts["L1"], pts["H1"], pts["TL"]
        l1_idx, h1_idx, tl_idx = l1.index, h1.index, tl.index
        sl = min(l1.price, tl.price) - 0.01

        # OLD
        old_sb = find_old_signal(df, tl_idx)
        old_status, old_entry, old_sim = "NO_SIGNAL", None, None
        if old_sb:
            old_entry = float(old_sb["high"])
            old_sim = simulate_trade_unified(
                df, old_sb["idx"], old_entry, sl,
                risk_mult=RISK_MULT, max_hold=MAX_HOLD)
            old_status = old_sim["status"]
            if old_sim["status"] in ("WIN", "LOSS"):
                old_sim["bars_held"] = compute_bars_held(
                    df, old_sim["entry_date"], old_sim["exit_date"])
                old_rows.append(old_sim)
            elif old_sim["status"] == "INVALIDATED":
                old_invalid += 1

        # NEW 版本B
        new_sig_idx = find_new_signal(df, tl_idx)
        new_status, new_entry, new_sim = "NO_SIGNAL", None, None
        if new_sig_idx is not None:
            estat, new_entry, entry_idx = entry_version_b(df, new_sig_idx, sl)
            if estat == "FILLED":
                new_sim = simulate_trade_unified(
                    df, entry_idx - 1, new_entry, sl,
                    risk_mult=RISK_MULT, max_hold=MAX_HOLD)
                new_status = new_sim["status"]
                if new_sim["status"] in ("WIN", "LOSS"):
                    new_sim["bars_held"] = compute_bars_held(
                        df, new_sim["entry_date"], new_sim["exit_date"])
                    new_rows.append(new_sim)
                elif new_sim["status"] == "INVALIDATED":
                    new_invalid += 1
            elif estat == "INVALIDATED":
                new_status, new_invalid = "INVALIDATED_WAIT", new_invalid + 1
            elif estat == "SKIP_NO_PULLBACK":
                new_status, new_skip_pb = "SKIP_NO_PULLBACK", new_skip_pb + 1
            elif estat == "SKIP_NO_BREAKOUT":
                new_status, new_skip_bo = "SKIP_NO_BREAKOUT", new_skip_bo + 1

        out_rows.append({
            "symbol": symbol, "adj": adj,
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

    return {
        "symbol": symbol, "adj": adj, "n_struct": len(structures),
        "old_rows": old_rows, "new_rows": new_rows,
        "old_invalid": old_invalid, "new_invalid": new_invalid,
        "new_skip_pb": new_skip_pb, "new_skip_bo": new_skip_bo,
        "out_rows": out_rows,
    }


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] MTR 全市场回测启动  LIMIT={LIMIT or 'ALL'}")
    os.makedirs(OUT_DIR, exist_ok=True)

    sym_map = get_symbol_adjust_map(DB_PATH)
    symbols = sorted(sym_map.keys())
    if LIMIT:
        symbols = symbols[:LIMIT]
    print(f"  标的池: 全库 {len(sym_map)} 只, 本次处理 {len(symbols)} 只")

    all_old, all_new = [], []
    year_old = defaultdict(list)
    year_new = defaultdict(list)
    totals = dict(symbols=0, symbols_with_struct=0, structures=0,
                  old_invalid=0, new_invalid=0, new_skip_pb=0, new_skip_bo=0,
                  errors=0)

    CSV_FIELDS = ["symbol", "adj", "l1_date", "h1_date", "tl_date", "sl",
                  "old_sig_date", "old_entry", "old_status", "old_net_R",
                  "new_sig_date", "new_entry", "new_status", "new_net_R"]
    sig_csv = os.path.join(OUT_DIR, "full_market_signals.csv")
    csv_f = open(sig_csv, "w", newline="", encoding="utf-8")
    csv_w = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    csv_w.writeheader()

    for i, sym in enumerate(symbols, 1):
        adj, _ = sym_map[sym]
        try:
            df = load_symbol(DB_PATH, sym, adj)
            if df.empty or len(df) < MIN_BARS:
                continue
            r = backtest_symbol(sym, adj, df)
            totals["symbols"] += 1
            totals["structures"] += r["n_struct"]
            if r["n_struct"]:
                totals["symbols_with_struct"] += 1
            totals["old_invalid"] += r["old_invalid"]
            totals["new_invalid"] += r["new_invalid"]
            totals["new_skip_pb"] += r["new_skip_pb"]
            totals["new_skip_bo"] += r["new_skip_bo"]
            all_old.extend(r["old_rows"])
            all_new.extend(r["new_rows"])
            for row in r["out_rows"]:
                csv_w.writerow(row)
            csv_f.flush()
            for t in r["old_rows"]:
                try:
                    year_old[pd.Timestamp(t["entry_date"]).year].append(t)
                except Exception:
                    pass
            for t in r["new_rows"]:
                try:
                    year_new[pd.Timestamp(t["entry_date"]).year].append(t)
                except Exception:
                    pass
        except Exception as e:
            totals["errors"] += 1
            if totals["errors"] <= 5:
                print(f"  [ERR] {sym}: {e}")
                traceback.print_exc()

        if i % 50 == 0:
            el = (datetime.now() - t0).total_seconds()
            print(f"  [{i}/{len(symbols)}] 已用 {el:.0f}s | 结构 {totals['structures']} | "
                  f"OLD {len(all_old)} NEW {len(all_new)}", flush=True)

    csv_f.close()

    print("[1/3] 汇总统计...")
    old_sum = summarize(all_old)
    new_sum = summarize(all_new)

    def year_block(bucket):
        out = {}
        for y in sorted(bucket.keys()):
            out[str(y)] = summarize(bucket[y])
        return out

    print("[2/3] 写出产物...")
    print(f"  {sig_csv}  (增量落盘, {totals['structures']} 行结构明细)")

    summary = {
        "meta": {
            "scope": "全市场日线" if not LIMIT else f"样本前{LIMIT}只",
            "limit": LIMIT,
            "old_signal_logic": "TL后首根阳线收上半部; 入场=信号K最高价",
            "new_signal_logic": "TL后首根close>EMA20 & 连阳>=2 & close_loc>=0.8; 入场=版本B(先回调再高1)",
            "sl": "min(L1,TL)-0.01", "tp": "2R", "max_hold": MAX_HOLD,
            "new_signal_window": NEW_SIGNAL_WINDOW, "b_scan_window": B_SCAN_WINDOW,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "totals": totals,
        "old": old_sum, "new": new_sum,
        "old_no_entry_invalidated": totals["old_invalid"],
        "new_no_entry_invalidated": totals["new_invalid"],
        "new_skip_no_pullback": totals["new_skip_pb"],
        "new_skip_no_breakout": totals["new_skip_bo"],
        "by_year_old": year_block(year_old),
        "by_year_new": year_block(year_new),
    }
    sum_json = os.path.join(OUT_DIR, "full_market_summary.json")
    with open(sum_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  {sum_json}")

    print("[3/3] 控制台报告")
    print("=" * 72)
    print(f"处理标的: {totals['symbols']} 只 (含结构 {totals['symbols_with_struct']} 只) | "
          f"结构总数: {totals['structures']} | 异常: {totals['errors']}")
    print("-" * 72)
    print("OLD 生产逻辑:")
    print(f"  触发交易: {old_sum['trades']}  等待期破SL撤单: {totals['old_invalid']}")
    if old_sum["trades"]:
        print(f"  净胜率: {old_sum['win_rate_pct']}%  Wilson95%: {old_sum['win_ci_95']}")
        print(f"  EV(净R): {old_sum['ev_net_R']}  平均净R: {old_sum['avg_net_R']}")
        print(f"  平均持仓: {old_sum['avg_bars_held']} 根")
    print("-" * 72)
    print("NEW 版本B (信号K+先回调再高1):")
    print(f"  触发交易: {new_sum['trades']}  等待期破SL撤单: {totals['new_invalid']}")
    print(f"  踏空(无回调): {totals['new_skip_pb']}  回调后未突破: {totals['new_skip_bo']}")
    if new_sum["trades"]:
        print(f"  净胜率: {new_sum['win_rate_pct']}%  Wilson95%: {new_sum['win_ci_95']}")
        print(f"  EV(净R): {new_sum['ev_net_R']}  平均净R: {new_sum['avg_net_R']}")
        print(f"  平均持仓: {new_sum['avg_bars_held']} 根")
    print("-" * 72)
    print("按年 (OLD / NEW 净胜率% , EV净R):")
    yrs = sorted(set(list(year_old.keys()) + list(year_new.keys())))
    for y in yrs:
        o = year_old.get(y, [])
        nw = year_new.get(y, [])
        os_ = summarize(o) if o else None
        ns_ = summarize(nw) if nw else None
        ostr = f"{os_['win_rate_pct']}%/{os_['ev_net_R']}(n={os_['trades']})" if os_ else "-"
        nstr = f"{ns_['win_rate_pct']}%/{ns_['ev_net_R']}(n={ns_['trades']})" if ns_ else "-"
        print(f"  {y}: OLD {ostr}   NEW {nstr}")
    print("=" * 72)
    el = (datetime.now() - t0).total_seconds()
    print(f"完成! 耗时 {el:.1f}s")


if __name__ == "__main__":
    main()
