"""Gap+H2 回测图表生成"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

equity = pd.read_csv('gap_h2_equity.csv', parse_dates=['date'])
trades = pd.read_csv('gap_h2_trades.csv')

peak = equity['value'].cummax()
dd = (equity['value'] / peak - 1) * 100

# ========== 图1: 权益曲线 + 回撤 ==========
fig, ax = plt.subplots(figsize=(14, 6))
ax.plot(equity['date'], equity['value'], color='#2563eb', linewidth=1.5, label='策略权益')
ax.fill_between(equity['date'], equity['value'].min(), equity['value'], alpha=0.08, color='#2563eb')
for _, t in trades.iterrows():
    entry_d = pd.Timestamp(t['entry_date'])
    exit_d = pd.Timestamp(t['exit_date'])
    color = '#22c55e' if t['pnl'] > 0 else '#ef4444'
    ax.axvspan(entry_d, exit_d, alpha=0.12, color=color, linewidth=0)
ax2 = ax.twinx()
ax2.fill_between(equity['date'], dd, 0, color='#ef4444', alpha=0.2, label='回撤')
ax2.set_ylabel('回撤 (%)', color='#ef4444', fontsize=11)
ax2.tick_params(axis='y', labelcolor='#ef4444')
ax.set_title('Gap+H2 两腿回调策略 — 权益曲线', fontsize=15, fontweight='bold')
ax.set_ylabel('权益 (元)', fontsize=11)
ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
ax.grid(axis='y', alpha=0.3)
ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f'{x:,.0f}'))
fig.autofmt_xdate(rotation=30)
plt.tight_layout()
plt.savefig('gap_h2_equity_curve.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 1 OK')

# ========== 图2: 每笔交易 PnL 柱状图 ==========
fig, ax = plt.subplots(figsize=(14, 5))
colors = ['#22c55e' if p > 0 else '#ef4444' for p in trades['pnl']]
ax.bar(range(len(trades)), trades['pnl'] / 10000, color=colors, alpha=0.85, edgecolor='white', linewidth=0.5)
ax.axhline(0, color='gray', linewidth=0.8)
ax.set_title('每笔交易盈亏 (万元)', fontsize=14, fontweight='bold')
ax.set_xlabel('交易序号', fontsize=11)
ax.set_ylabel('盈亏 (万元)', fontsize=11)
ax.grid(axis='y', alpha=0.3)
plt.tight_layout()
plt.savefig('gap_h2_pnl_per_trade.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 2 OK')

# ========== 图3: 累计 PnL ==========
fig, ax = plt.subplots(figsize=(14, 5))
cum_pnl = trades['pnl'].cumsum() / 10000
ax.plot(range(1, len(cum_pnl)+1), cum_pnl.values, color='#2563eb', linewidth=2, marker='o', markersize=4)
ax.fill_between(range(1, len(cum_pnl)+1), 0, cum_pnl.values, where=cum_pnl.values >= 0, alpha=0.15, color='#22c55e')
ax.fill_between(range(1, len(cum_pnl)+1), 0, cum_pnl.values, where=cum_pnl.values < 0, alpha=0.15, color='#ef4444')
ax.axhline(0, color='gray', linewidth=0.8)
ax.set_title('累计盈亏 (万元)', fontsize=14, fontweight='bold')
ax.set_xlabel('交易序号', fontsize=11)
ax.set_ylabel('累计盈亏 (万元)', fontsize=11)
ax.grid(axis='y', alpha=0.3)
plt.tight_layout()
plt.savefig('gap_h2_cumulative_pnl.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 3 OK')

# ========== 图4: PnL 分布 + 持仓天数 ==========
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
pnl_pcts = trades['pnl_pct']
ax1.hist(pnl_pcts, bins=20, color='#2563eb', alpha=0.7, edgecolor='white')
ax1.axvline(0, color='gray', linewidth=1, linestyle='--')
mean_pct = pnl_pcts.mean()
ax1.axvline(mean_pct, color='#f59e0b', linewidth=2, linestyle='-', label='均值 %.1f%%' % mean_pct)
ax1.set_title('盈亏比例分布', fontsize=13, fontweight='bold')
ax1.set_xlabel('盈亏 %', fontsize=11)
ax1.set_ylabel('频次', fontsize=11)
ax1.legend()

avg_hold = trades['holding_bars'].mean()
ax2.hist(trades['holding_bars'], bins=20, color='#8b5cf6', alpha=0.7, edgecolor='white')
ax2.axvline(avg_hold, color='#f59e0b', linewidth=2, linestyle='-', label='均值 %.0f 天' % avg_hold)
ax2.set_title('持仓天数分布', fontsize=13, fontweight='bold')
ax2.set_xlabel('持仓天数', fontsize=11)
ax2.set_ylabel('频次', fontsize=11)
ax2.legend()
plt.tight_layout()
plt.savefig('gap_h2_distributions.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 4 OK')

# ========== 图5: 月度收益热力图 ==========
equity['month'] = equity['date'].dt.to_period('M')
monthly = equity.groupby('month')['value'].last()
monthly_ret = monthly.pct_change() * 100

years = sorted(monthly_ret.index.year.unique())
months_labels = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']
matrix = pd.DataFrame(index=years, columns=range(1,13), dtype=float)
for idx, val in monthly_ret.items():
    if not np.isnan(val):
        matrix.loc[idx.year, idx.month] = val

fig, ax = plt.subplots(figsize=(12, 4))
data = matrix.values.astype(float)
im = ax.imshow(data, cmap='RdYlGn', aspect='auto', vmin=-15, vmax=15)
ax.set_yticks(range(len(years)))
ax.set_yticklabels(years)
ax.set_xticks(range(12))
ax.set_xticklabels(months_labels, fontsize=9)
ax.set_title('月度收益热力图 (%)', fontsize=14, fontweight='bold')
for i in range(data.shape[0]):
    for j in range(data.shape[1]):
        v = data[i, j]
        if not np.isnan(v):
            color = 'white' if abs(v) > 8 else 'black'
            ax.text(j, i, '%.1f' % v, ha='center', va='center', fontsize=8, color=color)
plt.colorbar(im, ax=ax, shrink=0.8, label='收益率 %')
plt.tight_layout()
plt.savefig('gap_h2_monthly_heatmap.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 5 OK')

# ========== 图6: 滚动 Sharpe & 回撤 ==========
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
daily_ret = equity['value'].pct_change()
roll_sharpe = (daily_ret.rolling(60).mean() / daily_ret.rolling(60).std()) * np.sqrt(252)
ax1.plot(equity['date'], roll_sharpe, color='#2563eb', linewidth=1.2)
ax1.axhline(1.0, color='#f59e0b', linewidth=1, linestyle='--', alpha=0.7, label='Sharpe = 1.0')
ax1.axhline(0, color='gray', linewidth=0.8)
ax1.fill_between(equity['date'], 0, roll_sharpe, where=roll_sharpe > 0, alpha=0.15, color='#22c55e')
ax1.fill_between(equity['date'], 0, roll_sharpe, where=roll_sharpe <= 0, alpha=0.15, color='#ef4444')
ax1.set_ylabel('滚动 Sharpe (60日)', fontsize=11)
ax1.set_title('滚动风险指标', fontsize=14, fontweight='bold')
ax1.legend(loc='upper left')
ax1.grid(axis='y', alpha=0.3)
ax2.fill_between(equity['date'], dd, 0, color='#ef4444', alpha=0.5)
ax2.set_ylabel('回撤 (%)', fontsize=11)
ax2.set_xlabel('日期', fontsize=11)
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
fig.autofmt_xdate(rotation=30)
plt.tight_layout()
plt.savefig('gap_h2_rolling_metrics.png', dpi=150, bbox_inches='tight')
plt.close()
print('Chart 6 OK')

print('\nAll 6 charts generated successfully!')
