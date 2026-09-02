"""月线区间破位Pinbar 复盘看板 (只读分析, 不改库/不改生产扫描逻辑)。

对 6 月窗口内全部原始触发信号做"结果归类":
  OPEN       已触发入场, 未触 SL/TP  -> 持仓中 (活口)
  TP_HIT     触及目标位 -> 已了结(盈)
  STOPPED    入场后跌破止损 -> 已了结(损)
  INVALIDATED 信号之后、入场之前就跌破止损 -> 信号失效(未成仓)
  PENDING    既未触发入场也未破位 -> 待触发
并附最新收盘价(日线, 截至数据末尾)与到 SL/TP 的距离, 供交易员盯盘。
"""
import json
import pandas as pd
import numpy as np
import core.data_provider as dp
from core.strategies.monthly_range_break_strategy import MonthlyRangeBreakStrategy

STRAT = MonthlyRangeBreakStrategy()
RECENT_MONTHS = 6


def _first_idx(cond_arr):
    idx = np.where(cond_arr)[0]
    return int(idx[0]) if len(idx) else None


def classify(code):
    df = dp.get_monthly_bars(code, limit=300)
    if df is None or len(df) < 40:
        return []
    df = STRAT.calculate_signals(df)
    sig_col = 'signal_mrb'
    recent = df.tail(RECENT_MONTHS)
    mask = recent.get(sig_col, pd.Series(dtype=bool)) == True
    # 最新日线收盘价 (盯盘用)
    daily = dp.get_stock_data(code, limit=8)
    last_close = float(daily['close'].iloc[-1]) if (daily is not None and not daily.empty) else None

    rows = []
    for idx in recent.index[mask]:
        row = df.loc[idx]
        entry = float(row['entry_mrb']); sl = float(row['sl_mrb']); tp = float(row['tp_mrb'])
        sdate = row['trade_date']
        if idx < len(df) - 1:
            post = df.iloc[idx + 1:]
            trig = _first_idx((post['high'].values >= entry))
            slo = _first_idx((post['low'].values <= sl))
            tpo = _first_idx((post['high'].values >= tp))
        else:
            trig = slo = tpo = None

        if trig is None and slo is None and tpo is None:
            outcome = 'PENDING'
        elif slo is not None and (trig is None or slo < trig):
            outcome = 'TP_HIT' if (tpo is not None and tpo < slo) else 'INVALIDATED'
        elif trig is not None:
            if tpo is not None and (slo is None or tpo < slo):
                outcome = 'TP_HIT'
            elif slo is not None:
                outcome = 'STOPPED'
            else:
                outcome = 'OPEN'
        else:
            outcome = 'TP_HIT'

        d_sl = (last_close - sl) / sl * 100 if last_close else None
        d_tp = (tp - last_close) / last_close * 100 if last_close else None
        rows.append({
            'code': code, 'name': dp.get_stock_name(code), 'signal_date': sdate,
            'entry': round(entry, 2), 'sl': round(sl, 2), 'tp': round(tp, 2),
            'last_close': round(last_close, 2) if last_close else None,
            'dist_sl_pct': round(d_sl, 1) if d_sl is not None else None,
            'dist_tp_pct': round(d_tp, 1) if d_tp is not None else None,
            'outcome': outcome,
        })
    return rows


def main():
    codes = dp.get_stock_list()
    print(f"universe: {len(codes)}")
    all_rows = []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(classify, c): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(codes)}")
            try:
                all_rows.extend(f.result())
            except Exception as e:
                print(f"  err {futs[f]}: {e}")

    order = {'OPEN': 0, 'PENDING': 1, 'TP_HIT': 2, 'STOPPED': 3, 'INVALIDATED': 4}
    all_rows.sort(key=lambda r: (order.get(r['outcome'], 9), r['signal_date'], r['code']))

    # ---- Markdown 看板 ----
    from datetime import datetime
    md = f"# 月线区间破位Pinbar 复盘看板\n\n生成: {datetime.now():%Y-%m-%d %H:%M} · 窗口: 最近 {RECENT_MONTHS} 个月 · 原始触发 {len(all_rows)} 只\n\n"
    md += "## 一、持仓跟踪 (活口 + 待触发)\n\n"
    md += "| 代码 | 名称 | 信号月 | 入场 | 止损 | 目标 | 最新收 | 距止损% | 距目标% | 状态 |\n|:---|:---|:---|---:|---:|---:|---:|---:|---:|:---|\n"
    for r in all_rows:
        if r['outcome'] in ('OPEN', 'PENDING'):
            md += (f"| {r['code']} | {r['name']} | {r['signal_date']} | {r['entry']:.2f} | {r['sl']:.2f} | "
                    f"{r['tp']:.2f} | {r['last_close']:.2f} | {r['dist_sl_pct']:.1f} | {r['dist_tp_pct']:.1f} | {r['outcome']} |\n")

    md += "\n## 二、已了结 / 失效样本 (复盘底稿)\n\n"
    md += "> 这类是触发过、但后来被月线级止损击穿或摸到目标的信号——留作复盘, 不在操盘台推送。\n\n"
    md += "| 代码 | 名称 | 信号月 | 入场 | 止损 | 目标 | 最新收 | 结果 |\n|:---|:---|:---|---:|---:|---:|---:|:---|\n"
    for r in all_rows:
        if r['outcome'] in ('TP_HIT', 'STOPPED', 'INVALIDATED'):
            md += (f"| {r['code']} | {r['name']} | {r['signal_date']} | {r['entry']:.2f} | {r['sl']:.2f} | "
                    f"{r['tp']:.2f} | {r['last_close']:.2f} | {r['outcome']} |\n")

    md += "\n## 三、结果分布\n\n"
    from collections import Counter
    cnt = Counter(r['outcome'] for r in all_rows)
    for k in ('OPEN', 'PENDING', 'TP_HIT', 'STOPPED', 'INVALIDATED'):
        md += f"- {k}: {cnt.get(k, 0)}\n"

    out_md = "strategy_lab/monthly_range_break_review.md"
    out_json = "data/monthly_range_break_review.json"
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False, indent=2)
    print(f"✅ 写出 {out_md} / {out_json}")
    print("分布:", dict(cnt))


if __name__ == "__main__":
    main()
