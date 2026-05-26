import io
import os
import requests
import hashlib
import base64
import pandas as pd
import numpy as np
import matplotlib
import logging
# 🟢【必须加】强制使用非交互式后端，防止 No Display 报错
matplotlib.use('Agg')
import mplfinance as mpf
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import textwrap
from matplotlib.patches import Rectangle
from PIL import Image
import re

def strip_emoji(text):
    """移除可能导致 matplotlib 乱码的 Emoji 及部分特殊符号"""
    if not text:
        return text
    text = str(text)
    # Remove chars in supplementary multilingual plane (most emojis)
    text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    # Remove some common BMP emojis and symbols
    text = re.sub(r'[\u2600-\u27bf]', '', text)
    # Remove variation selectors
    text = re.sub(r'[\ufe00-\ufe0f]', '', text)
    return text.strip()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 🟢 引入 V7 核心计算器
from core.calculator import add_indicators
# 🟢 引入 Data Provider (Names)
from core.data_provider import get_stock_name

# 假设 config 结构已建立，如果报错请检查路径
try:
    from config.settings import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, FONT_PATH
except ImportError:
    # 兼容模式：如果没有配置文件，给予默认空值或硬编码
    DISCORD_BOT_TOKEN = "" 
    DISCORD_CHANNEL_ID = ""
    FONT_PATH = "simhei.ttf"

# 全局字体设置 (防止中文乱码)
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial'] 
plt.rcParams['axes.unicode_minus'] = False

def fetch_stock_name(code: str) -> str:
    """获取股票名称 (通过 Data Provider 缓存)"""
    return get_stock_name(code)

