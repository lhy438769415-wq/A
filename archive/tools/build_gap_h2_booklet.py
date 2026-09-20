# archive/tools/build_gap_h2_booklet.py
"""
[成册] 单只标的 在 B(C0原版+新出场) / D(C0+重置+新出场) 配置下的 GAP H2 全部入场,
绘制成多页 PDF:
  - 分组: 配置 B → 止盈 / 止损 / 未结; 配置 D → 止盈 / 止损 / 未结
  - 组内按信号日期 (时间先后) 排序
  - 每笔交易 1 页 K 线图 + 文字摘要 (沿用 generate_chart_bytes 标注风格)

用法:
  python build_gap_h2_booklet.py --code sh.603296
  python build_gap_h2_booklet.py --auto        # 取 archive/top_bd_signal_stocks.csv 第一名
"""
import os
import sys
import argparse
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import io

warnings.filterwarnings('ignore')


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

from backtest_gap_h2_new_exit import evaluate_trade_new_exit, CONFIGS, _make_strategy
from core.data_provider import get_stock_data
from core.calculator import add_indicators
from tools.notifier import generate_chart_bytes
from config import settings

C = {c['label']: c for c in CONFIGS}
CFG_B = C['B C0原版+新出场']
CFG_D = C['D C0+重置+新出场']


def collect_trades(code):
    """返回 {config_label: [trade, ...]} 仅含已触发(entry 有值) 的交易。"""
    df = get_stock_data(code, limit=None)
    if df is None or len(df) < 100:
        return None
    if 'date' not in df.columns and 'trade_date' in df.columns:
        df['date'] = df['trade_date']
    df = add_indicators(df)

    out = {}
    for cfg in (CFG_B, CFG_D):
        strat = _make_strategy(cfg)
        sdf = strat.calculate_signals(df.copy())
        sig_col = 'signal_gap_h2'
        indices = [i for i, v in enumerate(sdf[sig_col].fillna(False).values) if v]
        trades = []
        for idx in indices:
            t = evaluate_trade_new_exit(sdf, idx)
            t['code'] = code
            t['config'] = cfg['label']
            sd = sdf.iloc[idx]['date'] if 'date' in sdf.columns else str(sdf.index[idx])
            t['signal_date'] = str(sd)[:10]
            # 仅保留真正触发入场的交易 (WIN/LOSS/HOLDING), 跳过 INVALIDATED/VOIDED/TIMEOUT/PENDING/ERROR
            if t.get('status') in ('WIN', 'LOSS', 'HOLDING') and t.get('entry_date'):
                trades.append(t)
        # 按信号日期先后排序
        trades.sort(key=lambda x: x.get('signal_date', ''))
        out[cfg['label']] = trades
    return out


def draw_trade(code, trade, label):
    """生成单笔交易 K 线 PNG bytes (窗口覆盖 信号→离场 / 未结则到末尾)。"""
    df = get_stock_data(code, limit=None)
    if df is None or len(df) < 100:
        return None
    if 'date' not in df.columns and 'trade_date' in df.columns:
        df['date'] = df['trade_date']
    df = add_indicators(df)
    strat = _make_strategy(next(c for c in CONFIGS if c['label'] == label))
    sdf = strat.calculate_signals(df.copy())

    dates = sdf['date'].astype(str).str[:10].values
    sig_pos = int(np.where(dates == trade['signal_date'])[0][0])
    exd = trade.get('exit_date')
    if exd and str(exd)[:10] in set(dates):
        exit_pos = int(np.where(dates == str(exd)[:10])[0][-1])
    else:
        exit_pos = len(sdf) - 1

    start = max(0, sig_pos - 90)
    end = min(len(sdf), exit_pos + 8)
    if end - start > 160:
        start = end - 160
    window = sdf.iloc[start:end].copy()

    sig_row = sdf.iloc[sig_pos]
    sl0 = float(sig_row['sl_gap_h2'])
    entry_sig = float(sig_row['entry_gap_h2'])
    r0 = entry_sig - sl0
    tp1 = entry_sig + 2.0 * r0  # 新出场: 2R 半仓位

    # 显式锚点: 把标注钉在这笔交易自己的 K 线上,
    # 防止窗口向后延伸出现新缺口+H2 时标注整体跳到后一笔形态 (用户实测踩坑)。
    extra = {'anchor_signal_date': pd.Timestamp(trade['signal_date'])}
    en_date = trade.get('entry_date')
    if en_date and str(en_date)[:10] in set(dates):
        extra['entry_mark'] = (pd.Timestamp(str(en_date)[:10]), float(trade['entry_price']))
    exd = trade.get('exit_date')
    if exd and str(exd)[:10] in set(dates):
        _reason = str(trade.get('reason', ''))
        _lab = 'Exit·SL' if 'stop' in _reason else ('Exit·L2' if 'reversal' in _reason else 'Exit')
        extra['exit_mark'] = (pd.Timestamp(str(exd)[:10]), float(trade['exit_price']), _lab)

    buf = generate_chart_bytes(
        code, f"{label}", 'STRATEGY_GAP_H2', sl_price=sl0, tp1=tp1,
        entry=entry_sig, df_override=window, timeframe='日K', draw_panel=True,
        sig_quality=float(sig_row.get('sig_bar_quality_h2', 0) or 0),
        extra_annotate=extra,
    )
    if buf is None:
        return None
    return buf.getvalue()


