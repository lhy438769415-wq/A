"""
Gap+H2 止损案例研究 — 周线级别 K 线图绘制 & Discord 推送
===========================================================
功能:
  1. 从 gap_h2_trades.csv 中筛选止损交易 (pnl < 0)
  2. 随机抽样 10 笔
  3. 从 baostock.db 读取日线数据, 聚合为周线 K 线
  4. 绘制专业的周线蜡烛图 (标注入场/止损/持仓区间)
  5. 逐张推送到 Discord 频道

用法:
  python -m tools.gap_h2_sl_case_study
"""

import os
import sys
import io
import sqlite3
import random
import logging
import time

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import mplfinance as mpf
from datetime import datetime, timedelta

# ============================================================
# 路径 & 配置
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config.settings import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, FONT_PATH, DB_PATH
from core.log_config import get_logger

logger = get_logger(__name__)

TRADES_CSV = os.path.join(PROJECT_ROOT, "gap_h2_trades.csv")
SAMPLE_SIZE = 10
WEEKLY_CONTEXT_BEFORE = 40   # 入场前展示 40 周 (约 10 个月)
WEEKLY_CONTEXT_AFTER = 20    # 出场后展示 20 周 (约 5 个月)

# ============================================================
# 数据加载
# ============================================================
def load_stop_loss_trades():
    """从 trades.csv 加载止损交易 (pnl < 0)"""
    df = pd.read_csv(TRADES_CSV)
    # 过滤亏损交易
    sl_trades = df[df['pnl'] < 0].copy()
    logger.info(f"共 {len(df)} 笔交易, 其中止损 {len(sl_trades)} 笔")
    return sl_trades


def _normalize_symbol(symbol):
    """将股票代码标准化为 6 位数字字符串 (如 677 -> 000677)"""
    s = str(symbol).strip()
    # 移除可能的前缀如 sz. sh.
    for prefix in ['sz.', 'sh.', 'bj.']:
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.zfill(6)