def generate_chart_bytes(code, stock_name, strategy_type, sl_price, tp1=0, tp2=0, reason="", df_override=None, ev_rating=None, sig_quality=0, bears=0):
    """
    绘制K线图，并将 AI 理由印在标题上 (支持 SL/TP 线)
    :param df_override: 可选，传入已计算好指标且切分好时间窗口的 DataFrame
    """
    # 延迟导入防止循环引用
    try:
        # 🟢 [Phase1] 统一数据层导入
        from core.data_provider import get_stock_data
    except ImportError:
        logger.error("❌ 无法导入 data_manager，绘图跳过")
        return None
    
    if df_override is not None:
        df = df_override.copy()
        # 假设传入的 df 已经包含了指标，但为了保险，检查关键列
        # 如果是切片数据，重算 EMA 会不准，所以直接信任传入的 df
        plot_df = df 
    else:
        # ======== 准备绘图数据 (优化 IV: 缩减一半 K 线展示数量) ========
        # 将原来的 250 根 / 180 根减少，使得图形更加粗壮紧凑
        display_bars = 120  # Structural Gap 大宽容度需求，120根足够涵盖一波段
        
        # 1. 获取日线数据 (默认最新)
        # 🟢 [Fix] Increase limit to 300 to ensure MTR 'trend_depth' (rolling 120) works
        df = get_stock_data(code, limit=300) 
        if df is None or df.empty: return None
        
        # 2. 调用核心计算器
        df = add_indicators(df)
        
        # 🟢 [P1] 统一通过 StrategyRegistry 注入策略计算
        from core.strategy_registry import StrategyRegistry
        try:
            strat = StrategyRegistry.get_strategy(strategy_type)
            df = strat.calculate_signals(df)
        except Exception:
            # 兼容回退：如果策略注入失败，跳过
            pass
            
        # 取最近 display_bars 天画图
        plot_df = df.tail(display_bars).copy()

    # 确保索引是 Datetime 类型
    # 🟢 [Fix] Explicit copy and .loc to silence SettingWithCopyWarning
    if 'date' in plot_df.columns:
        plot_df = plot_df.copy()
        plot_df.loc[:, 'date'] = pd.to_datetime(plot_df['date'])
        plot_df.set_index('date', inplace=True)
    elif not isinstance(plot_df.index, pd.DatetimeIndex):
        plot_df = plot_df.copy()
        plot_df.index = pd.to_datetime(plot_df.index)
    
    # 3. 样式设置
    rc_params = {'font.family': 'SimHei', 'axes.unicode_minus': False}
    if os.path.exists(FONT_PATH):
        try:
            fm.fontManager.addfont(FONT_PATH)
            prop = fm.FontProperties(fname=FONT_PATH)
            rc_params['font.family'] = prop.get_name()
        except: pass

    # 自定义 Al Brooks 风格 (红涨绿跌)
    mc = mpf.make_marketcolors(up='red', down='green', edge='inherit', wick='inherit', volume='in')
    my_style = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, rc=rc_params)

    apds = []
    # 🟢 [Phase1 Fix] EMA20 只在下方 L151 添加一次 (width=1.5)，此处不再重复添加
    buf = io.BytesIO()
    
    # --- 标题构建逻辑 ---
    # 1. 股票名字应该是“中文（编码）”样式
    # 2. 核心理由要放在k线图框的左上角区域空白处
    
    # 翻译策略名称 — P1: 使用 StrategyRegistry.get_metadata().display_name
    try:
        from core.strategy_registry import StrategyRegistry
        strat_cn = StrategyRegistry.get_metadata(strategy_type).get('display_name', '策略')
    except Exception:
        strat_cn = '策略'
    
    # 标题极简：中文名（代码）
    final_title = f"{stock_name}（{code}）"
    final_title = strip_emoji(final_title)
    
    # 1. 均线 (只保留 EMA20)
    if 'ema20' in plot_df.columns:
        apds.append(mpf.make_addplot(plot_df['ema20'], color='orange', width=1.5)) # label在mpf中不直接支持，需后续手动加假legend
    
    # 🟢 [V31.0] Geometric Bear Trendline (True Physical Line)
    if 'geometric_trendline' in plot_df.columns:
        # Plot only where it exists
        geo_line = plot_df['geometric_trendline']
        apds.append(mpf.make_addplot(geo_line, color='gray', width=1.5, linestyle='--', alpha=0.8))

    # 2. 标注波段点 (Swing High/Low) - 仅作为结构参考，调小透明度
    if 'is_sw_h_geometric' in plot_df.columns:
        # Use boolean mask to get prices
        sw_h_marks = plot_df['high'].where(plot_df['is_sw_h_geometric']).fillna(np.nan)
        if not sw_h_marks.isna().all():
            apds.append(mpf.make_addplot(sw_h_marks, type='scatter', marker='v', markersize=30, color='blue', alpha=0.6))

    # 3. 标注最终买入信号 (Signal Bar)
    if 'signal_mtr' in plot_df.columns and "MTR" in strategy_type.upper():
        buy_marks = plot_df['low'].where(plot_df['signal_mtr']) * 0.98
        if not buy_marks.isna().all():
            apds.append(mpf.make_addplot(buy_marks, type='scatter', marker='*', markersize=150, color='red'))

    # 🟢 [V8 Bugfix] 补全 3K 在绘图上的标记支持
    if 'signal_3k' in plot_df.columns and "3K" in strategy_type.upper():
        k3_marks = plot_df['low'].where(plot_df['signal_3k']) * 0.98
        if not k3_marks.isna().all():
            apds.append(mpf.make_addplot(k3_marks, type='scatter', marker='^', markersize=100, color='orange'))

    if 'signal_3k_gap_test' in plot_df.columns and "3K" in strategy_type.upper():
        gt_marks = plot_df['low'].where(plot_df['signal_3k_gap_test']) * 0.98
        if not gt_marks.isna().all():
            apds.append(mpf.make_addplot(gt_marks, type='scatter', marker='*', markersize=150, color='red'))

    # 🟢 [V3.0] Structural Gap 画图专线与防守底线渲染
    if 'signal_struct_gap_confirm' in plot_df.columns:
        sg_marks = plot_df['low'].where(plot_df['signal_struct_gap_confirm']) * 0.98
        # 去掉原先的红色五角星号标记，因为水平箭头已经足够明确
        # if not sg_marks.isna().all():
        #     apds.append(mpf.make_addplot(sg_marks, type='scatter', marker='*', markersize=200, color='red'))
        
        # 将被突破的百根高点以蓝色水平线渲染，展示大底防守
        if 'sl_struct_gap' in plot_df.columns:
            sg_floors = plot_df['sl_struct_gap']
            # Only plot where numerical
            sg_floors = sg_floors.where(pd.notna(sg_floors), np.nan)
            if not sg_floors.isna().all():
                apds.append(mpf.make_addplot(sg_floors, color='blue', width=2.0, linestyle='--'))

    # --- 准备横线 (SL/TP) ---
    h_lines = [sl_price]
    h_colors = ['green'] # SL
    h_styles = ['-.']
    
    if tp1 > 0:
        h_lines.append(tp1)
        h_colors.append('red') # TP1
        h_styles.append('--')
    if tp2 > 0:
        h_lines.append(tp2)
        h_colors.append('purple') # TP2
        h_styles.append(':')

    # --- 绘图执行 ---
    try:
        # 使用 returnfig=True 获取 figure 和 axes 对象
        fig, axlist = mpf.plot(
            plot_df, type='candle', style=my_style, addplot=apds,
            hlines=dict(hlines=h_lines, colors=h_colors, linestyle=h_styles, linewidths=1.0),
            volume=True, 
            title=final_title, 
            ylabel='', figsize=(11, 8), 
            returnfig=True 
        )
        
        # --- 手动添加图例 (Legend) [精简中文版] ---
        from matplotlib.lines import Line2D
        legend_elements = []
        
        # 动态添加已绘制的元素
        if 'geometric_trendline' in plot_df.columns:
            legend_elements.append(Line2D([0], [0], color='gray', linestyle='--', lw=1.5, label='趋势线'))
            
        if 'signal_mtr' in plot_df.columns and plot_df['signal_mtr'].any():
            legend_elements.append(Line2D([0], [0], marker='*', color='w', label='买点', markerfacecolor='red', markersize=12))
            
        if 'is_sw_h_geometric' in plot_df.columns and plot_df['is_sw_h_geometric'].any():
             legend_elements.append(Line2D([0], [0], marker='v', color='w', label='前高', markerfacecolor='blue', markersize=8))

        # 改到左边，并使用 bbox_to_anchor 稍微向下避开可能的新文字区
        if legend_elements:
            axlist[0].legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(0.02, 0.82), fontsize=9, framealpha=0.7, edgecolor='gray')

        # --- P1: 统一策略图表标注 (委托给策略自描述接口) ---
        try:
            from core.strategy_registry import StrategyRegistry
            strat_cls = type(StrategyRegistry.get_strategy(strategy_type))
            ax = axlist[0]
            strat_cls.annotate_chart(ax, plot_df, strategy_type,
                                      sl_price=sl_price, tp1=tp1, tp2=tp2,
                                      ev_rating=ev_rating, sig_quality=sig_quality, bears=bears)
        except Exception as e:
            logger.debug(f"Strategy annotation skipped: {e}")

        # --- 之前的左上角物理诊断、AI观点与交易日期等冗余信息已经被移除 ---
        
        # 标注价格标签
        xlim = axlist[0].get_xlim()
        label_x = xlim[1] * 0.99
        axlist[0].text(label_x, sl_price, f"SL: {sl_price:.2f}", color='green', fontsize=8, fontweight='bold', va='center', ha='right')
        if tp1 > 0: axlist[0].text(label_x, tp1, f"TP1: {tp1:.2f}", color='red', fontsize=8, va='center', ha='right')
        if tp2 > 0: axlist[0].text(label_x, tp2, f"TP2: {tp2:.2f}", color='purple', fontsize=8, va='center', ha='right')

        # === 🟢 大幅提升画质 ===
        # Discord 平台支持 25MB 巨物文件，故将原有的 dpi 大幅拉升至 300 (4K超清级)
        # 以确保拼接长图在手机和电脑端放大时 K 线极其锐利清晰
        fig.savefig(buf, dpi=300, bbox_inches='tight')
        plt.close(fig) # 释放内存
        buf.seek(0)
        return buf
        
    except Exception as e:
        logger.error(f"❌ Plot Error ({code}): {e}")
        return None

