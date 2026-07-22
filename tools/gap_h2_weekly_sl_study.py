"""
Gap+H2 周线级别止损案例研究 — 严格基于策略核心逻辑
=================================================
功能:
  1. 使用项目已有的 GapH2Strategy + weekly_bars 数据运行全 A 股回测
  2. 严格遵循 Al Brooks PA 原则:
     - 60 根周线最高点突破
     - LHLL → HH → LHLL 两腿回调状态机
     - 缺口存活 (Gap Floor 不得被击穿)
     - Buy Stop 入场 + 生命周期过滤
  3. 从止损交易中抽样 10 笔
  4. 绘制周线蜡烛图 (含策略核心锚点) 并推送 Discord

用法:
  python -m tools.gap_h2_weekly_sl_study
"""

import os
import sys
import io
import json
import random
import logging
import time
from datetime import datetime
from collections import defaultdict

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import mplfinance as mpf

# ============================================================
# 项目路径初始化
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.data_provider import get_stock_data_weekly, get_stock_list, get_stock_name
from core.calculator import add_indicators
from core.strategies.gap_h2_strategy import GapH2Strategy
from config.settings import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, FONT_PATH
from core.log_config import get_logger

logger = get_logger(__name__)

SAMPLE_SIZE = 10
LIFECYCLE_TIMEOUT_BARS = 30  # 与 backtest_gap_h2.py 一致


# ============================================================
# 第一步: 全 A 股周线回测 (复用 backtest_gap_h2.py 核心逻辑)
# ============================================================
def evaluate_trade_with_context(df, signal_idx):
    """
    含完整三条生命周期过滤的交易模拟 (改编自 backtest_gap_h2.py)
    额外返回策略上下文数据用于绘图
    """
    try:
        sig_row = df.iloc[signal_idx]
        entry_price = sig_row['entry_gap_h2']
        sl_price = sig_row['sl_gap_h2']
        tp_price = sig_row['tp_gap_h2']
        gap_floor = sig_row.get('gap_h2_floor_exact', sl_price)

        if pd.isna(entry_price) or pd.isna(sl_price) or pd.isna(tp_price):
            return None

        # 基础合法性校验: TP > Entry > SL > 0
        if not (tp_price > entry_price > sl_price > 0):
            return None

        # 回调特征
        bsg = sig_row.get('bars_since_breakout_h2', 0)
        sig_q = float(sig_row.get('sig_bar_quality_h2', 0))

        # 信号日期
        sig_date_col = 'trade_date' if 'trade_date' in df.columns else 'date'
        sig_date = sig_row[sig_date_col] if sig_date_col in sig_row.index else str(df.index[signal_idx])

        post = df.iloc[signal_idx + 1:]
        if post.empty:
            return None

        status = 'WAITING'
        bars_waited = 0
        entry_date = actual_entry = None
        exit_date = exit_price = None

        for i in range(len(post)):
            row = post.iloc[i]
            row_date = row[sig_date_col] if sig_date_col in row.index else post.index[i]

            if status == 'WAITING':
                bars_waited += 1
                # 生命周期过滤 1: 缺口被填补
                if row['low'] < gap_floor - 1e-3:
                    return None  # 废单
                # 生命周期过滤 2: TP 先于 Entry 被触及
                if row['high'] >= tp_price:
                    return None  # 作废
                # 生命周期过滤 3: 超时
                if bars_waited > LIFECYCLE_TIMEOUT_BARS:
                    return None  # 超时
                # Buy Stop 触发
                if row['high'] >= entry_price:
                    actual_entry = max(entry_price, row['open'])
                    entry_date = row_date
                    status = 'IN_TRADE'
                    # 当根 K 线检查
                    if row['low'] <= sl_price:
                        exit_price = sl_price
                        exit_date = row_date
                        break
                    if row['high'] >= tp_price:
                        exit_price = tp_price
                        exit_date = row_date
                        break

            elif status == 'IN_TRADE':
                if row['low'] <= sl_price:
                    exit_price = min(sl_price, row['open'])
                    exit_date = row_date
                    break
                if row['high'] >= tp_price:
                    exit_price = tp_price
                    exit_date = row_date
                    break

        if status == 'WAITING' or exit_price is None:
            return None

        pnl_pct = (exit_price / actual_entry - 1) * 100
        is_win = exit_price >= tp_price

        return {
            'signal_idx': signal_idx,
            'signal_date': str(sig_date)[:10],
            'entry_date': str(entry_date)[:10],
            'exit_date': str(exit_date)[:10],
            'entry_price': round(actual_entry, 4),
            'exit_price': round(exit_price, 4),
            'sl_price': round(sl_price, 4),
            'tp_price': round(tp_price, 4),
            'gap_floor': round(gap_floor, 4),
            'pnl_pct': round(pnl_pct, 2),
            'status': 'WIN' if is_win else 'LOSS',
            'sig_quality': sig_q,
            'bars_since_breakout': bsg,
        }

    except Exception as e:
        return None