def load_daily_bars_for_symbol(symbol, start_date, end_date):
    """从 baostock.db 加载指定股票的日线数据"""
    symbol = _normalize_symbol(symbol)
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT trade_date as date, open, high, low, close, volume
        FROM daily_bars
        WHERE symbol = ? AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """
    df = pd.read_sql(query, conn, params=[symbol, start_date, end_date])
    conn.close()
    
    if df.empty:
        return None
    
    df['date'] = pd.to_datetime(df['date'])
    return df


def resample_to_weekly(df):
    """
    将日线数据聚合为周线 K 线
    
    使用 ISO Week 标准, 以周五为每周截止日
    """
    df = df.copy()
    df = df.set_index('date')
    
    # 按周聚合 (周五收盘)
    weekly = df.resample('W-FRI').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
    }).dropna(subset=['open'])
    
    weekly.index.name = 'date'
    return weekly


# ============================================================
# 图表绘制
# ============================================================
def plot_weekly_sl_case(weekly_df, trade_info, case_idx):
    """
    绘制周线 K 线图 — 单笔止损案例
    
    标注:
      - 入场周: 绿色竖线 + 标签
      - 出场周: 红色竖线 + 标签
      - 入场价: 蓝色水平虚线
      - 止损区间: 红色半透明填充区间
    """
    if weekly_df.empty or len(weekly_df) < 5:
        logger.warning(f"Case {case_idx}: 周线数据不足, 跳过绘图")
        return None
    
    symbol = _normalize_symbol(trade_info['symbol'])
    entry_date = pd.Timestamp(trade_info['entry_date'])
    exit_date = pd.Timestamp(trade_info['exit_date'])
    entry_price = trade_info['entry_price']
    exit_price = trade_info['exit_price']
    pnl = trade_info['pnl']
    pnl_pct = trade_info['pnl_pct']
    holding_bars = trade_info['holding_bars']
    
    # 获取股票名称
    try:
        import json
        names_path = os.path.join(PROJECT_ROOT, 'config', 'stock_names.json')
        with open(names_path, 'r', encoding='utf-8') as f:
            stock_names = json.load(f)
        stock_name = stock_names.get(symbol, symbol)
    except Exception:
        stock_name = symbol
    
    # --- 字体设置 ---
    rc_params = {'font.family': 'SimHei', 'axes.unicode_minus': False}
    if os.path.exists(FONT_PATH):
        try:
            fm.fontManager.addfont(FONT_PATH)
            prop = fm.FontProperties(fname=FONT_PATH)
            rc_params['font.family'] = prop.get_name()
        except Exception:            pass
    
    # --- mplfinance 样式 (Al Brooks 红涨绿跌) ---
    mc = mpf.make_marketcolors(
        up='red', down='green',
        edge='inherit', wick='inherit',
        volume='in'
    )
    my_style = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle=':',
        y_on_right=True,
        rc=rc_params
    )
    
    # --- 计算 EMA20 (周线级别) ---
    weekly_df = weekly_df.copy()
    weekly_df['ema20'] = weekly_df['close'].ewm(span=20, adjust=False).mean()
    
    apds = []
    if 'ema20' in weekly_df.columns:
        apds.append(mpf.make_addplot(weekly_df['ema20'], color='orange', width=1.5))
    
    # --- 标注入场/出场位置 ---
    # 找到入场周和出场周在周线数据中的位置
    entry_week_mask = (weekly_df.index >= entry_date - timedelta(days=4)) & (weekly_df.index <= entry_date + timedelta(days=4))
    exit_week_mask = (weekly_df.index >= exit_date - timedelta(days=4)) & (weekly_df.index <= exit_date + timedelta(days=4))
    
    # 入场标记
    entry_marks = pd.Series(np.nan, index=weekly_df.index)
    if entry_week_mask.any():
        entry_marks[entry_week_mask] = weekly_df.loc[entry_week_mask, 'low'].values[0] * 0.96
    if not entry_marks.isna().all():
        apds.append(mpf.make_addplot(entry_marks, type='scatter', marker='^', markersize=150, color='blue', alpha=0.9))
    
    # 出场标记
    exit_marks = pd.Series(np.nan, index=weekly_df.index)
    if exit_week_mask.any():
        exit_marks[exit_week_mask] = weekly_df.loc[exit_week_mask, 'high'].values[0] * 1.04
    if not exit_marks.isna().all():
        apds.append(mpf.make_addplot(exit_marks, type='scatter', marker='v', markersize=150, color='red', alpha=0.9))
    
    # --- 水平线: 入场价 & 出场价 ---
    h_lines = [entry_price, exit_price]
    h_colors = ['#2563eb', '#ef4444']
    h_styles = ['--', '-.']
    
    # --- 标题 ---
    title = (
        f"[止损案例 #{case_idx}] {stock_name}({symbol}) | "
        f"周线级别\n"
        f"入场: {entry_date.strftime('%Y-%m-%d')} @ {entry_price:.2f} → "
        f"出场: {exit_date.strftime('%Y-%m-%d')} @ {exit_price:.2f} | "
        f"亏损: {pnl:,.0f}元 ({pnl_pct:+.1f}%) | "
        f"持仓: {holding_bars}天"
    )
    
    try:
        fig, axlist = mpf.plot(
            weekly_df,
            type='candle',
            style=my_style,
            addplot=apds if apds else None,
            hlines=dict(hlines=h_lines, colors=h_colors, linestyle=h_styles, linewidths=1.2),
            volume=True,
            title=title,
            ylabel='价格',
            figsize=(14, 9),
            returnfig=True,
        )
        
        ax = axlist[0]
        
        # --- 在图上添加价格标签 ---
        xlim = ax.get_xlim()
        label_x = xlim[1] * 0.99
        ax.text(label_x, entry_price, f"入场: {entry_price:.2f}",
                color='#2563eb', fontsize=9, fontweight='bold', va='center', ha='right',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#2563eb', alpha=0.8))
        ax.text(label_x, exit_price, f"止损: {exit_price:.2f}",
                color='#ef4444', fontsize=9, fontweight='bold', va='center', ha='right',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='#ef4444', alpha=0.8))
        
        # --- 在入场区间添加半透明高亮 ---
        # 找到入场和出场的 x 坐标 (使用 values 避免 iloc 错误)
        dates_list = weekly_df.index.tolist()
        entry_mask_vals = entry_week_mask.values if hasattr(entry_week_mask, 'values') else entry_week_mask
        exit_mask_vals = exit_week_mask.values if hasattr(exit_week_mask, 'values') else exit_week_mask
        entry_x = None
        exit_x = None
        for i in range(len(dates_list)):
            if i < len(entry_mask_vals) and entry_mask_vals[i]:
                entry_x = i
            if i < len(exit_mask_vals) and exit_mask_vals[i]:
                exit_x = i
        
        if entry_x is not None and exit_x is not None:
            ax.axvspan(entry_x - 0.5, exit_x + 0.5, alpha=0.08, color='#ef4444')
        
        # --- 图例 ---
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], color='orange', lw=1.5, label='EMA20(周线)'),
            Line2D([0], [0], marker='^', color='w', markerfacecolor='blue', markersize=10, label='入场周'),
            Line2D([0], [0], marker='v', color='w', markerfacecolor='red', markersize=10, label='止损周'),
            Line2D([0], [0], color='#2563eb', linestyle='--', lw=1, label=f'入场价 {entry_price:.2f}'),
            Line2D([0], [0], color='#ef4444', linestyle='-.', lw=1, label=f'止损价 {exit_price:.2f}'),
        ]
        ax.legend(handles=legend_elements, loc='upper left', fontsize=8, framealpha=0.85, edgecolor='gray')
        
        # --- 保存到 BytesIO ---
        buf = io.BytesIO()
        fig.savefig(buf, dpi=200, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        buf.seek(0)
        
        logger.info(f"Case #{case_idx}: {stock_name}({symbol}) 图表已生成")
        return buf
    
    except Exception as e:
        logger.error(f"Case #{case_idx} 绘图失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================
# Discord 推送
# ============================================================
def send_discord_image_with_text(img_buffer, filename, content):
    """向 Discord 发送带文字的图片"""
    import requests
    
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        logger.warning("Discord 配置缺失, 跳过推送")
        return False
    
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    proxies = {"http": None, "https": None}
    
    img_buffer.seek(0)
    files = {"file": (filename, img_buffer.read(), "image/png")}
    data = {}
    if content:
        data["content"] = content
    
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, files=files, data=data,
                                proxies=proxies, timeout=30)
            if resp.status_code in [200, 201]:
                logger.info(f"✅ Discord 推送成功: {filename}")
                return True
            elif resp.status_code == 429:
                retry_after = resp.json().get('retry_after', 3)
                logger.warning(f"⚠️ Discord 限速, 等待 {retry_after}s...")
                time.sleep(retry_after)
            else:
                logger.error(f"❌ Discord 推送失败: [{resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ 推送异常 (尝试 {attempt+1}): {e}")
            time.sleep(2)
    
    return False


def send_discord_text(content):
    """发送纯文本消息"""
    import requests
    
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID:
        return
    
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    proxies = {"http": None, "https": None}
    
    try:
        resp = requests.post(url, json={"content": content}, headers=headers,
                            timeout=15, proxies=proxies)
        if resp.status_code not in [200, 201]:
            logger.warning(f"文本推送状态: {resp.status_code}")
    except Exception as e:
        logger.error(f"文本推送失败: {e}")


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    logger.info("=" * 60)
    logger.info("Gap+H2 止损案例研究 — 周线级别分析")
    logger.info("=" * 60)
    
    # 1. 加载止损交易
    sl_trades = load_stop_loss_trades()
    if sl_trades.empty:
        logger.error("没有找到止损交易!")
        return
    
    # 2. 抽样 10 笔 (如果不足 10 笔则全取)
    n = min(SAMPLE_SIZE, len(sl_trades))
    sampled = sl_trades.sample(n=n, random_state=42)
    logger.info(f"抽样 {n} 笔止损案例:")
    
    for i, (_, row) in enumerate(sampled.iterrows()):
        logger.info(f"  #{i+1}: {row['symbol']} | 入场 {row['entry_date']} → 出场 {row['exit_date']} | "
                    f"亏损 {row['pnl']:,.0f}元 ({row['pnl_pct']:+.1f}%)")
    
    # 3. 推送开场消息
    header_msg = (
        "📊 **Gap+H2 策略 — 止损案例研究 (周线级别)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 生成时间: {t0.strftime('%Y-%m-%d %H:%M')}\n"
        f"📈 策略总交易: 44 笔 | 胜率: 47.73%\n"
        f"🔍 本次抽样: {n} 笔止损案例\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⬇️ 以下逐笔推送周线K线图, 请研究价格行为..."
    )
    send_discord_text(header_msg)
    time.sleep(1)
    
    # 4. 逐笔生成周线图并推送
    success_count = 0
    
    for case_idx, (_, trade) in enumerate(sampled.iterrows(), 1):
        symbol = trade['symbol']
        entry_date = pd.Timestamp(trade['entry_date'])
        exit_date = pd.Timestamp(trade['exit_date'])
        
        # 计算数据窗口: 入场前 WEEKLY_CONTEXT_BEFORE 周 ~ 出场后 WEEKLY_CONTEXT_AFTER 周
        data_start = (entry_date - timedelta(weeks=WEEKLY_CONTEXT_BEFORE)).strftime('%Y-%m-%d')
        data_end = (exit_date + timedelta(weeks=WEEKLY_CONTEXT_AFTER)).strftime('%Y-%m-%d')
        
        logger.info(f"\n--- Case #{case_idx}/{n}: {symbol} ---")
        logger.info(f"  数据窗口: {data_start} ~ {data_end}")
        
        # 加载日线数据
        daily_df = load_daily_bars_for_symbol(symbol, data_start, data_end)
        if daily_df is None or daily_df.empty:
            logger.warning(f"  ❌ 日线数据为空, 跳过")
            continue
        
        logger.info(f"  日线数据: {len(daily_df)} 条")
        
        # 聚合为周线
        weekly_df = resample_to_weekly(daily_df)
        logger.info(f"  周线数据: {len(weekly_df)} 条")
        
        # 绘图
        chart_buf = plot_weekly_sl_case(weekly_df, trade, case_idx)
        if chart_buf is None:
            continue
        
        # Discord 推送
        # 获取股票名称
        try:
            import json
            names_path = os.path.join(PROJECT_ROOT, 'config', 'stock_names.json')
            with open(names_path, 'r', encoding='utf-8') as f:
                stock_names = json.load(f)
            stock_name = stock_names.get(symbol, symbol)
        except Exception:
            stock_name = symbol
        
        caption = (
            f"**止损案例 #{case_idx}/{n}** — {stock_name}({symbol})\n"
            f"📅 入场: {trade['entry_date']} @ ¥{trade['entry_price']:.2f}\n"
            f"📅 出场: {trade['exit_date']} @ ¥{trade['exit_price']:.2f}\n"
            f"💰 盈亏: {trade['pnl']:+,.0f}元 ({trade['pnl_pct']:+.1f}%)\n"
            f"⏱ 持仓: {trade['holding_bars']}个交易日"
        )
        
        filename = f"gap_h2_sl_case_{case_idx}_{symbol}.png"
        ok = send_discord_image_with_text(chart_buf, filename, caption)
        if ok:
            success_count += 1
        
        # 避免 Discord 限速
        time.sleep(1.5)
    
    # 5. 推送结尾消息
    footer_msg = (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ 止损案例研究推送完成 ({success_count}/{n} 张)\n"
        "💡 **研究重点**:\n"
        "  • 入场前的周线趋势结构是否健康?\n"
        "  • 缺口突破后回调是否过深 (周线级别)?\n"
        "  • EMA20 周线级别支撑是否有效?\n"
        "  • 止损后价格走势如何? (是否反转)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    send_discord_text(footer_msg)
    
    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"\n🎉 全部完成! 耗时 {elapsed:.1f}s, 成功推送 {success_count}/{n} 张图表")


if __name__ == "__main__":
    main()
