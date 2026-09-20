# archive/tools/backtest_gap_h2_samples.py
"""
[回测采样] GAP H2 四配置 (A/B/C/D) 各取 1 笔止盈(WIN) + 1 笔止损(LOSS)样本, 绘制 K 线图

选样规则 (典型样本, 非最优/最差):
  - 与该配置「平均盈R」最接近的 WIN / 「平均亏R」最接近的 LOSS;
  - 新出场(B/D) 的 WIN 优先在「2R 半仓已触发」中选 (体现分批止盈设计);
  - 新出场(B/D) 的 LOSS 优先选硬底线止损 (reason=stop_loss / same_day_stop);
  - 要求样本入场后已离场且有 exit_date, 图窗口能覆盖完整交易生命周期。

绘图: 严格复用 tools/notifier.generate_chart_bytes (与此前「止盈样本/止损样本」同方法):
  df_override 传窗口切片, SL 横线=缺口地板, TP1 横线=旧出场测量目标 / 新出场 2R 半仓位。
输出: docs/gap_h2_sample_<配置>_<止盈|止损>.png 共 8 张。
"""
import os
import sys
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed


def _find_project_root(start: str) -> str:
    p = os.path.abspath(start)
    while p and p != os.path.dirname(p):
        if os.path.isdir(os.path.join(p, 'core')) and os.path.isfile(os.path.join(p, 'core', 'paths.py')):
            return p
        p = os.path.dirname(p)
    return p


