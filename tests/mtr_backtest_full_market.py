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
  S2/S3 (2026-09-28 用户指定): 同一信号K口径(连阳>=2 / >=3)
        - 入场 = 信号K最高价挂 Buy Stop (与 OLD 同假设, 便于隔离信号K变量)
        - SL = min(L1,TL)-0.01 ; TP 由环境变量 MTR_TP_R 决定 (默认 2R)

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
RISK_MULT = float(os.environ.get("MTR_TP_R", "2.0"))  # TP = entry + N*R (默认2R; 用 MTR_TP_R 覆盖)
MAX_HOLD = 60                    # 最大持仓根数
MIN_BARS = 130                   # 少于此行数的标的跳过 (warmup=120)
# 信号K 口径 (2026-09-28 新增):
#   strict (默认) = 先锁 TL 后【首次收站 EMA20】那根, 再回验; 不达标 -> 本结构无信号 (不顺延)
#   loose         = 窗口内循环, 首个同时满足全部条件的 K 即信号K (允许顺延到后面)
# 两版差异已由 tests/mtr_signal_semantics_diff.py 全市场量化:
#   连阳>=2 严格命中 12,347 / 宽松 32,947; 连阳>=3 严格 5,182 / 宽松 25,840
SIGNAL_MODE = (os.environ.get("MTR_SIGNAL_MODE", "strict") or "strict").strip().lower()
if SIGNAL_MODE not in ("strict", "loose"):
    raise SystemExit(f"MTR_SIGNAL_MODE 只支持 strict / loose, 收到: {SIGNAL_MODE!r}")
# 产物名后缀: strict 保持旧名(向后兼容), 其他口径加后缀避免互相覆盖
SUFFIX = "" if SIGNAL_MODE == "strict" else f"_{SIGNAL_MODE}"
# 探速(设定了 LIMIT)也强制加后缀 —— 否则探速会覆盖全量产物。
# 教训(2026-09-28): 一次 MTR_LIMIT=150 的验证跑被超时杀掉, 却已把全量
# full_market_signals_2R.csv (39,097 行) 覆盖成 1,423 行的半成品。
if LIMIT:
    SUFFIX += f"_L{LIMIT}"


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


def _consec_bull_at(close, open_, k):
    """从第 k 根往前回溯的连续阳线根数 (含第 k 根自身)。"""
    n = 0
    i = k
    while i >= 0 and close[i] > open_[i]:
        n += 1
        i -= 1
    return n


def _find_new_signal_loose(df, tl_idx, min_consec_bull=2):
    """宽松口径: 窗口内循环, 首个【同时】满足 (阳线 & 连阳>=N & close_loc>=0.8 &
    close>EMA20) 的 K 即信号K —— 首次站上均线那根不达标时可往后顺延。
    (2026-09-28 之前的单股脚本用的就是这一版; 全市场信号量约为严格版的 2.7~5.0 倍。)"""
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
        if (c > o and _consec_bull_at(close, open_, k) >= min_consec_bull
                and close_loc >= 0.8 and c > ema[k]):
            return k
    return None