def _img_to_arr(png_bytes):
    return plt.imread(io.BytesIO(png_bytes))


def add_divider(pdf, title, subtitle=''):
    fig = plt.figure(figsize=(11.69, 8.27))  # A4 竖
    fig.patch.set_facecolor('#0d1117')
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis('off')
    ax.text(0.5, 0.62, title, ha='center', va='center', fontsize=26,
            color='#e6edf3', fontweight='bold')
    if subtitle:
        ax.text(0.5, 0.52, subtitle, ha='center', va='center', fontsize=13,
                color='#8b949e')
    pdf.savefig(fig); plt.close(fig)


def add_trade_page(pdf, code, trade, label, seq):
    png = draw_trade(code, trade, label)
    if png is None:
        return False
    img = _img_to_arr(png)
    status = trade['status']
    kind = '止盈' if status == 'WIN' else ('止损' if status == 'LOSS' else '未结(持仓中)')
    r = trade.get('total_r', float('nan'))
    half = trade.get('half_exit_price')
    half_s = f"{half:.2f}" if (half is not None and not (isinstance(half, float) and np.isnan(half))) else '—'

    fig = plt.figure(figsize=(11.69, 8.27))
    fig.patch.set_facecolor('#ffffff')
    # 图占上方 ~82%
    ax = fig.add_axes([0.04, 0.20, 0.92, 0.74])
    ax.imshow(img); ax.axis('off')
    # 文字摘要条
    cap = (f"#{seq}  [{label}]  {kind}   标的 {code}\n"
           f"信号 {trade.get('signal_date','-')}   入场 {trade.get('entry_date','-')}   "
           f"离场 {trade.get('exit_date','-') if trade.get('exit_date') else '—(未结)'}\n"
           f"入价 {trade.get('entry_price', float('nan')):.2f}   出价 "
           f"{trade.get('exit_price', float('nan')):.2f}   半仓位 {half_s}   "
           f"total_R {r:+.3f}   离场原因 {trade.get('reason','-')}")
    fig.text(0.05, 0.10, cap, fontsize=10.5, color='#1f2328', va='top')
    pdf.savefig(fig); plt.close(fig)
    return True


def build(code, out_path):
    trades_by_cfg = collect_trades(code)
    if trades_by_cfg is None:
        print(f"[!] {code} 无数据")
        return None

    # 分组
    groups = []  # (config_label, kind_cn, [trades])
    for cfg in (CFG_B, CFG_D):
        ts = trades_by_cfg[cfg['label']]
        win = [t for t in ts if t['status'] == 'WIN']
        loss = [t for t in ts if t['status'] == 'LOSS']
        hold = [t for t in ts if t['status'] == 'HOLDING']
        groups.append((cfg['label'], '止盈', win))
        groups.append((cfg['label'], '止损', loss))
        if hold:
            groups.append((cfg['label'], '未结', hold))

    total = sum(len(g[2]) for g in groups)
    if total == 0:
        print(f"[!] {code} 在 B/D 下无已触发入场")
        return None

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with PdfPages(out_path) as pdf:
        # 封面
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.patch.set_facecolor('#0d1117')
        ax = fig.add_axes([0, 0, 1, 1]); ax.axis('off')
        ax.text(0.5, 0.74, 'GAP H2 单股入场成册', ha='center', va='center',
                fontsize=30, color='#e6edf3', fontweight='bold')
        ax.text(0.5, 0.65, f'标的 {code}  ·  全历史日线', ha='center', va='center',
                fontsize=16, color='#58a6ff')
        lines = [f'配置 B (C0原版+新出场): 共 {len(trades_by_cfg[CFG_B["label"]])} 笔入场',
                 f'配置 D (C0+重置+新出场): 共 {len(trades_by_cfg[CFG_D["label"]])} 笔入场',
                 f'成册总页(交易): {total}',
                 '出场: 阶段A 2R半仓止盈 → 阶段B 过突破高点移止损至信号K低点 → 阶段C 反转出场',
                 '分组: 配置 → 止盈/止损/未结; 组内按信号日期先后']
        ax.text(0.5, 0.40, '\n'.join(lines), ha='center', va='center',
                fontsize=12, color='#8b949e', linespacing=1.8)
        pdf.savefig(fig); plt.close(fig)

        for cfg_label, kind, ts in groups:
            if not ts:
                continue
            add_divider(pdf, f'{cfg_label} · {kind}',
                        f'{kind}共 {len(ts)} 笔 · 按信号日期先后')
            for i, t in enumerate(ts, 1):
                add_trade_page(pdf, code, t, cfg_label, i)

    print(f"[*] 成册完成: {out_path}  (交易页 {total})")
    return out_path


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', type=str, default='')
    ap.add_argument('--auto', action='store_true', help='取 top_bd_signal_stocks.csv 第一名')
    args = ap.parse_args()

    code = args.code
    if args.auto:
        csv = os.path.join(project_root, 'archive', 'top_bd_signal_stocks.csv')
        if os.path.exists(csv):
            d = pd.read_csv(csv)
            code = str(d.iloc[0]['code'])
            print(f"[*] auto 选取: {code}")
    if not code:
        print("[!] 需提供 --code 或 --auto")
        sys.exit(1)

    out = os.path.join(project_root, 'docs', f'gap_h2_booklet_{code}.pdf')
    build(code, out)
