import streamlit as st
import pandas as pd
import json
import os
import sys
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

# Add project root to path so we can import core modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.data_provider as dp
from core.calculator import add_indicators

st.set_page_config(page_title="Brooks-AI | Weekly Struct Gap Dashboard", layout="wide", page_icon="🎯")

# --- Custom Styling ---
st.markdown("""
<style>
    .reportview-container {
        background: #0E1117;
    }
    .main {
        padding: 0rem !important;
    }
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 0rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    header {
        visibility: hidden;
    }
</style>
""", unsafe_allow_html=True)

# --- Config ---
# 🟢 [P1 Opt 3] 字体 fallback：优先使用 settings 配置路径，否则在脚本同目录或项目根目录中查找
try:
    from config.settings import FONT_PATH
except ImportError:
    FONT_PATH = "simhei.ttf"

# Fallback: 如果配置路径不存在，尝试脚本所在目录和项目根目录
if not os.path.exists(FONT_PATH):
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    for _candidate in [os.path.join(_project_root, 'config', 'fonts', 'SimHei.ttf'), os.path.join(_project_root, 'SimHei.ttf'), os.path.join(_script_dir, 'SimHei.ttf')]:
        if os.path.exists(_candidate):
            FONT_PATH = _candidate
            break
    
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial'] 
plt.rcParams['axes.unicode_minus'] = False

@st.cache_data
def load_watchlist(json_path):
    if not os.path.exists(json_path):
        return None
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('signals_gap', [])

# 🟢 [P1 Opt 1] 缓存策略计算结果，避免切换股票时重复运算
@st.cache_data(ttl=600, show_spinner=False)
def _compute_signals(code):
    from core.strategies.structural_gap_strategy import StructuralGapStrategy
    df = dp.get_stock_data_weekly(code, limit=200)
    if df is None or df.empty:
        return None
    df = add_indicators(df)
    return StructuralGapStrategy().calculate_signals(df)

