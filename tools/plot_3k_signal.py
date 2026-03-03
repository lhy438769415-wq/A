# tools/plot_3k_signal.py
"""
3K策略信号K线图绘制 — 推送样式
标注: 3K形态(K1/K2/K3)、前期波段高低点、缺口测试确认信号、Entry/SL/TP
"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import matplotlib.font_manager as fm

from core.data_provider import get_stock_data
from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
from config import settings

# 中文字体
for f in ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']:
    if any(f.lower() in fp.name.lower() for fp in fm.fontManager.ttflist):
        plt.rcParams['font.sans-serif'] = [f]
        break
plt.rcParams['axes.unicode_minus'] = False


def plot_3k_signal(symbol: str, signal_date: str, output_dir: str = None):
    """绘制3K信号K线图"""
    print(f"绘制 {symbol} @ {signal_date} ...")

    code = f"sz.{symbol}" if symbol.startswith(('0', '3')) else f"sh.{symbol}"
    df = get_stock_data(code)
    if df is None or df.empty:
        print(f"  未找到 {code} 数据")
        return None

    df = add_indicators(df)
    strategy = ThreeKStrategy()
    df = strategy.calculate_signals(df)
    df['date_dt'] = pd.to_datetime(df['date'])

    # 找到目标信号
    target_dt = pd.to_datetime(signal_date)
    sig_rows = df[df['signal_3k'] == True]
    if sig_rows.empty:
        print(f"  未找到3K信号")
        return None

    # 找最近的信号
    row_idx = (sig_rows['date_dt'] - target_dt).abs().argsort().iloc[0]
    sig_row = sig_rows.iloc[row_idx]
    sig_pos = df.index.get_loc(sig_row.name)
    k1_pos = sig_pos - 2

    # 前期波段数据
    LOOKBACK = getattr(settings, 'K3_SWING_LOOKBACK', 40)
    psh = df['high'].iloc[max(0, k1_pos - LOOKBACK):k1_pos].max()
    psl = df['low'].iloc[max(0, k1_pos - LOOKBACK):k1_pos].min()
    k1_high = df.iloc[k1_pos]['high']
    k2_high = df.iloc[k1_pos + 1]['high']
    k3_low = sig_row['low']
    # [V2.2] gap_floor = max(K1_High, K2_High, PSH)
    gap_floor = max(k1_high, k2_high, psh)
    gap_mid = (gap_floor + k3_low) / 2

    # 找缺口测试确认信号
    gt_rows = df[(df.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True) &
                 (df['date_dt'] > sig_row['date_dt']) &
                 (df['date_dt'] <= sig_row['date_dt'] + pd.Timedelta(days=60))]
    has_gt = len(gt_rows) > 0

    # 绘图范围
    plot_start = max(0, k1_pos - 25)
    plot_end = min(len(df) - 1, sig_pos + 25)
    s = df.iloc[plot_start:plot_end + 1].copy()
    s = s.reset_index(drop=True)

    # === 绘图 ===
    fig, ax = plt.subplots(figsize=(16, 9))
    plt.style.use('dark_background')
    fig.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    # K线
    for i, r in s.iterrows():
        color = '#26a69a' if r['close'] >= r['open'] else '#ef5350'
        # 影线
        ax.plot([r['date_dt'], r['date_dt']], [r['low'], r['high']],
                color=color, linewidth=1, zorder=2)
        # 实体
        body_bottom = min(r['open'], r['close'])
        body_height = abs(r['close'] - r['open'])
        rect = Rectangle((mdates.date2num(r['date_dt']) - 0.35, body_bottom),
                          0.7, body_height, facecolor=color, edgecolor=color,
                          zorder=3, linewidth=0.5)
        ax.add_patch(rect)

    # EMA20
    if 'ema20' in s.columns:
        ax.plot(s['date_dt'], s['ema20'], color='#f5a623', alpha=0.6,
                linewidth=1.5, linestyle='--', label='EMA20')

    # === [V2.2] 标注绘图范围内所有 3K 信号 ===
    all_3k_in_range = df[(df['signal_3k'] == True) &
                         (df.index >= plot_start) &
                         (df.index <= plot_end)]
    for _, sig_r in all_3k_in_range.iterrows():
        sp = df.index.get_loc(sig_r.name)
        k1p = sp - 2
        if k1p < 0:
            continue
        for label, pos_i, yoffset in [('K1', k1p, -1.5), ('K2', k1p + 1, -1.5), ('K3', sp, 1)]:
            bar = df.iloc[pos_i]
            y = bar['low'] if yoffset < 0 else bar['high']
            ax.annotate(label, xy=(bar['date_dt'], y),
                        xytext=(0, yoffset * 12), textcoords='offset points',
                        ha='center', fontsize=11, fontweight='bold',
                        color='#00e5ff',
                        arrowprops=dict(arrowstyle='->', color='#00e5ff', lw=1.5))

    # === 前期波段高/低点水平线 ===
    ax.axhline(y=psh, color='#ff9800', linestyle='--', alpha=0.7, linewidth=1)
    ax.text(s['date_dt'].iloc[0], psh, f'  前期高点 {psh:.2f}', color='#ff9800',
            fontsize=9, va='bottom')

    ax.axhline(y=psl, color='#2196f3', linestyle='--', alpha=0.5, linewidth=1)
    ax.text(s['date_dt'].iloc[0], psl, f'  前期低点 {psl:.2f}', color='#2196f3',
            fontsize=9, va='top')

    # === Gap Floor 水平线 (突破点) ===
    ax.axhline(y=gap_floor, color='#ff5252', linestyle='-', alpha=0.8, linewidth=1.5)
    ax.text(s['date_dt'].iloc[-1], gap_floor, f'Gap Floor {gap_floor:.2f}  ',
            color='#ff5252', fontsize=9, ha='right', va='top', fontweight='bold')

    # === 缺口测试确认信号标注 ===
    if has_gt:
        gt_row = gt_rows.iloc[0]
        entry = gt_row.get('entry_3k_gap_test', np.nan)
        sl = gt_row.get('sl_3k_gap_test', np.nan)
        tp = gt_row.get('tp_3k_gap_test', np.nan)

        # Entry 箭头
        ax.annotate(f'Buy Stop\n{entry:.2f}',
                    xy=(gt_row['date_dt'], entry),
                    xytext=(30, 20), textcoords='offset points',
                    fontsize=10, fontweight='bold', color='#00e676',
                    arrowprops=dict(arrowstyle='->', color='#00e676', lw=2))

        # SL线
        if not np.isnan(sl):
            ax.axhline(y=sl, color='#ff1744', linestyle='-', alpha=0.6, linewidth=1)
            ax.text(s['date_dt'].iloc[-1], sl, f'SL {sl:.2f}  ',
                    color='#ff1744', fontsize=9, ha='right', va='top')

        # TP线
        if not np.isnan(tp):
            ax.axhline(y=tp, color='#00e676', linestyle='-', alpha=0.6, linewidth=1)
            ax.text(s['date_dt'].iloc[-1], tp, f'TP {tp:.2f}  ',
                    color='#00e676', fontsize=9, ha='right', va='bottom')

        # R:R
        risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
        reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
        rr = reward / risk if risk > 0 else 0
        rr_str = f'R:R = 1:{rr:.1f}' if rr > 0 else ''

        # 测量目标
        meas_target = 2 * gap_mid - psl
        ax.axhline(y=meas_target, color='#ffeb3b', linestyle='-.', alpha=0.5, linewidth=1)
        ax.text(s['date_dt'].iloc[-1], meas_target, f'Measured Target {meas_target:.2f}  ',
                color='#ffeb3b', fontsize=9, ha='right', va='bottom')

    else:
        rr_str = 'No Gap Test Confirmed'

    # === 标题 ===
    ax.set_title(f'3K Measured Gap: {symbol}  |  Signal: {sig_row["date"]}  |  {rr_str}',
                 fontsize=14, fontweight='bold', color='white', pad=15)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45, fontsize=8)
    ax.grid(alpha=0.15, color='white')
    ax.legend(loc='upper left', fontsize=9)

    plt.tight_layout()

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__), '..', 'strategy_lab')
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f'3k_signal_{symbol}_{signal_date}.png')
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  已保存: {filename}")
    return filename


if __name__ == '__main__':
    # 最高R:R: 603829 2023-06-27 (R:R=1:18.0)
    plot_3k_signal('603829', '2023-06-27')
    # 最低R:R: 603123 2023-10-24 (R:R=1:0.1)
    plot_3k_signal('603123', '2023-10-24')