def format_mtr_alert(code, name, price, sl, tp1, tp2, ev_rating="N/A"):
    """MTR Master 极简模板 (V9.5)"""
    return f"• 🎯 {name} ({code}) [{ev_rating}] | Buy Stop: {price:.2f} | 止损: {sl:.2f} | 目标: {tp1:.2f}"

def format_3k_alert(code, name, price, sl, tp1=0):
    """3K Momentum 极简模板 (单行无图标)"""
    if tp1 == 0:
        risk = price - sl
        if risk <= 0: risk = 0.01
        tp1 = price + risk
    
    return f"• {name} ({code}) | Buy Stop: {price:.2f} | 止损: {sl:.2f} | 止盈: {tp1:.2f}"

def format_structural_alert(code, name, price, sl, tp1=0, ev_rating="N/A"):
    """Structural Gap (V3.0) 结构性缺口报警"""
    if tp1 == 0: tp1 = price * 1.05
    return f"• 🚀 {name} ({code}) [{ev_rating}] | 入场: {price:.2f} | 止损: {sl:.2f} | 建议止盈: {tp1:.2f}"

def stitch_images(image_buffers):
    """长图拼接"""
    if not image_buffers: return None
    valid_buffers = [b for b in image_buffers if b and not b.closed]
    if not valid_buffers: return None

    try:
        images = [Image.open(buf) for buf in valid_buffers]
        if not images: return None
        
        # 计算总画布大小
        widths, heights = zip(*(i.size for i in images))
        total_height = sum(heights)
        max_width = max(widths)
        
        new_im = Image.new('RGB', (max_width, total_height), (255, 255, 255))
        
        y_offset = 0
        for im in images:
            # 居中粘贴
            x_offset = int((max_width - im.size[0]) / 2)
            new_im.paste(im, (x_offset, y_offset))
            y_offset += im.size[1]
        
        out_buf = io.BytesIO()
        # 🟢【重大利好】Discord 支持单文件最大 25MB，放肆地拉满清晰画质
        new_im.save(out_buf, format='PNG') 
        out_buf.seek(0)
        return out_buf
    except Exception as e:
        logger.error(f"❌ Stitch Error: {e}")
        return None