def draw_chart(code, name, entry, sl, tp, is_pending, ev_rating, sig_quality, bears):
    df = _compute_signals(code)
    if df is None:
        st.error(f"Failed to fetch data for {code}")
        return
        
    plot_df = df.tail(120).copy()
    
    if 'date' in plot_df.columns:
        plot_df.loc[:, 'date'] = pd.to_datetime(plot_df['date'])
        plot_df.set_index('date', inplace=True)
    elif not isinstance(plot_df.index, pd.DatetimeIndex):
        plot_df.index = pd.to_datetime(plot_df.index)
        
    # Set up mplfinance style
    rc_params = {'font.family': 'SimHei', 'axes.unicode_minus': False}
    if os.path.exists(FONT_PATH):
        try:
            fm.fontManager.addfont(FONT_PATH)
            prop = fm.FontProperties(fname=FONT_PATH)
            rc_params['font.family'] = prop.get_name()
        except: pass

    mc = mpf.make_marketcolors(up='red', down='green', edge='inherit', wick='inherit', volume='in')
    my_style = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True, rc=rc_params)

    apds = []
    if 'ema20' in plot_df.columns:
        apds.append(mpf.make_addplot(plot_df['ema20'], color='orange', width=1.5))
        
    # Blue floor line for historical
    if 'signal_struct_gap_confirm' in plot_df.columns and 'sl_struct_gap' in plot_df.columns:
        sg_floors = plot_df['sl_struct_gap'].where(pd.notna(plot_df['sl_struct_gap']), np.nan)
        if not sg_floors.isna().all():
            apds.append(mpf.make_addplot(sg_floors, color='blue', width=2.0, linestyle='--'))

    h_lines = [sl]
    h_colors = ['green']
    h_styles = ['-.']
    if tp and tp > 0:
        h_lines.append(tp)
        h_colors.append('red')
        h_styles.append('--')

    try:
        fig, axlist = mpf.plot(
            plot_df, type='candle', style=my_style, addplot=apds,
            hlines=dict(hlines=h_lines, colors=h_colors, linestyle=h_styles, linewidths=1.0),
            volume=True, 
            title=f"\n{name}（{code}）评级: {ev_rating}\n", 
            ylabel='', figsize=(18, 10), 
            tight_layout=True,
            returnfig=True 
        )
        
        ax = axlist[0]
        
        # Determine signal date
        signal_date = None
        if 'signal_struct_gap_confirm' in plot_df.columns and plot_df['signal_struct_gap_confirm'].any() and not is_pending:
            signal_date = plot_df[plot_df['signal_struct_gap_confirm']].index[-1]
        elif 'is_breakout' in plot_df.columns and plot_df['is_breakout'].any() and is_pending:
            signal_date = plot_df.index[-1]
            
        if signal_date:
            signal_price = plot_df.loc[signal_date]['low']
            idx_list = plot_df.index.tolist()
            prior_low = plot_df.loc[signal_date].get('struct_gap_prior_low', sl) if not is_pending else plot_df['low'].min()
            
            # --- Exact Match with notifier.py logic ---
            pre_signal = plot_df.loc[:signal_date]
            floor_candidates = pre_signal[pre_signal['high'] >= sl * 0.99]
            if not floor_candidates.empty:
                floor_date = floor_candidates.index[0]
            else:
                floor_date = pre_signal.index[len(pre_signal)//3]
                
            abs_diff = (pre_signal['low'] - prior_low).abs()
            if abs_diff.min() < 1e-4:
                origin_date = abs_diff.idxmin()
            else:
                origin_date = pre_signal.index[0]
                
            test_date = pre_signal.index[-2] if len(pre_signal) > 1 else pre_signal.index[0]
            
            try:
                signal_x = idx_list.index(signal_date)
                origin_x = idx_list.index(origin_date)
                floor_x = idx_list.index(floor_date)
                test_x = idx_list.index(test_date)
                
                origin_true_low = prior_low
                floor_true_high = plot_df.loc[floor_date, 'high']
                test_true_low = plot_df.loc[test_date, 'low']
            except ValueError:
                signal_x = len(plot_df) - 1
                origin_x, floor_x, test_x = signal_x - 40, signal_x - 20, signal_x - 1
                origin_true_low, floor_true_high, test_true_low = prior_low, sl, sl * 1.02

            exact_top_series = plot_df.loc[signal_date].get('struct_gap_top_exact')
            exact_floor_series = plot_df.loc[signal_date].get('struct_gap_floor_exact')
            
            final_gap_high = exact_top_series if pd.notna(exact_top_series) else test_true_low
            final_gap_low = exact_floor_series if pd.notna(exact_floor_series) else floor_true_high
            
            # Draw Gap Zone box (Original style)
            center_x = max(0, len(plot_df) // 2)
            rect_start_x = min(center_x, test_x - 5)
            rect_width = test_x - rect_start_x
            rect_height = final_gap_high - final_gap_low
            
            # Adjust Y-axis limits slightly to ensure TP is visible
            y_min, y_max = ax.get_ylim()
            if tp and tp > 0:
                y_max = max(y_max, tp * 1.05)
            y_min = min(y_min, final_gap_low * 0.95)
            ax.set_ylim(y_min, y_max)
            
            gap_rect = Rectangle(
                (rect_start_x - 0.5, final_gap_low), 
                rect_width + 1, rect_height,
                linewidth=1.2, facecolor='none', edgecolor='#2962FF', linestyle='--', alpha=0.6
            )
            ax.add_patch(gap_rect)
            
            ax.axhline(y=final_gap_low, color='#2962FF', linestyle='-', linewidth=1, alpha=0.5)
            
            label_x_mid = rect_start_x + rect_width / 2
            label_y_mid = final_gap_low + rect_height / 2
            ax.text(label_x_mid, label_y_mid,
                    "防守缺口\n(Gap Zone)", color='#2962FF', fontsize=9, fontweight='normal', ha='center', va='center',
                    bbox=dict(boxstyle='square,pad=0.2', facecolor='white', edgecolor='#2962FF', alpha=0.8))

            # Left panel
            rr_ratio = (tp - entry) / (entry - sl) if tp and entry > sl and tp > entry else 0
            # 🟢 [P2 Fix] sig_quality 为 0 时标注 (待确认)；ev_rating 用 truthiness 判断而非 pd.notna
            quality_str = f"{sig_quality:.2f}" if sig_quality > 0 else "(待确认)"
            rating_str = ev_rating if ev_rating else 'N/A'
            panel_text = f"买入点：{entry:.2f}\n" \
                         f"极限防守：{sl:.2f}\n" \
                         f"对称止盈：{tp:.2f} ({rr_ratio:.2f}R)\n" \
                         f"------------------\n" \
                         f"动能质量：{quality_str}\n" \
                         f"回调连阴：{bears} 连阴\n" \
                         f"系统评级：{rating_str}"
            
            ax.text(0.02, 0.96, panel_text, transform=ax.transAxes, fontsize=10,
                    verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.9, edgecolor='gray'))
            
            # Annotate Origin
            ax.annotate("起跳支点", 
                        xy=(origin_x + 0.5, origin_true_low), 
                        xytext=(origin_x + 6.5, origin_true_low),
                        arrowprops=dict(arrowstyle="->", color='#6A1B9A', lw=1.5, alpha=0.7),
                        fontsize=9, color='#6A1B9A', fontweight='normal', ha='left', va='center',
                        bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))

            # Arrows
            if not is_pending:
                ax.annotate("买入点 (Buy Stop)", 
                            xy=(signal_x + 0.5, entry), 
                            xytext=(signal_x + 6.5, entry),
                            arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5),
                            fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                            bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
            else:
                ax.annotate("预期买点 (待反转)", 
                            xy=(signal_x + 0.5, entry), 
                            xytext=(signal_x + 6.5, entry),
                            arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5, linestyle="--"),
                            fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                            bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
                            
            if tp and tp > 0:
                ax.axhline(y=tp, color='#D32F2F', linestyle='--', linewidth=1.2, alpha=0.6)
                ax.annotate("TP (目标)", 
                            xy=(signal_x, tp), 
                            xytext=(signal_x - 8, tp),
                            arrowprops=dict(arrowstyle="-", color='#D32F2F', alpha=0),
                            fontsize=9, color='#D32F2F', fontweight='bold', ha='right', va='center',
                            bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#D32F2F', alpha=0.7))

        ax.text(ax.get_xlim()[1]*0.99, sl, f"SL: {sl:.2f}", color='green', fontsize=8, fontweight='bold', va='center', ha='right')
        
        # INCREASED PLOT WIDTH IMPLICITLY BY REMOVING CONSTRAINTS
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)
        
    except Exception as e:
        st.error(f"Plot Error: {e}")


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(project_root, 'data', 'weekly_gap_watchlist.json')
    
    signals = load_watchlist(json_path)
    if not signals:
        st.warning("⚠️ 找不到监控数据，请先运行 `python tools/scanner_weekly_gap.py`")
        return
        
    df_sig = pd.DataFrame(signals)
    
    # --- Sidebar Filters & List ---
    st.sidebar.title("🎯 Brooks-AI 雷达")
    st.sidebar.header("🕹️ 过滤面板")
    
    all_ratings = list(df_sig['ev_rating'].unique())
    selected_ratings = st.sidebar.multiselect("筛选评级 (EV Rating)", options=all_ratings, default=all_ratings)
    
    df_filtered = df_sig[df_sig['ev_rating'].isin(selected_ratings)]
    st.sidebar.write(f"📊 当前命中数量: **{len(df_filtered)}**只")
    
    st.sidebar.subheader("📋 候选名单 (点击选择)")
    display_cols = ['code', 'name', 'ev_rating', 'rr']
    
    # Selection DataFrame in Sidebar
    event = st.sidebar.dataframe(
        df_filtered[display_cols].style.format({'rr': '{:.1f}'}), 
        use_container_width=True, 
        height=600,
        on_select="rerun",
        selection_mode="single-row"
    )
    
    selected_code = None
    if event and event.selection and event.selection.rows:
        selected_idx = event.selection.rows[0]
        selected_code = df_filtered.iloc[selected_idx]['code']
        
    if not selected_code:
        selected_code = df_filtered.iloc[0]['code'] if not df_filtered.empty else None

    # --- Main Area (Full Width) ---
    if selected_code:
        row = df_filtered[df_filtered['code'] == selected_code].iloc[0]
        
        # Chart takes up entire width now
        draw_chart(row['code'], row['name'], row['entry'], row['sl'], row['tp'], row['is_pending'], row['ev_rating'], row.get('sig_quality', 0), row.get('bears', 0))

if __name__ == "__main__":
    main()