project_root = _find_project_root(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from core.paths import ensure_importable
ensure_importable()

from backtest_gap_h2 import evaluate_trade
from backtest_gap_h2_new_exit import evaluate_trade_new_exit, CONFIGS, _make_strategy, _worker
from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from tools.notifier import generate_chart_bytes

# 配置 → 绘图/文件标签
CFG_META = {
    'A C0原版+旧出场':  dict(tag='A', short='A_C0旧出场',   strat='STRATEGY_GAP_H2'),
    'B C0原版+新出场':  dict(tag='B', short='B_C0新出场',   strat='STRATEGY_GAP_H2'),
    'C C0+重置+旧出场': dict(tag='C', short='C_重置旧出场', strat='STRATEGY_GAP_H2_ENHANCED'),
    'D C0+重置+新出场': dict(tag='D', short='D_重置新出场', strat='STRATEGY_GAP_H2_ENHANCED'),
}


def scan_trades(stock_limit=0):
    """全市场跑 2x2 回测, 只收集交易记录 (与 backtest_gap_h2_new_exit 同一 worker)。"""
    codes = get_stock_list()
    if not codes:
        print("[!] 无法获取股票列表")
        return []
    if stock_limit > 0:
        codes = codes[:stock_limit]
    from config import settings
    n = len(codes)
    print(f"[*] 扫描 {n} 只标的 ...")
    all_trades = []
    with ProcessPoolExecutor(max_workers=getattr(settings, 'MAX_WORKERS', 4)) as exe:
        futs = {exe.submit(_worker, c, CONFIGS, 0): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [{done}/{n}]", flush=True)
            r = f.result()
            if r:
                all_trades.extend(r)
    print(f"[*] 总交易记录 {len(all_trades)} 条")
    return all_trades


def pick_sample(trades, label, kind):
    """选典型样本: 最接近平均盈R/亏R; 新出场按偏好过滤。"""
    sub = [t for t in trades if t['config'] == label and t['status'] == kind and t.get('exit_date')]
    if not sub:
        return None
    if kind == 'WIN' and '新出场' in label:
        half = [t for t in sub if t.get('half_exit_price') is not None
                and not pd.isna(t.get('half_exit_price', np.nan))]
        if half:
            sub = half
    if kind == 'LOSS' and '新出场' in label:
        hard = [t for t in sub if t.get('reason') in ('stop_loss', 'same_day_stop')]
        if hard:
            sub = hard
    rs = np.array([t['total_r'] for t in sub])
    tgt = rs.mean()
    best = min(sub, key=lambda t: abs(t['total_r'] - tgt))
    return best


def draw_sample(trade, label):
    """用 notifier.generate_chart_bytes 绘制样本 K 线 (窗口覆盖 信号→离场 全程)。"""
    meta = CFG_META[label]
    code = trade['code']
    kind = trade['status']          # WIN / LOSS
    kind_cn = '止盈' if kind == 'WIN' else '止损'

    df = get_stock_data(code, limit=None)
    if df is None or len(df) < 100:
        print(f"[!] {code} 数据不足, 跳过")
        return None
    if 'date' not in df.columns and 'trade_date' in df.columns:
        df['date'] = df['trade_date']
    df = add_indicators(df)
    strat = _make_strategy(next(c for c in CONFIGS if c['label'] == label))
    sdf = strat.calculate_signals(df.copy())

    dates = sdf['date'].astype(str).str[:10].values
    sig_pos = int(np.where(dates == trade['signal_date'])[0][0])
    exit_matches = np.where(dates == str(trade['exit_date'])[:10])[0]
    exit_pos = int(exit_matches[-1]) if len(exit_matches) else sig_pos + 40

    start = max(0, sig_pos - 90)
    end = min(len(sdf), exit_pos + 8)
    if end - start > 160:
        start = end - 160
    window = sdf.iloc[start:end].copy()

    sig_row = sdf.iloc[sig_pos]
    sl0 = float(sig_row['sl_gap_h2'])                     # 缺口地板 (初始硬底线)
    entry_sig = float(sig_row['entry_gap_h2'])            # 信号K高点 (Buy Stop 挂单价)
    if '新出场' in label:
        # 新出场: TP1 横线画 2R 半仓止盈位 (R 以地板止损计)
        r0 = entry_sig - sl0
        tp1 = entry_sig + 2.0 * r0
    else:
        # 旧出场: TP1 横线画测量目标 (2×地板−前摆低)
        tp1 = float(sig_row['tp_gap_h2'])

    stock_name = f"{meta['tag']}·{kind_cn}样本"
    buf = generate_chart_bytes(
        code, stock_name, meta['strat'], sl_price=sl0, tp1=tp1,
        entry=entry_sig, df_override=window, timeframe='日K', draw_panel=True,
        sig_quality=float(sig_row.get('sig_bar_quality_h2', 0) or 0),
    )
    if buf is None:
        print(f"[!] {code} 绘图失败")
        return None
    out = os.path.join(project_root, 'docs', f"gap_h2_sample_{meta['short']}_{kind_cn}.png")
    with open(out, 'wb') as f:
        f.write(buf.getvalue())
    return out


def describe(trade, label):
    r = trade.get('total_r', float('nan'))
    half = trade.get('half_exit_price')
    half_s = f"{half:.2f}" if (half is not None and not pd.isna(half)) else "-"
    return (f"[{label}] {trade['status']}  {trade['code']}  信号{trade.get('signal_date')} "
            f"入{trade.get('entry_date')} 出{trade.get('exit_date')}  "
            f"入价{trade.get('entry_price', float('nan')):.2f} 出价{trade.get('exit_price', float('nan')):.2f} "
            f"半仓位{half_s}  total_r={r:+.3f}  reason={trade.get('reason')}")


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--stocks', type=int, default=0, help='仅取前 N 只标的 (0=全部)')
    args = p.parse_args()

    trades = scan_trades(args.stocks)
    picks = {}
    for c in CONFIGS:
        for kind in ('WIN', 'LOSS'):
            t = pick_sample(trades, c['label'], kind)
            if t is None:
                print(f"[!] {c['label']} 无 {kind} 样本")
                continue
            picks[(c['label'], kind)] = t
            print(describe(t, c['label']))

    print("\n[*] 开始绘图 ...")
    outs = []
    for (label, kind), t in picks.items():
        path = draw_sample(t, label)
        if path:
            outs.append(path)
            print(f"  [OK] {path}")
    print(f"\n[*] 完成, 共 {len(outs)} 张图")