def backtest_single_weekly(code):
    """对单只股票运行周线级别 Gap H2 回测"""
    try:
        df = get_stock_data_weekly(code, limit=800)
        if df is None or len(df) < 100:
            return []

        df = add_indicators(df)
        strategy = GapH2Strategy()
        df = strategy.calculate_signals(df)

        sig_col = 'signal_gap_h2'
        if sig_col not in df.columns:
            return []

        indices = [i for i, v in enumerate(df[sig_col]) if v]
        results = []
        for idx in indices:
            trade = evaluate_trade_with_context(df, idx)
            if trade is not None:
                trade['code'] = code
                trade['symbol'] = code.split('.')[-1]
                results.append(trade)

        return results
    except Exception:
        return []


def run_weekly_backtest():
    """全 A 股周线 Gap H2 回测"""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from config import settings

    codes = get_stock_list()
    if not codes:
        logger.error("无法获取股票列表!")
        return []

    n = len(codes)
    logger.info(f"[周线回测] 开始扫描 {n} 只股票...")

    all_trades = []
    t0 = time.time()

    # 多进程回测
    with ProcessPoolExecutor(max_workers=settings.MAX_WORKERS) as exe:
        futs = {exe.submit(backtest_single_weekly, c): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                logger.info(f"  进度: {done}/{n}, 累计信号: {len(all_trades)}")
            r = f.result()
            if r:
                all_trades.extend(r)

    elapsed = time.time() - t0
    wins = [t for t in all_trades if t['status'] == 'WIN']
    losses = [t for t in all_trades if t['status'] == 'LOSS']

    logger.info(f"[周线回测] 完成! 耗时 {elapsed:.1f}s")
    logger.info(f"  已结案交易: {len(all_trades)} 笔")
    logger.info(f"  胜: {len(wins)} | 负: {len(losses)}")
    if all_trades:
        wr = len(wins) / len(all_trades) * 100
        logger.info(f"  胜率: {wr:.1f}%")

    return all_trades


# ============================================================
# 第二步: 绘制周线 K 线图 (含策略锚点)
# ============================================================
def plot_weekly_case(code, trade, case_idx, total):
    """
    绘制周线 K 线图 — 严格标注策略核心锚点
    
    标注:
      - 突破缺口 K 线 (创 60 周最高)
      - Gap Floor (SL 线)
      - Entry Buy Stop 线
      - TP 目标线
      - 入场周 / 出场周标记
      - EMA20 (周线级别)
    """
    try:
        df = get_stock_data_weekly(code, limit=800)
        if df is None or len(df) < 100:
            return None

        df = add_indicators(df)
        strategy = GapH2Strategy()
        df = strategy.calculate_signals(df)

        # 确定日期列
        date_col = 'trade_date' if 'trade_date' in df.columns else 'date'

        # 信号 K 线位置
        signal_idx = trade['signal_idx']
        if signal_idx >= len(df):
            return None

        # 计算展示窗口: 突破前 30 周 ~ 出场后 15 周
        context_before = 30
        context_after = 15
        start_idx = max(0, signal_idx - context_before)

        # 找到出场周的位置
        exit_date_str = trade['exit_date']
        exit_idx = signal_idx
        for i in range(signal_idx, len(df)):
            d = str(df.iloc[i][date_col])[:10]
            if d >= exit_date_str:
                exit_idx = i
                break

        end_idx = min(len(df), exit_idx + context_after)
        plot_df = df.iloc[start_idx:end_idx].copy()

        if len(plot_df) < 10:
            return None

        # --- 准备绘图 DataFrame ---
        if date_col in plot_df.columns:
            plot_df = plot_df.copy()
            plot_df[date_col] = pd.to_datetime(plot_df[date_col])
            plot_df = plot_df.set_index(date_col)
        elif not isinstance(plot_df.index, pd.DatetimeIndex):
            plot_df.index = pd.to_datetime(plot_df.index)

        # --- 字体设置 ---
        rc_params = {'font.family': 'SimHei', 'axes.unicode_minus': False}
        if os.path.exists(FONT_PATH):
            try:
                fm.fontManager.addfont(FONT_PATH)
                prop = fm.FontProperties(fname=FONT_PATH)
                rc_params['font.family'] = prop.get_name()
            except Exception:                pass

        # --- mplfinance 样式 (Al Brooks 红涨绿跌) ---
        mc = mpf.make_marketcolors(up='red', down='green', edge='inherit', wick='inherit', volume='in')
        my_style = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, rc=rc_params)

        apds = []
        # EMA20
        if 'ema20' in plot_df.columns:
            apds.append(mpf.make_addplot(plot_df['ema20'], color='orange', width=1.5))

        # --- 标注信号 K 线 (信号发出的那根周线) ---
        signal_date_ts = pd.Timestamp(trade['signal_date'])
        entry_date_ts = pd.Timestamp(trade['entry_date'])
        exit_date_ts = pd.Timestamp(trade['exit_date'])

        # 信号标记 (第二次回调 LHLL)
        sig_marks = pd.Series(np.nan, index=plot_df.index)
        for idx_d in plot_df.index:
            if abs((idx_d - signal_date_ts).days) <= 5:
                sig_marks[idx_d] = plot_df.loc[idx_d, 'low'] * 0.96

        if not sig_marks.isna().all():
            apds.append(mpf.make_addplot(sig_marks, type='scatter', marker='*', markersize=200, color='gold', alpha=0.9))

        # 入场标记
        entry_marks = pd.Series(np.nan, index=plot_df.index)
        for idx_d in plot_df.index:
            if abs((idx_d - entry_date_ts).days) <= 5:
                entry_marks[idx_d] = plot_df.loc[idx_d, 'low'] * 0.95

        if not entry_marks.isna().all():
            apds.append(mpf.make_addplot(entry_marks, type='scatter', marker='^', markersize=120, color='blue', alpha=0.9))

        # 出场标记 (止损)
        exit_marks = pd.Series(np.nan, index=plot_df.index)
        for idx_d in plot_df.index:
            if abs((idx_d - exit_date_ts).days) <= 5:
                exit_marks[idx_d] = plot_df.loc[idx_d, 'high'] * 1.05

        if not exit_marks.isna().all():
            apds.append(mpf.make_addplot(exit_marks, type='scatter', marker='v', markersize=120, color='red', alpha=0.9))

        # --- 水平线: Gap Floor(SL) / Entry / TP ---
        h_lines = []
        h_colors = []
        h_styles = []

        sl = trade['sl_price']
        entry = trade['entry_price']
        tp = trade['tp_price']

        if sl > 0:
            h_lines.append(sl)
            h_colors.append('#22c55e')  # 绿色 = Gap Floor / SL
            h_styles.append('-')
        if entry > 0:
            h_lines.append(entry)
            h_colors.append('#2563eb')  # 蓝色 = Buy Stop Entry
            h_styles.append('--')
        if tp > 0:
            h_lines.append(tp)
            h_colors.append('#ef4444')  # 红色 = TP
            h_styles.append('--')

        # --- 股票名称 ---
        stock_name = get_stock_name(code)
        symbol = code.split('.')[-1]

        # --- 标题 ---
        title = (
            f"[止损案例 #{case_idx}/{total}] {stock_name}({symbol}) | 周线级别 Gap+H2\n"
            f"信号: {trade['signal_date']} | 入场: {trade['entry_date']} @ {entry:.2f} → "
            f"止损: {trade['exit_date']} @ {trade['exit_price']:.2f} | "
            f"{trade['pnl_pct']:+.1f}% | "
            f"回调{trade['bars_since_breakout']}周"
        )

        hline_dict = dict(hlines=h_lines, colors=h_colors, linestyle=h_styles, linewidths=1.5) if h_lines else None

        fig, axlist = mpf.plot(
            plot_df,
            type='candle',
            style=my_style,
            addplot=apds if apds else None,
            hlines=hline_dict,
            volume=True,
            title=title,
            ylabel='价格',
            figsize=(14, 9),
            returnfig=True,
        )

        ax = axlist[0]
        xlim = ax.get_xlim()
        label_x = xlim[1] * 0.99

        # 价格标签
        if sl > 0:
            ax.text(label_x, sl, f"Gap Floor(SL): {sl:.2f}",
                    color='#22c55e', fontsize=8, fontweight='bold', va='center', ha='right',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#22c55e', alpha=0.85))
        if entry > 0:
            ax.text(label_x, entry, f"Buy Stop: {entry:.2f}",
                    color='#2563eb', fontsize=8, fontweight='bold', va='center', ha='right',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#2563eb', alpha=0.85))
        if tp > 0:
            ax.text(label_x, tp, f"TP(2*GF-PSL): {tp:.2f}",
                    color='#ef4444', fontsize=8, fontweight='bold', va='center', ha='right',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#ef4444', alpha=0.85))

        # 图例
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], color='orange', lw=1.5, label='EMA20(周线)'),
            Line2D([0], [0], color='#22c55e', lw=1.5, label='Gap Floor (SL)'),
            Line2D([0], [0], color='#2563eb', linestyle='--', lw=1, label='Buy Stop Entry'),
            Line2D([0], [0], color='#ef4444', linestyle='--', lw=1, label='TP Target'),
            Line2D([0], [0], marker='*', color='w', markerfacecolor='gold', markersize=12, label='H2 信号周'),
            Line2D([0], [0], marker='^', color='w', markerfacecolor='blue', markersize=10, label='入场周'),
            Line2D([0], [0], marker='v', color='w', markerfacecolor='red', markersize=10, label='止损周'),
        ]
        ax.legend(handles=legend_elements, loc='upper left', fontsize=7, framealpha=0.85, edgecolor='gray')

        buf = io.BytesIO()
        fig.savefig(buf, dpi=200, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        buf.seek(0)

        logger.info(f"  Case #{case_idx}: {stock_name}({symbol}) 图表已生成")
        return buf

    except Exception as e:
        logger.error(f"  Case #{case_idx} 绘图失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================
# 第三步: Discord 推送
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
                logger.info(f"  ✅ Discord 推送成功: {filename}")
                return True
            elif resp.status_code == 429:
                retry_after = resp.json().get('retry_after', 3)
                logger.warning(f"  ⚠️ Discord 限速, 等待 {retry_after}s...")
                time.sleep(retry_after)
            else:
                logger.error(f"  ❌ Discord 推送失败: [{resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            logger.error(f"  ❌ 推送异常 (尝试 {attempt+1}): {e}")
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
    except Exception as e:
        logger.error(f"文本推送失败: {e}")


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    logger.info("=" * 70)
    logger.info("Gap+H2 周线级别止损案例研究 — 严格基于策略核心逻辑")
    logger.info("=" * 70)
    logger.info("策略条件:")
    logger.info("  1. 突破: HH+HL 跨越 60 周 K 线最高点 → 结构性缺口")
    logger.info("  2. 回调: LHLL → HH(High 1) → LHLL(High 2) 两腿状态机")
    logger.info("  3. 缺口: Gap Floor 在整个回调期间不被击穿")
    logger.info("  4. 入场: Buy Stop 挂在 H2 信号 K 线最高点")
    logger.info("  5. SL = Gap Floor, TP = 2*Gap_Floor - Prior_Swing_Low")
    logger.info("  6. 生命周期过滤: 缺口填补废单 / TP先达废单 / 30周超时")

    # 1. 运行全 A 股周线回测
    all_trades = run_weekly_backtest()
    if not all_trades:
        logger.error("回测无结果!")
        return

    # 2. 筛选止损交易
    sl_trades = [t for t in all_trades if t['status'] == 'LOSS']
    logger.info(f"\n止损交易: {len(sl_trades)} 笔 (总 {len(all_trades)} 笔)")

    if not sl_trades:
        logger.error("没有止损交易!")
        return

    # 3. 抽样
    n = min(SAMPLE_SIZE, len(sl_trades))
    random.seed(42)
    sampled = random.sample(sl_trades, n)

    logger.info(f"\n抽样 {n} 笔止损案例:")
    for i, t in enumerate(sampled, 1):
        name = get_stock_name(t['code'])
        logger.info(f"  #{i}: {name}({t['symbol']}) | "
                    f"信号 {t['signal_date']} → 入场 {t['entry_date']} → 止损 {t['exit_date']} | "
                    f"{t['pnl_pct']:+.1f}% | 回调{t['bars_since_breakout']}周")

    # 4. 推送开场消息
    wins = [t for t in all_trades if t['status'] == 'WIN']
    total = len(all_trades)
    wr = len(wins) / total * 100 if total > 0 else 0

    header_msg = (
        "📊 **Gap+H2 策略 — 周线级别止损案例研究**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 生成时间: {t0.strftime('%Y-%m-%d %H:%M')}\n"
        f"📈 周线级别已结案交易: {total} 笔\n"
        f"🏆 胜率: {wr:.1f}% ({len(wins)}W / {len(sl_trades)}L)\n"
        f"🔍 本次抽样: {n} 笔止损案例\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "**策略核心条件**:\n"
        "  • 突破 60 周最高点 → 结构性缺口\n"
        "  • LHLL→HH→LHLL 两腿回调状态机\n"
        "  • Gap Floor 全程不击穿\n"
        "  • Buy Stop @ H2 信号 K 线最高\n"
        "  • SL = Gap Floor | TP = 2*GF - PSL\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⬇️ 以下逐笔推送周线K线图..."
    )
    send_discord_text(header_msg)
    time.sleep(1)

    # 5. 逐笔生成图表并推送
    success_count = 0

    for case_idx, trade in enumerate(sampled, 1):
        logger.info(f"\n--- 正在绘制 Case #{case_idx}/{n}: {trade['code']} ---")

        chart_buf = plot_weekly_case(trade['code'], trade, case_idx, n)
        if chart_buf is None:
            continue

        stock_name = get_stock_name(trade['code'])
        symbol = trade['symbol']

        caption = (
            f"**止损案例 #{case_idx}/{n}** — {stock_name}({symbol})\n"
            f"⭐ H2 信号: {trade['signal_date']} (回调{trade['bars_since_breakout']}周)\n"
            f"📥 入场: {trade['entry_date']} @ ¥{trade['entry_price']:.2f} (Buy Stop)\n"
            f"📤 止损: {trade['exit_date']} @ ¥{trade['exit_price']:.2f}\n"
            f"🛡️ Gap Floor (SL): ¥{trade['sl_price']:.2f}\n"
            f"🎯 TP 目标: ¥{trade['tp_price']:.2f}\n"
            f"💰 盈亏: {trade['pnl_pct']:+.1f}%"
        )

        filename = f"gap_h2_weekly_sl_{case_idx}_{symbol}.png"
        ok = send_discord_image_with_text(chart_buf, filename, caption)
        if ok:
            success_count += 1

        time.sleep(1.5)

    # 6. 推送结尾消息
    footer_msg = (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ 周线级别止损案例研究推送完成 ({success_count}/{n} 张)\n"
        "💡 **Al Brooks PA 研究重点**:\n"
        "  • 突破 K 线是否真正创了 60 周新高? (Gap 的合法性)\n"
        "  • 两腿回调是否清晰? (LHLL→HH→LHLL 形态完整度)\n"
        "  • Gap Floor 在回调期间是否被 Low 严格尊重?\n"
        "  • 入场后价格行为: 是否出现强势空头陷阱?\n"
        "  • 止损后走势: 价格是否最终走向了 TP 方向?\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    send_discord_text(footer_msg)

    elapsed = (datetime.now() - t0).total_seconds()
    logger.info(f"\n🎉 全部完成! 耗时 {elapsed:.1f}s, 成功推送 {success_count}/{n} 张图表")


if __name__ == "__main__":
    main()
