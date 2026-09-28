# -*- coding: utf-8 -*-
"""
MTR 全市场回测 —— 明细复算 / 稳健性分析 / 退化R案例复现
==============================================================
模式:
  (默认)  从 signals CSV 明细独立重算 n / 胜率 / EV, 并与汇总 json 交叉验证;
          做按年剥离、极端值处理(截尾 / 剔除不可信净R)对比。
  diag <symbol> <tl_date>
          从 data/baostock.db 复现单结构的 R 计算, 用于诊断"净R 爆炸"根因
          (定位: risk = entry_ref - sl_price 退化为极小值时, R 倍数失真)。

环境变量:
  MTR_ARTIFACT_SUFFIX  读取的产物口径后缀。默认 "" = 严格口径
                       (full_market_signals_2R.csv); 传 "_loose" 则读宽松口径
                       (full_market_signals_2R_loose.csv)。

只读; 不写任何文件。
"""
import csv
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.getcwd()
OUT_DIR = os.path.join(PROJECT_ROOT, "archive", "mtr_backtest")
SUFFIX = os.environ.get("MTR_ARTIFACT_SUFFIX", "")   # "" = strict, "_loose" = loose

# 不可信净R 阈值: 2R 目标策略不可能经常亏超过 3R (跳空穿损的极限量级);
# 超过即判定为"风险单位退化"造成的失真样本。
EXTREME_NEG = -3.0

GROUPS = [
    ("OLD", "old_sig_date", "old_status", "old_net_R"),
    ("NEW_B", "new_sig_date", "new_status", "new_net_R"),
    ("S2", "s2_sig_date", "s2_status", "s2_net_R"),
    ("S3", "s3_sig_date", "s3_status", "s3_net_R"),
]


