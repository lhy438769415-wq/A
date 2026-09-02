#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
月线区间底部破位 Pinbar 扫描 —— 生产数据路径验证版

数据源：本地 data/baostock.db 的 daily_bars（qfq 前复权）
做法：  内存聚合 日线 -> 月线（不新增任何数据库表，零 schema 变更）
严格复刻用户对图片给出的 5 条量化条件，与 westock 版 scan.js 逻辑完全一致。

本脚本是「接生产前的验证器」：
1. 用生产数据源（daily_bars）跑一遍扫描；
2. 与 westock 版 scan.js 的 28 只命中做交叉比对，确认生产路径能复现。
不修改任何现有代码 / 数据库结构。
"""
import sqlite3
import os
import json
import datetime
from collections import defaultdict

# ---------------- 参数（与 scan.js 默认一致）----------------
BASE_LOOKBACK = 20          # Base_Low = LLV(LOW, N)
SHADOW_BODY_MIN = 2         # 下影 > 实体 × N
SHADOW_UPPER_MIN = 2        # 下影 > 上影 × N
SHADOW_RANGE_PCT_MIN = 60   # 下影 / 振幅 >= N%
SCAN_RECENT_MONTHS = 6      # 回看最近 N 个已收盘月

# ---------------- 路径 ----------------
HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "baostock.db")
WESTOCK_JSON = os.path.join(PROJECT_ROOT, "pinbar-month-scan-results-range-break.json")
OUT_JSON = os.path.join(PROJECT_ROOT, "monthly_range_break_production_scan.json")
OUT_MD = os.path.join(PROJECT_ROOT, "monthly_range_break_production_scan.md")


def build_target_months(now, n):
    """以『上月』为终点，回看 n 个自然月。例 2026-09 -> 2026-03..2026-08。"""
    target = set()
    y, m = now.year, now.month - 1  # 上月
    if m == 0:
        y, m = y - 1, 12
    for _ in range(n):
        target.add(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return target


def screen_range_break(monthly, idx):
    """monthly: 最新在前的月线列表，每项为 [ym, open, close, high, low]。返回命中详情或 None。"""
    t = monthly[idx]
    history = monthly[idx + 1: idx + 1 + BASE_LOOKBACK]
    if len(history) < BASE_LOOKBACK:
        return None
    base_low = min(c[4] for c in history if c[4] > 0)
    if not (t[4] < base_low):                       # 条件2 盘中跌破
        return None
    if not (t[2] > base_low):                       # 条件3 收盘收回
        return None
    if not (t[2] > t[1]):                           # 条件4 阳线
        return None
    lower = min(t[1], t[2]) - t[4]                  # 下影线
    upper = t[3] - max(t[1], t[2])                  # 上影线
    body = t[2] - t[1]                             # 实体（阳线，>0）
    total = t[3] - t[4]                            # 全根振幅
    if not (lower > 0) or not (total > 0):
        return None
    if not (lower > SHADOW_BODY_MIN * body):        # 下影 > 实体 × N
        return None
    if not (lower > SHADOW_UPPER_MIN * upper):      # 下影 > 上影 × N
        return None
    if not (lower / total >= SHADOW_RANGE_PCT_MIN / 100):  # 下影占振幅 >= N%
        return None
    return {
        "lowerShadowRatio": round(lower / body, 2),
        "baseLow": round(base_low, 3),
        "breakDepthPct": round((base_low - t[4]) / base_low * 100, 2),
        "shadowPct": round(lower / total * 100, 1),
        "upperShadowRatio": round(upper / body, 2),
        "bullish": True,
    }


def process_symbol(sym, monthly, target_months, hits):
    """聚合完一支股票的所有月线后，逐月筛查目标窗。"""
    monthly.sort(key=lambda m: m[0], reverse=True)  # 最新在前
    for idx, t in enumerate(monthly):
        if t[0] not in target_months:
            continue
        res = screen_range_break(monthly, idx)
        if not res:
            continue
        hits.append({
            "symbol": sym,
            "month": t[0],
            "open": t[1], "close": t[2], "high": t[3], "low": t[4],
            **res,
        })


def main():
    now = datetime.date.today()
    target_months = build_target_months(now, SCAN_RECENT_MONTHS)
    print(f"[info] 今天={now}  目标月窗口={sorted(target_months)}")

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = con.cursor()
    # 按 symbol, 日期排序流式读取，内存中逐股聚合为月线
    cur.execute(
        "SELECT symbol, trade_date, open, close, high, low "
        "FROM daily_bars WHERE adjust='qfq' ORDER BY symbol, trade_date"
    )

    hits = []
    cur_sym = None
    monthly = []  # [ym, open, close, high, low]

    def flush(sym, mlist):
        if sym is not None and mlist:
            process_symbol(sym, mlist, target_months, hits)

    for sym, date, o, c, h, l in cur:
        if sym != cur_sym:
            flush(cur_sym, monthly)
            cur_sym = sym
            monthly = []
        ym = date[:7]
        if not monthly or monthly[-1][0] != ym:
            monthly.append([ym, o, c, h, l])   # 月内第一根：open=首日均价开盘
        else:
            m = monthly[-1]
            m[2] = c                            # close = 末日收盘
            m[3] = max(m[3], h)
            m[4] = min(m[4], l)
    flush(cur_sym, monthly)
    con.close()

    hits.sort(key=lambda x: x["lowerShadowRatio"], reverse=True)
    print(f"[done] 生产路径命中 {len(hits)} 只")

    # ---------------- 与 westock 版交叉比对 ----------------
    if os.path.exists(WESTOCK_JSON):
        with open(WESTOCK_JSON, "r", encoding="utf-8") as f:
            wj = json.load(f)
        def to6(s):
            return s[2:] if s[:2] in ("sh", "sz") else s
        wset = {(to6(r["symbol"]), r["month"][:7]) for r in wj}
        pset = {(h["symbol"], h["month"]) for h in hits}
        matched = wset & pset
        missing = wset - pset       # westock 有、生产路径没有
        extra = pset - wset         # 生产路径有、westock 没有
        print(f"[cross] westock命中 {len(wset)} | 生产路径命中 {len(pset)} | "
              f"交集 {len(matched)} | 漏判 {len(missing)} | 多判 {len(extra)}")
        if missing:
            print("  漏判:", sorted(missing))
        if extra:
            print("  多判:", sorted(extra))

    # ---------------- 输出 ----------------
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(hits, f, ensure_ascii=False, indent=2)

    lines = [
        "# 月线区间底部破位 Pinbar 扫描（生产数据路径 / daily_bars 聚合）",
        "",
        f"> 数据源：本地 data/baostock.db → daily_bars(qfq) 内存聚合为月线",
        f"> 条件：Base_Low=LLV(LOW,{BASE_LOOKBACK}) | 跌破 | 收回 | 阳线 | "
        f"下影>实体×{SHADOW_BODY_MIN} | 下影>上影×{SHADOW_UPPER_MIN} | 下影/振幅≥{SHADOW_RANGE_PCT_MIN}%",
        f"> 扫描最近 {SCAN_RECENT_MONTHS} 个月 | 命中 {len(hits)} 只",
        "",
        "| 代码 | 月份 | 开 | 收 | 高 | 低 | 下影/实体 | Base_Low | 破位深% | 下影/振幅% | 上影/实体 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for h in hits:
        lines.append(
            f"| {h['symbol']} | {h['month']} | {h['open']} | {h['close']} | {h['high']} | "
            f"{h['low']} | {h['lowerShadowRatio']} | {h['baseLow']} | {h['breakDepthPct']} | "
            f"{h['shadowPct']} | {h['upperShadowRatio']} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[out] {OUT_JSON}")
    print(f"[out] {OUT_MD}")


if __name__ == "__main__":
    main()