def find_new_signal(df, tl_idx, min_consec_bull=2):
    """信号K 定位。口径由全局 SIGNAL_MODE 决定 (strict 默认 / loose), 见参数区注释。

    strict (默认) = TL 后【首次收站 EMA20】的那根 K, 再回验:
        ① 该根自身为阳线 (由 连阳 >= N 保证) ② 连续阳线(往前回溯) >= N ③ close_loc >= 0.8
        关键顺序 (2026-09-23 用户纠正): 先锁首次站上那根再判 ——
        绝不允许顺延; 若首次站上那根不达标, 本结构【无信号】返回 None。
    loose = 窗口内循环取首个同时达标者, 允许顺延 (见 _find_new_signal_loose)。

    H1->TL 段内已有 close<EMA20 (TL 本身 close<EMA 已保证), 不额外判。"""
    if SIGNAL_MODE == "loose":
        return _find_new_signal_loose(df, tl_idx, min_consec_bull)
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
    # 第二步：回验这根信号K —— 连续阳线(从本根往前回溯 min_consec_bull 根) 且 收在K线顶部
    if first_above - 1 < 0:
        return None
    h, lo = high[first_above], low[first_above]
    if h <= lo:
        return None
    close_loc = (close[first_above] - lo) / (h - lo)
    if (_consec_bull_at(close, open_, first_above) >= min_consec_bull
            and close_loc >= 0.8):
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
    s2_rows, s3_rows = [], []
    old_invalid = new_invalid = new_skip_pb = new_skip_bo = 0
    s2_invalid = s3_invalid = 0
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

        # S2 / S3: 同一信号K口径(连阳>=2 / >=3) + 信号K最高价挂 Buy Stop, SL=MTR极值低点
        s_sig = {}
        for tag, mincb in (("s2", 2), ("s3", 3)):
            sig_idx = find_new_signal(df, tl_idx, min_consec_bull=mincb)
            st, en, sim = "NO_SIGNAL", None, None
            if sig_idx is not None:
                en = float(df["high"].iloc[sig_idx])
                sim = simulate_trade_unified(
                    df, sig_idx, en, sl,
                    risk_mult=RISK_MULT, max_hold=MAX_HOLD)
                st = sim["status"]
                if sim["status"] in ("WIN", "LOSS"):
                    sim["bars_held"] = compute_bars_held(
                        df, sim["entry_date"], sim["exit_date"])
                    (s2_rows if tag == "s2" else s3_rows).append(sim)
                elif sim["status"] == "INVALIDATED":
                    if tag == "s2":
                        s2_invalid += 1
                    else:
                        s3_invalid += 1
            s_sig[tag] = (sig_idx, en, st, sim)

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
            "s2_sig_date": str(df["date"].iloc[s_sig["s2"][0]])[:10] if s_sig["s2"][0] is not None else "",
            "s2_entry": round(s_sig["s2"][1], 2) if s_sig["s2"][1] else "",
            "s2_status": s_sig["s2"][2],
            "s2_net_R": s_sig["s2"][3]["net_R"] if s_sig["s2"][3] else "",
            "s3_sig_date": str(df["date"].iloc[s_sig["s3"][0]])[:10] if s_sig["s3"][0] is not None else "",
            "s3_entry": round(s_sig["s3"][1], 2) if s_sig["s3"][1] else "",
            "s3_status": s_sig["s3"][2],
            "s3_net_R": s_sig["s3"][3]["net_R"] if s_sig["s3"][3] else "",
        })

    return {
        "symbol": symbol, "adj": adj, "n_struct": len(structures),
        "old_rows": old_rows, "new_rows": new_rows,
        "s2_rows": s2_rows, "s3_rows": s3_rows,
        "old_invalid": old_invalid, "new_invalid": new_invalid,
        "new_skip_pb": new_skip_pb, "new_skip_bo": new_skip_bo,
        "s2_invalid": s2_invalid, "s3_invalid": s3_invalid,
        "out_rows": out_rows,
    }


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] MTR 全市场回测启动  LIMIT={LIMIT or 'ALL'} TP={RISK_MULT:g}R  "
          f"信号K口径={SIGNAL_MODE}")
    os.makedirs(OUT_DIR, exist_ok=True)

    sym_map = get_symbol_adjust_map(DB_PATH)
    symbols = sorted(sym_map.keys())
    if LIMIT:
        symbols = symbols[:LIMIT]
    print(f"  标的池: 全库 {len(sym_map)} 只, 本次处理 {len(symbols)} 只")

    all_old, all_new = [], []
    all_s2, all_s3 = [], []
    year_old = defaultdict(list)
    year_new = defaultdict(list)
    year_s2 = defaultdict(list)
    year_s3 = defaultdict(list)
    totals = dict(symbols=0, symbols_with_struct=0, structures=0,
                  old_invalid=0, new_invalid=0, new_skip_pb=0, new_skip_bo=0,
                  s2_invalid=0, s3_invalid=0, errors=0)

    CSV_FIELDS = ["symbol", "adj", "l1_date", "h1_date", "tl_date", "sl",
                  "old_sig_date", "old_entry", "old_status", "old_net_R",
                  "new_sig_date", "new_entry", "new_status", "new_net_R",
                  "s2_sig_date", "s2_entry", "s2_status", "s2_net_R",
                  "s3_sig_date", "s3_entry", "s3_status", "s3_net_R"]
    sig_csv = os.path.join(OUT_DIR, f"full_market_signals_{RISK_MULT:g}R{SUFFIX}.csv")
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
            totals["s2_invalid"] += r["s2_invalid"]
            totals["s3_invalid"] += r["s3_invalid"]
            all_old.extend(r["old_rows"])
            all_new.extend(r["new_rows"])
            all_s2.extend(r["s2_rows"])
            all_s3.extend(r["s3_rows"])
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
            for t in r["s2_rows"]:
                try:
                    year_s2[pd.Timestamp(t["entry_date"]).year].append(t)
                except Exception:
                    pass
            for t in r["s3_rows"]:
                try:
                    year_s3[pd.Timestamp(t["entry_date"]).year].append(t)
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
    s2_sum = summarize(all_s2)
    s3_sum = summarize(all_s3)

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
            "new_signal_logic": ("TL后首次收站EMA20那根再回验连阳>=2 & close_loc>=0.8"
                                 " (strict, 不顺延); 入场=版本B(先回调再高1)"
                                 if SIGNAL_MODE == "strict" else
                                 "TL后40根内首个同时满足 close>EMA20 & 连阳>=2 & close_loc>=0.8"
                                 " (loose, 允许顺延); 入场=版本B(先回调再高1)"),
            "signal_mode": SIGNAL_MODE,
            "sl": "min(L1,TL)-0.01", "tp": f"{RISK_MULT:g}R", "max_hold": MAX_HOLD,
            "new_signal_window": NEW_SIGNAL_WINDOW, "b_scan_window": B_SCAN_WINDOW,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "totals": totals,
        "old": old_sum, "new": new_sum, "s2": s2_sum, "s3": s3_sum,
        "old_no_entry_invalidated": totals["old_invalid"],
        "new_no_entry_invalidated": totals["new_invalid"],
        "new_skip_no_pullback": totals["new_skip_pb"],
        "new_skip_no_breakout": totals["new_skip_bo"],
        "s2_no_entry_invalidated": totals["s2_invalid"],
        "s3_no_entry_invalidated": totals["s3_invalid"],
        "by_year_old": year_block(year_old),
        "by_year_new": year_block(year_new),
        "by_year_s2": year_block(year_s2),
        "by_year_s3": year_block(year_s3),
    }
    sum_json = os.path.join(OUT_DIR, f"full_market_summary_{RISK_MULT:g}R{SUFFIX}.json")
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
    print("S2 信号K高点挂单 (连阳>=2):")
    print(f"  触发交易: {s2_sum['trades']}  等待期破SL撤单: {totals['s2_invalid']}")
    if s2_sum["trades"]:
        print(f"  净胜率: {s2_sum['win_rate_pct']}%  Wilson95%: {s2_sum['win_ci_95']}")
        print(f"  EV(净R): {s2_sum['ev_net_R']}  平均净R: {s2_sum['avg_net_R']}")
        print(f"  平均持仓: {s2_sum['avg_bars_held']} 根")
    print("-" * 72)
    print("S3 信号K高点挂单 (连阳>=3):")
    print(f"  触发交易: {s3_sum['trades']}  等待期破SL撤单: {totals['s3_invalid']}")
    if s3_sum["trades"]:
        print(f"  净胜率: {s3_sum['win_rate_pct']}%  Wilson95%: {s3_sum['win_ci_95']}")
        print(f"  EV(净R): {s3_sum['ev_net_R']}  平均净R: {s3_sum['avg_net_R']}")
        print(f"  平均持仓: {s3_sum['avg_bars_held']} 根")
    print("-" * 72)
    print("按年 (OLD / S3 净胜率% , EV净R):")
    yrs = sorted(set(list(year_old.keys()) + list(year_s3.keys())))
    for y in yrs:
        o = year_old.get(y, [])
        s3y = year_s3.get(y, [])
        os_ = summarize(o) if o else None
        s3s_ = summarize(s3y) if s3y else None
        ostr = f"{os_['win_rate_pct']}%/{os_['ev_net_R']}(n={os_['trades']})" if os_ else "-"
        s3str = f"{s3s_['win_rate_pct']}%/{s3s_['ev_net_R']}(n={s3s_['trades']})" if s3s_ else "-"
        print(f"  {y}: OLD {ostr}   S3 {s3str}")
    print("=" * 72)
    el = (datetime.now() - t0).total_seconds()
    print(f"完成! 耗时 {el:.1f}s")


if __name__ == "__main__":
    main()
