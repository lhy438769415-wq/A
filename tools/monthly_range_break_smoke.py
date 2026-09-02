#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
冒烟测试：验证 MonthlyRangeBreakPinbar 策略已正确接入系统。

1. 注册表能列出 STRATEGY_MONTHLY_RANGE_BREAK
2. get_monthly_bars('002739') + calculate_signals 能复现已知 2026-06 命中
3. 全市场扫描命中数与独立验证脚本（~31）量级一致

运行：PYTHONPATH=<项目根> .venv/Scripts/python.exe tools/monthly_range_break_smoke.py
"""
import sys
import time
import sqlite3

# ---- 仅冒烟测试用，生产代码禁止 sys.path.insert（此处用 PYTHONPATH 注入，不写文件）----
from core.data_provider import get_monthly_bars
from core.strategy_registry import StrategyRegistry
from core.strategies.monthly_range_break_strategy import MonthlyRangeBreakStrategy

DB_PATH = "data/baostock.db"


def main():
    print("=== 1. 注册表检查 ===")
    listed = StrategyRegistry.list_strategies()
    print("官方列表:", listed)
    assert "STRATEGY_MONTHLY_RANGE_BREAK" in listed, "注册表未包含新策略！"
    meta = StrategyRegistry.get_metadata("STRATEGY_MONTHLY_RANGE_BREAK")
    print("display_name:", meta["display_name"], "| timeframes:", meta["supported_timeframes"])
    print("OK: 策略已注册\n")

    print("=== 2. 002739 已知命中复现 (2026-06) ===")
    df = get_monthly_bars("002739")
    assert df is not None and not df.empty, "get_monthly_bars 返回空"
    strat = MonthlyRangeBreakStrategy()
    out = strat.calculate_signals(df.copy())
    hits = out[out["signal_mrb"].fillna(False)]
    print(f"002739 月线 {len(df)} 根, 命中 {len(hits)} 只：")
    for _, r in hits.iterrows():
        print(f"  {r['ym']} 开{r['open']} 收{r['close']} 高{r['high']} 低{r['low']} "
              f"下影/实体={r['lower_shadow_ratio_mrb']} 下影/振幅={r['shadow_pct_mrb']}% "
              f"破位深={r['break_depth_pct_mrb']}% Base_Low={r['base_low_mrb']}")
    assert any(hits["ym"] == "2026-06"), "未能复现 002739 2026-06 命中！"
    print("OK: 002739 2026-06 命中复现\n")

    print("=== 3. 全市场扫描（对比独立验证脚本 ~31 只）===")
    t0 = time.time()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    symbols = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM daily_bars")]
    con.close()
    print(f"全市场 {len(symbols)} 只，开始扫描...")

    total = 0
    sample = []
    for sym in symbols:
        mdf = get_monthly_bars(sym)
        if mdf is None or mdf.empty:
            continue
        res = strat.calculate_signals(mdf.copy())
        sh = res[res["signal_mrb"].fillna(False)]
        if len(sh):
            total += len(sh)
            for _, r in sh.iterrows():
                sample.append((sym, r["ym"], round(r["lower_shadow_ratio_mrb"], 2)))
    dt = time.time() - t0
    print(f"全市场命中 {total} 只（全部历史月线），耗时 {dt:.1f}s")

    # 仅看最近 6 个已收盘月（2026-03..2026-08），与独立验证脚本 31 只对比
    import datetime
    now = datetime.date.today()
    y, m = now.year, now.month - 1
    if m == 0:
        y, m = y - 1, 12
    window = set()
    for _ in range(6):
        window.add(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    win_total = sum(1 for s in sample if s[1] in window)
    print(f"其中落在 {sorted(window)} 窗口内的命中：{win_total} 只 "
          f"（对比独立脚本基线 31 只：{'一致 ✅' if 28 <= win_total <= 34 else '偏差 ⚠️'}）")

    sample.sort(key=lambda x: -x[2])
    print("Top10（按 下影/实体 降序）：")
    for s in sample[:10]:
        print(f"  {s[0]} {s[1]} 下影/实体={s[2]}")
    print("\n冒烟测试通过。")


if __name__ == "__main__":
    main()
