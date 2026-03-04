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
        from tools.data_manager import get_stock_data
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
        
        # 🟢 [V30.0] 注入策略计算
        if "MTR" in strategy_type.upper():
            from core.strategies.mtr_strategy import MTRStrategy
            df = MTRStrategy().calculate_signals(df)
        elif "3K" in strategy_type.upper():
            from core.strategies.three_k_strategy import ThreeKStrategy
            df = ThreeKStrategy().calculate_signals(df)
        elif "STRUCTURAL_GAP" in strategy_type.upper():
            from core.strategies.structural_gap_strategy import StructuralGapStrategy
            df = StructuralGapStrategy().calculate_signals(df)
            
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
    if 'ema20' in plot_df.columns:
        apds.append(mpf.make_addplot(plot_df['ema20'], color='orange', width=1.0))
    buf = io.BytesIO()
    
    # --- 标题构建逻辑 ---
    # 1. 股票名字应该是“中文（编码）”样式
    # 2. 核心理由要放在k线图框的左上角区域空白处
    
    # 翻译策略名称 — 去除所有内部标识符，只保留简洁中文
    strat_upper = strategy_type.upper()
    if 'MTR' in strat_upper:
        strat_cn = 'MTR反转'
    elif '3K' in strat_upper:
        strat_cn = '3K动能'
    elif 'STRUCTURAL_GAP' in strat_upper:
        strat_cn = '结构缺口'
    else:
        strat_cn = '策略'
    
    # 标题极简：中文名（代码）
    final_title = f"{stock_name}（{code}）"
    
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

        # --- MTR 教科书式标注 (Auto-Annotation) ---
        if "MTR" in strategy_type.upper():
            try:
                ax = axlist[0]
                # 1. 定位关键点
                if 'signal_mtr' not in plot_df.columns: raise ValueError("No signal column")
                sig_mask = plot_df['signal_mtr']
                if not sig_mask.any(): raise ValueError("No signal found")
                
                signal_date = sig_mask[sig_mask].index[-1]
                signal_price = plot_df.loc[signal_date]['low']
                
                # 极值低点 (Climax Low) - 在信号前
                pre_signal = plot_df.loc[:signal_date]
                climax_date = pre_signal['low'].idxmin()
                climax_price = pre_signal.loc[climax_date]['low']
                
                # 第一腿高点 (Leg 1 Peak) - 在低点和信号之间
                if climax_date != signal_date:
                    leg1_range = plot_df.loc[climax_date:signal_date]
                    leg1_date = leg1_range['high'].idxmax()
                    leg1_price = leg1_range.loc[leg1_date]['high']
                else:
                    leg1_date = climax_date
                    leg1_price = climax_price * 1.05

                # 2. 绘制标注 (Textbook Style)
                # Stage 1: Major Trend (左侧 -> Climax)
                trend_mid_date = plot_df.index[len(plot_df.index)//4] 
                if trend_mid_date < climax_date:
                     ax.annotate("1. Bear Trend\n(趋势下跌)", 
                                xy=(climax_date, climax_price), 
                                xytext=(trend_mid_date, plot_df['high'].max()*0.95),
                                arrowprops=dict(arrowstyle="->", color='gray', linestyle='dashed'),
                                fontsize=9, color='black', fontweight='bold', ha='center',
                                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7))

                # Stage 2: Sell Climax
                ax.annotate("2. Sell Climax\n(抛售高潮)", 
                            xy=(climax_date, climax_price), 
                            xytext=(climax_date, climax_price * 0.90),
                            arrowprops=dict(arrowstyle="->", color='red'),
                            fontsize=9, color='red', ha='center', va='top')

                # Stage 3: First Leg Breakout
                ax.annotate("3. First Leg\n(有力突破)", 
                            xy=(leg1_date, leg1_price), 
                            xytext=(leg1_date, leg1_price * 1.10),
                            arrowprops=dict(arrowstyle="->", color='blue'),
                            fontsize=9, color='blue', ha='center', va='bottom')
                            
                # Stage 4: Signal (Higher Low)
                ax.annotate("4. SIGNAL (HL)\n(次低点确认)", 
                            xy=(signal_date, signal_price), 
                            xytext=(signal_date, signal_price * 0.90),
                            arrowprops=dict(arrowstyle="->", color='green', linewidth=2),
                            fontsize=10, color='green', fontweight='bold', ha='center', va='top',
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='#e6ffe6', edgecolor='green'))
                            
            except Exception as e:
                logger.warning(f"MTR Annotation Failed: {e}")

        # --- Structural Gap 极简直白标注 ---
        if "STRUCTURAL_GAP" in strategy_type.upper():
            try:
                ax = axlist[0]
                 # 1. 定位关键点
                signal_date = None
                
                if 'signal_struct_gap_confirm' in plot_df.columns and plot_df['signal_struct_gap_confirm'].any():
                    # 正常确认的信号
                    signal_date = plot_df[plot_df['signal_struct_gap_confirm']].index[-1]
                    is_pending_track = False
                elif 'is_breakout' in plot_df.columns and plot_df['is_breakout'].any() and ev_rating and '追踪' in ev_rating:
                    # 这个是还在孕育期、未确认反转的悬空状态缺口
                    signal_date = plot_df.index[-1] # 用最新一天假装成信号天
                    is_pending_track = True
                else:
                    raise ValueError("No Struct Gap Signal or Breakout found")
                signal_price = plot_df.loc[signal_date]['low']
                floor_price = plot_df.loc[signal_date]['sl_struct_gap']
                prior_low = plot_df.loc[signal_date]['struct_gap_prior_low']
                
                # --- 兼容: 原生倒求历史极值坐标点 ---
                pre_signal = plot_df.loc[:signal_date]
                # 寻找百日前高(作为缺口的边际) 对应的时间
                floor_candidates = pre_signal[pre_signal['high'] >= floor_price * 0.99]
                if not floor_candidates.empty:
                    floor_date = floor_candidates.index[0]
                else:
                    floor_date = pre_signal.index[len(pre_signal)//3]
                
                # 起点: 严格定位绝对最低价那一天 (消除 >= 错配)
                # 使用 abs 解决浮点相等判断问题
                abs_diff = (pre_signal['low'] - prior_low).abs()
                if abs_diff.min() < 1e-4:
                    origin_date = abs_diff.idxmin()
                else:
                    origin_date = pre_signal.index[0]
                
                # 回调测试极值点: 触发信号前一根K线 (倒推1根)
                test_date = pre_signal.index[-2] if len(pre_signal) > 1 else pre_signal.index[0]
                
                # 将 timestamp 转换为 K线图上的 x 坐标
                date_list = list(plot_df.index)
                try:
                    signal_x = date_list.index(signal_date)
                    origin_x = date_list.index(origin_date)
                    floor_x = date_list.index(floor_date)
                    test_x = date_list.index(test_date)
                    
                    origin_true_low = prior_low
                    floor_true_high = plot_df.loc[floor_date, 'high']
                    test_true_low = plot_df.loc[test_date, 'low']
                except ValueError:
                    signal_x = len(plot_df) - 1
                    origin_x, floor_x, test_x = signal_x - 40, signal_x - 20, signal_x - 1
                    origin_true_low, floor_true_high, test_true_low = prior_low, floor_price, floor_price * 1.02

                # 如果策略暴露了精确高度，则优先使用：突破后的第一根阴线最低点 (有效顶部) 与 起始底座 (下沿)
                exact_top_series = plot_df.loc[signal_date]['struct_gap_top_exact'] if 'struct_gap_top_exact' in plot_df.columns else None
                exact_floor_series = plot_df.loc[signal_date]['struct_gap_floor_exact'] if 'struct_gap_floor_exact' in plot_df.columns else None
                
                # 兼容旧跑图数据或未更新缓存情况
                final_gap_high = exact_top_series if pd.notna(exact_top_series) else test_true_low
                final_gap_low = exact_floor_series if pd.notna(exact_floor_series) else floor_true_high
                
                # 标注 1. 开放的缺口区域 (矩形绘制) - 需求: 内部无填充的边框
                # 矩形的左下角向左拉升到整个图片的一半 (防止遮挡右侧密集区域)
                center_x = max(0, len(plot_df) // 2)
                # 左边界强制位于中心点
                rect_start_x = min(center_x, test_x - 5)
                rect_width = test_x - rect_start_x
                rect_height = final_gap_high - final_gap_low
                
                gap_rect = Rectangle(
                    (rect_start_x - 0.5, final_gap_low), 
                    rect_width + 1, rect_height,
                    linewidth=1.2, facecolor='none', edgecolor='#2962FF', linestyle='--', alpha=0.6  # 无填充的虚线边框图案
                )
                ax.add_patch(gap_rect)
                
                # 缺口下沿警戒线
                ax.axhline(y=final_gap_low, color='#2962FF', linestyle='-', linewidth=1, alpha=0.5)
                
                # 在矩形悬空居中位置标字
                label_x_mid = rect_start_x + rect_width / 2
                label_y_mid = final_gap_low + rect_height / 2
                ax.text(label_x_mid, label_y_mid,
                        "防守缺口\n(Gap Zone)", color='#2962FF', fontsize=9, fontweight='normal', ha='center', va='center',
                        bbox=dict(boxstyle='square,pad=0.2', facecolor='white', edgecolor='#2962FF', alpha=0.8))
                
                # 标注 2. 左上角参数面板 (入场、止损、止盈、盈亏比)
                if not is_pending_track:
                    entry_price = plot_df.loc[signal_date]['entry_struct_gap'] # 提前获取 entry_price 以供面板计算
                    rr_ratio = (tp1 - entry_price) / (entry_price - final_gap_low) if tp1 > entry_price and entry_price > final_gap_low else 0
                else:
                    # Pending hasn't generated entry_struct_gap to the dataframe, calculating manually from parameter
                    entry_price = plot_df.loc[signal_date]['high'] + 0.01  # Approximation
                    rr_ratio = (tp1 - entry_price) / (entry_price - final_gap_low) if tp1 > entry_price and entry_price > final_gap_low else 0
                
                panel_text = f"买入点：{entry_price:.2f}\n" \
                             f"极限防守：{final_gap_low:.2f}\n" \
                             f"对称止盈：{tp1:.2f} ({rr_ratio:.2f}R)\n" \
                             f"------------------\n" \
                             f"动能质量：{sig_quality:.2f}\n" \
                             f"回调连阴：{bears} 连阴\n" \
                             f"系统评级：{ev_rating if ev_rating else 'N/A'}"
                
                
                # ax.transAxes 表示相对坐标, 0,1 即左上角
                ax.text(0.02, 0.96, panel_text, transform=ax.transAxes, fontsize=10,
                        verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray'))
                
                # 标注 3. 测量缺口的起点 (精确指向波段极低点) TV风格
                # 波段低点的文字移到紫色箭头右边，箭头向左边指 K 线
                ax.annotate("起跳支点", 
                            xy=(origin_x + 0.5, origin_true_low), 
                            xytext=(origin_x + 6.5, origin_true_low),
                            arrowprops=dict(arrowstyle="->", color='#6A1B9A', lw=1.5, alpha=0.7),
                            fontsize=9, color='#6A1B9A', fontweight='normal', ha='left', va='center',
                            bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
                                
                # 标注 3. 入场点 (改为向左的水平箭头，指向信号当天的价格实体，只留箭头)
                if not is_pending_track:
                    ax.annotate("买入点 (Buy Stop)", 
                                xy=(signal_x + 0.5, entry_price), 
                                xytext=(signal_x + 6.5, entry_price),
                                arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5),
                                fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                                bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
                else:
                    ax.annotate("预期买点 (待反转)", 
                                xy=(signal_x + 0.5, entry_price), 
                                xytext=(signal_x + 6.5, entry_price),
                                arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5, linestyle="--"),
                                fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                                bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
                                
                # 标注 4. 测量缺口止盈 (目标价 - 虚线与标注)
                if tp1 > 0:
                    ax.axhline(y=tp1, color='#D32F2F', linestyle='--', linewidth=1.2, alpha=0.6)
                    # 文字也放到左侧对齐
                    ax.annotate("TP (目标)", 
                                xy=(signal_x, tp1), 
                                xytext=(signal_x - 8, tp1),
                                arrowprops=dict(arrowstyle="-", color='#D32F2F', alpha=0),
                                fontsize=9, color='#D32F2F', fontweight='bold', ha='right', va='center',
                                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#D32F2F', alpha=0.7))
            except Exception as e:
                logger.warning(f"Struct Gap Annotation Failed: {e}")

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

def format_mtr_alert(code, name, price, sl, tp1, tp2):
    """MTR Master 极简模板"""
    return f"""• Buy Stop: {price:.2f} | 止损: {sl:.2f}
• 止盈: TP1 {tp1:.2f} | TP2 {tp2:.2f}"""

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
    """向 Discord 频道发送纯文本消息"""
    if not DISCORD_BOT_TOKEN or not DISCORD_CHANNEL_ID: 
        logger.warning("⚠️ 未配置 Discord Bot Token 或 Channel ID，跳过发送文本")
        return
        
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    proxies = {"http": None, "https": None}
    
    for attempt in range(2): 
        try:
            # 分段发送，Discord 文本单条上限为 2000 字符
            if len(content) > 1950:
                content = content[:1950] + "\n...(内容过长已截断)"
                
            resp = requests.post(
                url, 
                json={"content": content}, 
                headers=headers,
                timeout=15,
                proxies=proxies
            )
            
            if resp.status_code in [200, 201]:
                return
            else:
                logger.warning(f"⚠️ 发送详情: [{resp.status_code}] {resp.text}")
        except Exception as e:
            if attempt == 0:
                logger.warning(f"⚠️ 发送 Discord 文本尝试 {attempt+1} 失败，正在重试... ({e})")
                import time
                time.sleep(1)
            else:
                logger.error(f"❌ 发送文本最终失败: {e}")

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