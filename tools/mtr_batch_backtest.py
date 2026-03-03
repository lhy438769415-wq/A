# -*- coding: utf-8 -*-
"""
MTR V35.3 批量回测统计 — 含去重 + 信号K质量分析
全量扫描A股完整历史，按 (code, H0_idx, L1_idx) 去重。
"""
import sys, os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from core.data_provider import get_stock_list, get_stock_data
from core.calculator import add_indicators
from core.strategies.mtr_strategy import MTRStrategy


def backtest_stock(code, strategy):
    """对单只股票的全部历史进行 MTR 回测，返回去重后的信号列表"""
    df = get_stock_data(code, limit=None)
    if df is None or len(df) < 200:
        return []

    # [V35.3 Fix] 每只股票重置引擎状态，防止跨股票的 locked_until/exhausted 污染
    strategy.structural_engine.locked_until = -1
    strategy.structural_engine.exhausted_h1_indices = set()

    df = add_indicators(df)
    df = strategy.calculate_signals(df)

    hits = df[df["mtr_stage"] == "SETUP_READY"]
    if len(hits) == 0:
        return []

    results = []
    seen_structures = set()  # (H0_idx, L1_idx) 去重键

    for idx in hits.index:
        row = df.loc[idx]

        h0_idx = row.get("mtr_H0_idx", np.nan)
        l1_idx = row.get("mtr_L1_idx", np.nan)
        if pd.isna(h0_idx) or pd.isna(l1_idx):
            continue

        # 去重: 同一 H0-L1 结构只保留第一次
        dedup_key = (int(h0_idx), int(l1_idx))
        if dedup_key in seen_structures:
            continue
        seen_structures.add(dedup_key)

        score = row["mtr_score"]
        l1_price = row.get("mtr_L1_price", np.nan)
        tl_price = row.get("mtr_TL_price", np.nan)
        entry_price = row.get("mtr_entry_price", np.nan)
        signal_bar_idx = row.get("mtr_signal_bar_idx", np.nan)
        sb_quality = row.get("mtr_signal_bar_quality", np.nan)

        if any(pd.isna(x) for x in [l1_price, tl_price, entry_price, signal_bar_idx]):
            continue

        sb_i = int(signal_bar_idx)
        sl = min(l1_price, tl_price)
        risk = entry_price - sl
        if risk <= 0:
            continue

        # 看信号K之后30根K线
        future_start = sb_i + 1
        future_end = min(len(df), sb_i + 31)
        if future_end <= future_start:
            continue

        future_df = df.iloc[future_start:future_end]
        future_low = future_df["low"].min()
        future_high = future_df["high"].max()

        hit_sl = future_low < sl
        hit_1r = future_high >= entry_price + risk
        hit_2r = future_high >= entry_price + 2 * risk
        hit_3r = future_high >= entry_price + 3 * risk
        max_rr = (future_high - entry_price) / risk

        results.append({
            "code": code,
            "date": str(row.get("date", "")),
            "score": score,
            "sb_quality": sb_quality if not pd.isna(sb_quality) else 0,
            "entry": entry_price,
            "sl": sl,
            "risk": risk,
            "hit_sl": hit_sl,
            "hit_1r": hit_1r,
            "hit_2r": hit_2r,
            "hit_3r": hit_3r,
            "max_rr": max_rr,
        })

    return results


def print_band(label, band):
    n = len(band)
    if n == 0:
        return
    w1 = band["hit_1r"].mean() * 100
    w2 = band["hit_2r"].mean() * 100
    sl_pct = band["hit_sl"].mean() * 100
    avg_r = band["max_rr"].mean()
    print("  %-12s | %6d | %7.1f%% | %7.1f%% | %7.1f%% | %6.2f" % (label, n, w1, w2, sl_pct, avg_r))


def main():
    print("=" * 60)
    print("MTR V35.3 Batch Backtest (with dedup)")
    print("=" * 60)

    stocks = get_stock_list()
    total = len(stocks)
    print("Total stocks: %d" % total)

    strategy = MTRStrategy()
    all_results = []

    for i, code in enumerate(stocks):
        try:
            results = backtest_stock(code, strategy)
            all_results.extend(results)
            if (i + 1) % 500 == 0:
                print("  [%d/%d] signals so far: %d" % (i + 1, total, len(all_results)))
        except Exception:
            continue

    print("\n" + "=" * 60)
    print("RESULTS (deduplicated)")
    print("=" * 60)
    print("Stocks: %d | Unique signals: %d" % (total, len(all_results)))

    if not all_results:
        print("No signals."); return

    rdf = pd.DataFrame(all_results)
    n = len(rdf)
    print("\n--- Overall ---")
    print("  1R Win: %d/%d = %.1f%%" % (rdf["hit_1r"].sum(), n, rdf["hit_1r"].mean()*100))
    print("  2R Win: %d/%d = %.1f%%" % (rdf["hit_2r"].sum(), n, rdf["hit_2r"].mean()*100))
    print("  SL Hit: %d/%d = %.1f%%" % (rdf["hit_sl"].sum(), n, rdf["hit_sl"].mean()*100))
    print("  Avg R:  %.2f | Median R: %.2f" % (rdf["max_rr"].mean(), rdf["max_rr"].median()))

    # 按分数段
    print("\n--- By Score ---")
    print("  %-12s | %6s | %8s | %8s | %8s | %6s" % ("Band", "Count", "1R Win", "2R Win", "SL Hit", "AvgR"))
    print("  " + "-" * 65)
    for lo, hi in [(50,60),(60,70),(70,80),(80,90),(90,101)]:
        print_band("%d-%d" % (lo,hi), rdf[(rdf["score"]>=lo)&(rdf["score"]<hi)])

    # 按信号K质量
    print("\n--- By Signal Bar Quality ---")
    print("  %-12s | %6s | %8s | %8s | %8s | %6s" % ("Quality", "Count", "1R Win", "2R Win", "SL Hit", "AvgR"))
    print("  " + "-" * 65)
    for lo, hi in [(0,3),(3,5),(5,7),(7,11)]:
        print_band("Q%.0f-%.0f" % (lo,hi), rdf[(rdf["sb_quality"]>=lo)&(rdf["sb_quality"]<hi)])

    out = os.path.join(ROOT, "mtr_backtest_results.csv")
    rdf.to_csv(out, index=False, encoding="utf-8-sig")
    print("\nSaved: %s" % out)


if __name__ == "__main__":
    main()