def load(tp):
    path = os.path.join(OUT_DIR, f"full_market_signals_{tp:g}R{SUFFIX}.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def extract(rows, date_key, status_key, r_key):
    out = []
    for r in rows:
        if r.get(status_key) not in ("WIN", "LOSS"):
            continue
        rv = r.get(r_key)
        ds = r.get(date_key) or ""
        if rv in ("", None) or len(ds) < 4:
            continue
        try:
            out.append((int(ds[:4]), float(rv)))
        except ValueError:
            continue
    return out


def stats(pairs):
    n = len(pairs)
    if not n:
        return dict(n=0, win=0.0, ev=0.0, avg_w=0.0, avg_l=0.0,
                    med=0.0, worst=0.0, best=0.0)
    rs = sorted(p[1] for p in pairs)
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    return dict(
        n=n,
        win=round(len(wins) / n * 100, 2),
        ev=round(sum(rs) / n, 4),
        avg_w=round(sum(wins) / len(wins), 3) if wins else 0.0,
        avg_l=round(sum(losses) / len(losses), 3) if losses else 0.0,
        med=round(rs[n // 2], 3),
        worst=round(rs[0], 3),
        best=round(rs[-1], 3),
    )


def trimmed_ev(pairs, frac=0.01):
    rs = sorted(p[1] for p in pairs)
    k = int(len(rs) * frac)
    if k <= 0:
        return round(sum(rs) / len(rs), 4) if rs else 0.0
    core = rs[k:len(rs) - k]
    return round(sum(core) / len(core), 4) if core else 0.0


def drop_extreme(pairs, thr=EXTREME_NEG):
    """剔除 net_R < thr 的不可信样本; 返回 (保留样本, 被剔除数, 被剔除R总和)。"""
    keep = [p for p in pairs if p[1] >= thr]
    bad = [p for p in pairs if p[1] < thr]
    return keep, len(bad), round(sum(p[1] for p in bad), 2)


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "diag":
        return diag(sys.argv[2], sys.argv[3])
    for tp in (2.0, 1.0):
        rows = load(tp)
        if rows is None:
            print(f"[跳过] 产物不存在: full_market_signals_{tp:g}R{SUFFIX}.csv")
            continue
        print("=" * 100)
        print(f"TP = {tp:g}R   明细行数 {len(rows)}   信号K口径 = "
              f"{SUFFIX.lstrip('_') or 'strict'}   (独立重算, 未读 json)")
        print("=" * 100)
        print(f"{'组':<6}{'n':>7}{'胜率%':>8}{'EV原始':>9}{'均赢R':>7}{'均亏R':>8}"
              f"{'中位':>7}{'最差':>10}{'剔退化n':>8}{'剔退化R和':>10}{'EV剔退化':>9}{'截尾1%':>9}")
        per_group = {}
        for name, dk, sk, rk in GROUPS:
            pairs = extract(rows, dk, sk, rk)
            per_group[name] = pairs
            s = stats(pairs)
            keep, nb, badsum = drop_extreme(pairs)
            kev = stats(keep)["ev"]
            print(f"{name:<6}{s['n']:>7}{s['win']:>8}{s['ev']:>9}{s['avg_w']:>7}"
                  f"{s['avg_l']:>8}{s['med']:>7}{s['worst']:>10}{nb:>8}{badsum:>10}"
                  f"{kev:>9}{trimmed_ev(pairs):>9}")

        print("\n-- 按年 EV (净R) / n  [原始口径] --")
        years = sorted({y for p in per_group.values() for y, _ in p})
        print("年份  " + "".join(f"{n:>20}" for n, *_ in GROUPS))
        for y in years:
            line = f"{y}  "
            for name, *_ in GROUPS:
                sub = [p for p in per_group[name] if p[0] == y]
                s = stats(sub)
                line += f"{s['ev']:>12}(n={s['n']:<6})" if sub else f"{'-':>20}"
            print(line)

        print("\n-- 按年: 剔除退化样本后的 EV / n --")
        print("年份  " + "".join(f"{n:>20}" for n, *_ in GROUPS))
        for y in years:
            line = f"{y}  "
            for name, *_ in GROUPS:
                sub = [p for p in per_group[name] if p[0] == y]
                keep, nb, _ = drop_extreme(sub)
                s = stats(keep)
                line += (f"{s['ev']:>12}(n={s['n']:<6})" if s["n"]
                         else f"{'-':>20}")
            print(line)

        print("\n-- 全期: 原始 vs 剔退化 vs 剥离2023(剔退化后) --")
        print(f"{'组':<6}{'n':>7}{'EV原始':>9}{'EV剔退化':>10}"
              f"{'n(24-26)':>10}{'EV(24-26)剔退化':>17}")
        for name, *_ in GROUPS:
            pairs = per_group[name]
            keep, _, _ = drop_extreme(pairs)
            sub = [p for p in keep if p[0] != 2023]
            s2 = stats(sub)
            print(f"{name:<6}{stats(pairs)['n']:>7}{stats(pairs)['ev']:>9}"
                  f"{stats(keep)['ev']:>10}{s2['n']:>10}{s2['ev']:>17}")
        print()


def diag(symbol, tl_date):
    """复现单结构的 SL / risk / 入场成交价, 定位净R 爆炸根因。"""
    import sqlite3
    import pandas as pd
    sys.path.append(PROJECT_ROOT)
    from core.strategies.mtr_structural_v35 import MTRStructuralEngineV35
    from core.backtest_engine import simulate_trade_unified

    db = os.path.join(PROJECT_ROOT, "data", "baostock.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA query_only=1")
    df = pd.read_sql_query(
        "SELECT trade_date AS date, open, high, low, close, volume FROM daily_bars "
        "WHERE symbol=? AND adjust='qfq' ORDER BY trade_date", conn, params=(symbol,))
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    engine = MTRStructuralEngineV35(ema_period=20)
    swings = engine.find_swing_points(df, window=5)
    target = None
    for ci in range(120, len(df)):
        res = engine.match_mtr_pattern(df, swings, ci)
        if not res or res.get("stage") != "SETUP_READY":
            continue
        if str(df["date"].iloc[res["points"]["TL"].index])[:10] == tl_date:
            target = res
    if target is None:
        print(f"[diag] {symbol} 未找到 TL={tl_date} 的结构")
        return
    pts = target["points"]
    l1, h1, tl = pts["L1"], pts["H1"], pts["TL"]
    sl = min(l1.price, tl.price) - 0.01
    sb = engine._find_signal_bar(df, type("T", (), {"index": tl.index})())
    print(f"[diag] {symbol}  TL={tl_date}")
    print(f"  L1 idx={l1.index} price={l1.price:.4f} | TL idx={tl.index} price={tl.price:.4f}")
    print(f"  sl = min(L1,TL) - 0.01 = {sl:.4f}")
    print(f"  信号K idx={sb['idx']} date={str(df['date'].iloc[sb['idx']])[:10]} "
          f"O={df['open'].iloc[sb['idx']]:.4f} H={df['high'].iloc[sb['idx']]:.4f} "
          f"L={df['low'].iloc[sb['idx']]:.4f} C={df['close'].iloc[sb['idx']]:.4f}")
    entry_ref = float(sb["high"])
    risk = entry_ref - sl
    print(f"  entry_ref = 信号K最高价 = {entry_ref:.4f}")
    print(f"  >>> risk = entry_ref - sl = {risk:.4f}  "
          f"占价格 {risk / entry_ref * 100:.3f}%")
    for tp in (2.0, 1.0):
        sim = simulate_trade_unified(df, sb["idx"], entry_ref, sl, risk_mult=tp, max_hold=60)
        print(f"  TP={tp:g}R -> status={sim['status']} entry_fill={sim['entry_fill']:.4f} "
              f"exit_fill={sim['exit_fill']:.4f} net_R={sim['net_R']:.3f} "
              f"reason={sim['reason']}")


if __name__ == "__main__":
    main()
