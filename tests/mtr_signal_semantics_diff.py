# -*- coding: utf-8 -*-
"""
MTR 信号K 口径差异量化 —— 「先锁不顺延(严格)」vs「循环内可顺延(宽松)」
======================================================================
背景: tests/mtr_backtest_single_stock.py 的 find_new_signal 与
      tests/mtr_backtest_full_market.py 的 find_new_signal 曾口径不一致
      (前者允许顺延, 后者不允许)。2026-09-28 已把单股脚本对齐为严格版。
      本脚本量化两版差异的样本量, 并交叉校验本地实现与全市场脚本一致。

严格版 (= 现两脚本口径, tests/mtr_backtest_full_market.py:141-172):
    第一步 先锁 TL 后【首次收站 EMA20】的那根 K;
    第二步 回验该根 (连阳 >= N 且 close_loc >= 0.8); 不达标 -> 本结构【无信号】。

宽松版 (= 2026-09-28 之前的单股实现):
    在 TL 后窗口内循环, 首个【同时】满足 (阳线 & 连阳>=N & close_loc>=0.8 &
    close>EMA20) 的 K 即信号K -> 首次站上那根不达标时可往后顺延。

理论关系 (由本脚本实测校验):
    宽松版命中索引 >= 严格版命中索引;
    严格版非空时, 两版必相等 (更早的 K 因 close<=EMA20 不可能被宽松版选中);
    => 唯一差异情形 = 严格版 None 且宽松版非空, 即"被顺延救回来的信号"。

只读; 不写任何文件。用法:
  MTR_LIMIT=0   .venv/Scripts/python.exe tests/mtr_signal_semantics_diff.py   # 全市场
  MTR_LIMIT=150 .venv/Scripts/python.exe tests/mtr_signal_semantics_diff.py   # 探速
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

import pandas as pd

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "tests"))

from mtr_backtest_full_market import (  # noqa: E402
    DB_PATH, MIN_BARS, NEW_SIGNAL_WINDOW,
    get_symbol_adjust_map, load_symbol, add_indicators, collect_structures,
    find_new_signal,
)

LIMIT = int(os.environ.get("MTR_LIMIT", "0") or "0")
CB_LEVELS = (2, 3)


def _consec_bull_at(close, open_, k):
    n = 0
    i = k
    while i >= 0 and close[i] > open_[i]:
        n += 1
        i -= 1
    return n


def strict_locked(df, tl_idx, mincb):
    """严格版。返回 (first_above, sig_idx) —— first_above 供滞后量统计用。"""
    close = df["close"].values
    open_ = df["open"].values
    high = df["high"].values
    low = df["low"].values
    ema = df["ema20"].values
    end = min(len(df), tl_idx + 1 + NEW_SIGNAL_WINDOW)
    first_above = None
    for k in range(tl_idx + 1, end):
        if close[k] > ema[k]:
            first_above = k
            break
    if first_above is None or first_above - 1 < 0:
        return first_above, None
    h, lo = high[first_above], low[first_above]
    if h <= lo:
        return first_above, None
    close_loc = (close[first_above] - lo) / (h - lo)
    if (_consec_bull_at(close, open_, first_above) >= mincb
            and close_loc >= 0.8):
        return first_above, first_above
    return first_above, None


def loose_scan(df, tl_idx, mincb):
    """宽松版 (旧单股实现, 允许顺延)。"""
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
        if (c > o and _consec_bull_at(close, open_, k) >= mincb
                and close_loc >= 0.8 and c > ema[k]):
            return k
    return None


def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] 信号K 口径差异量化启动  LIMIT={LIMIT or 'ALL'}")
    print(f"  窗口 NEW_SIGNAL_WINDOW={NEW_SIGNAL_WINDOW} 根")

    sym_map = get_symbol_adjust_map(DB_PATH)
    symbols = sorted(sym_map.keys())
    if LIMIT:
        symbols = symbols[:LIMIT]
    print(f"  标的池: 全库 {len(sym_map)} 只, 本次处理 {len(symbols)} 只", flush=True)

    stat = {c: defaultdict(int) for c in CB_LEVELS}
    lag = {c: [] for c in CB_LEVELS}
    rescued_year = {c: defaultdict(int) for c in CB_LEVELS}
    mismatch = 0
    errors = 0
    n_sym = 0
    n_struct = 0

    for i, sym in enumerate(symbols, 1):
        adj, _ = sym_map[sym]
        try:
            df = load_symbol(DB_PATH, sym, adj)
            if df.empty or len(df) < MIN_BARS:
                continue
            df = add_indicators(df)
            structures = collect_structures(df)
            n_sym += 1
            n_struct += len(structures)
            for res in structures:
                tl_idx = res["points"]["TL"].index
                for cb in CB_LEVELS:
                    first_above, sig_strict = strict_locked(df, tl_idx, cb)
                    sig_loose = loose_scan(df, tl_idx, cb)
                    if sig_strict != find_new_signal(df, tl_idx, min_consec_bull=cb):
                        mismatch += 1
                    s = stat[cb]
                    s["structures"] += 1
                    if sig_strict is not None:
                        s["strict_hit"] += 1
                    if sig_loose is not None:
                        s["loose_hit"] += 1
                    if sig_strict is None and sig_loose is None:
                        s["both_none"] += 1
                    elif sig_strict is not None and sig_loose == sig_strict:
                        s["same_index"] += 1
                    elif sig_strict is None and sig_loose is not None:
                        s["rescued"] += 1
                        lag[cb].append(sig_loose - first_above)
                        try:
                            rescued_year[cb][
                                pd.Timestamp(df["date"].iloc[sig_loose]).year] += 1
                        except Exception:
                            pass
                    else:
                        s["anomaly"] += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  [ERR] {sym}: {e}")
        if i % 100 == 0:
            el = (datetime.now() - t0).total_seconds()
            print(f"  [{i}/{len(symbols)}] 已用 {el:.0f}s | 结构 {n_struct} | "
                  f"救回(连阳2) {stat[2]['rescued']} (连阳3) {stat[3]['rescued']}",
                  flush=True)

    print()
    print("=" * 88)
    print(f"处理标的 {n_sym} 只 | 结构总数 {n_struct} | 异常 {errors} "
          f"| 本地严格版与现脚本不一致 {mismatch} 次 (应为 0)")
    print("=" * 88)
    for cb in CB_LEVELS:
        s = stat[cb]
        st = s["structures"] or 1
        print(f"\n-- 连阳 >= {cb} --")
        print(f"  严格版命中(不顺延)   : {s['strict_hit']:>7}  ({s['strict_hit'] / st * 100:.2f}%)")
        print(f"  宽松版命中(可顺延)   : {s['loose_hit']:>7}  ({s['loose_hit'] / st * 100:.2f}%)")
        print(f"  两者索引完全相同     : {s['same_index']:>7}")
        print(f"  两版都无信号         : {s['both_none']:>7}")
        print(f"  >>> 被顺延救回(净增) : {s['rescued']:>7}  "
              f"(占严格版命中 {s['rescued'] / (s['strict_hit'] or 1) * 100:.1f}%)")
        if s["anomaly"]:
            print(f"  ⚠ 违反理论关系(应为0) : {s['anomaly']}")
        if lag[cb]:
            ls = sorted(lag[cb])
            n = len(ls)
            print(f"  滞后根数: 最小 {ls[0]}  中位 {ls[n // 2]}  均值 {sum(ls) / n:.1f}  "
                  f"最大 {ls[-1]}  (>5根占 {sum(1 for x in ls if x > 5) / n * 100:.0f}%)")
        yr = rescued_year[cb]
        if yr:
            print("  按年分布: " + "  ".join(f"{y}:{yr[y]}" for y in sorted(yr)))

    print()
    print(f"总耗时 {(datetime.now() - t0).total_seconds():.1f}s")
    print("=" * 88)


if __name__ == "__main__":
    main()