def send_discord_message(content):
    """向 Discord 频道发送纯文本消息 (自动分段，不截断)"""
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID: 
        logger.warning("⚠️ 未配置 Discord Bot Token 或 Channel ID，跳过发送文本")
        return
    
    # 🟢 [Fix] 按行智能分段，确保所有内容都能推送到 Discord
    MAX_LEN = 1950  # Discord 单条消息上限 2000，留余量
    chunks = _split_message_by_lines(content, MAX_LEN)
    
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    proxies = {"http": None, "https": None}
    
    for idx, chunk in enumerate(chunks):
        for attempt in range(2): 
            try:
                resp = requests.post(
                    url, 
                    json={"content": chunk}, 
                    headers=headers,
                    timeout=15,
                    proxies=proxies
                )
                
                if resp.status_code in [200, 201]:
                    break  # 发送成功，跳出重试循环
                elif resp.status_code == 429:
                    # Rate limit: 等待 Discord 要求的时间
                    retry_after = resp.json().get('retry_after', 2)
                    logger.warning(f"⚠️ Discord 限速，等待 {retry_after}s...")
                    import time
                    time.sleep(retry_after)
                else:
                    logger.warning(f"⚠️ 发送详情: [{resp.status_code}] {resp.text}")
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"⚠️ 发送 Discord 文本尝试 {attempt+1} 失败，正在重试... ({e})")
                    import time
                    time.sleep(1)
                else:
                    logger.error(f"❌ 发送文本最终失败: {e}")
        
        # 多段消息之间加间隔，避免触发 rate limit
        if idx < len(chunks) - 1:
            import time
            time.sleep(0.5)


def _split_message_by_lines(content: str, max_len: int = 1950) -> list:
    """将长消息按行分割为多个不超过 max_len 的片段
    
    保证：
    1. 不在行中间截断
    2. 每个片段是完整的若干行
    3. 单行超长时强制截断该行（极端情况）
    """
    if len(content) <= max_len:
        return [content]
    
    lines = content.split('\n')
    chunks = []
    current_chunk = []
    current_len = 0
    
    for line in lines:
        line_len = len(line) + 1  # +1 for '\n'
        
        if current_len + line_len > max_len and current_chunk:
            # 当前块已满，保存并开始新块
            chunks.append('\n'.join(current_chunk))
            current_chunk = []
            current_len = 0
        
        # 单行超长的极端情况 (理论上不应该发生)
        if line_len > max_len:
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
                current_chunk = []
                current_len = 0
            chunks.append(line[:max_len])
            continue
        
        current_chunk.append(line)
        current_len += line_len
    
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks if chunks else [content[:max_len]]

def send_discord_image(img_buffer, filename="chart.png", content=""):
    """向 Discord 频道发送图片（可附带文字，无损画质最高支持 25MB）"""
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID or not img_buffer: 
        return
        
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
        # 注意: multipart/form-data 的 requests 实现会自动添加 Content-Type 和 boundary，不要手动加
    }
    
    proxies = {"http": None, "https": None}
    
    try:
        img_buffer.seek(0)
        img_content = img_buffer.read()
        
        # 构建 multipart 表单数据
        files = {
            "file": (filename, img_content, 'image/png')
        }
        data = {}
        if content:
            data['content'] = content

        for attempt in range(2):
            try:
                resp = requests.post(
                    url, 
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=30,
                    proxies=proxies
                )
                if resp.status_code in [200, 201]:
                    return
                else:
                    logger.error(f"❌ Discord 拒收图片: [{resp.status_code}] {resp.text}")
            except Exception as e:
                if attempt == 0:
                    import time
                    time.sleep(1)
                    continue
                logger.error(f"❌ 发送图片异常: {e}")
    except Exception as e:
        logger.error(f"❌ 图片处理失败: {e}")


def send_discord_images(img_buffers, filenames=None, content=""):
    """
    一条消息发送多张图片 (Discord 自动排列网格)
    
    Args:
        img_buffers: list of BytesIO objects
        filenames: list of filenames (optional, auto-generated if None)
        content: 消息文字内容
    
    Note: Discord API 限制单条消息最多 10 个附件
    """
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID or not img_buffers:
        return
    
    if filenames is None:
        filenames = [f"chart_{i}.png" for i in range(len(img_buffers))]
    
    # Discord 限制: 最多 10 个附件/消息
    MAX_PER_MSG = 10
    
    for batch_start in range(0, len(img_buffers), MAX_PER_MSG):
        batch_bufs = img_buffers[batch_start:batch_start + MAX_PER_MSG]
        batch_names = filenames[batch_start:batch_start + MAX_PER_MSG]
        
        url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        proxies = {"http": None, "https": None}
        
        files = {}
        for i, (buf, fname) in enumerate(zip(batch_bufs, batch_names)):
            buf.seek(0)
            files[f"file{i}"] = (fname, buf.read(), "image/png")
        
        data = {}
        # 只在第一批附消息文字
        if batch_start == 0 and content:
            data["content"] = content
        
        try:
            resp = requests.post(url, headers=headers, files=files, data=data,
                                proxies=proxies, timeout=60)
            if resp.status_code == 200:
                logger.info(f"✅ 多图推送成功 ({len(batch_bufs)} 张)")
            else:
                logger.error(f"❌ 多图推送失败: [{resp.status_code}] {resp.text[:200]}")
        except Exception as e:
            logger.error(f"❌ 多图推送异常: {e}")
        
        # 批次间间隔避免 rate limit
        if batch_start + MAX_PER_MSG < len(img_buffers):
            import time
            time.sleep(1)