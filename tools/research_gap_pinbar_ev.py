# -*- coding: utf-8 -*-
"""
[Research] 突破缺口 + Pinbar(长下影线) 组合形态 EV 研究

纯离线分析，只读本地数据库，零网络请求。

研究目标：
  在全市场日线/周线历史数据上，扫描同时满足以下条件的形态：
    1. 出现突破缺口 (Low > 过去60根K线最高点, 即结构性跳空)
    2. 缺口后回调期间，出现 Pinbar (长下影线) 对缺口区域进行测试
       - 下影线必须探入/接近缺口区域 (低点距 gap_floor ≤ GAP_TEST_ATR 个 ATR)
       - 收盘在缺口上方 (缺口保持开放)
       - 收盘在 EMA20 附近
    3. 在 Pinbar 高点挂 Buy Stop Order

  分别统计两种止损方案的数学期望：
    方案 A: SL = 缺口下沿 (Gap Floor)，TP = 测量缺口对称目标
    方案 B: SL = 前期波段低点 (Prior Swing Low)，TP = 测量缺口对称目标
"""
import sys
import os
import io
import pandas as pd
import numpy as np
import logging
from collections import defaultdict

# 确保项目根目录在路径中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
except Exception:
    pass

from core.calculator import add_indicators
import core.data_provider as dp

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ==============================================================================
# 参数配置
# ==============================================================================
LOOKBACK_WINDOW = 60       # 突破判定回溯窗口 (日线≈3个月, 周线≈1.2年)
MAX_PULLBACK_BARS = 40     # 回调期最大追踪K线数
MIN_PULLBACK_BARS = 2      # 回调最少K线数

# Pinbar 判定阈值
PINBAR_LOWER_WICK_MIN = 0.40   # 下影线占比 ≥ 40%
PINBAR_CLOSE_LOC_MIN = 0.50    # 收盘在K线中位以上
PINBAR_EMA_DIST_ATR = 1.5      # 收盘价距EMA20最大距离 (ATR倍数, 放宽至1.5以涵盖更多有效测试)

# 缺口测试条件 (核心新增)
GAP_TEST_ATR = 2.0             # Pinbar低点距gap_floor最大距离 (ATR倍数, 即"接近缺口"的容差)


# ==============================================================================
# 第一步：突破缺口检测 (复用 structural_gap_strategy 核心逻辑)
# ==============================================================================
def detect_breakout_gaps(df):
    """
    识别结构性突破缺口，并为每次突破锁定关键锚点。

    返回新增的列:
      - is_breakout: 该K线是否构成突破缺口
      - gap_floor: 突破前N根K线的最高点 (阻力转支撑)
      - prior_swing_low: 突破前N根K线的最低点 (波段底)
      - bars_since_breakout: 距最近一次突破的K线数
    """
    n = LOOKBACK_WINDOW

    # 微观: HH + HL
    is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))

    # 宏观: 过去N根的最高点 (shift(2) 排除当根和前一根)
    gap_floor_raw = df['high'].rolling(min_periods=1, window=n).max().shift(2)

    # 突破: 当根最低点高于历史最高点 (加容差)
    df['is_breakout'] = is_hh_hl & (df['low'] > gap_floor_raw - 1e-3)

    # 前期波段低点
    prior_swing_low_raw = df['low'].rolling(min_periods=1, window=n).min().shift(2)

    # 锁定并向下填充 (ffill) 使回调期内可用
    df['gap_floor'] = np.where(df['is_breakout'], gap_floor_raw, np.nan)
    df['gap_floor'] = pd.Series(df['gap_floor'].values, index=df.index).ffill()

    df['prior_swing_low'] = np.where(df['is_breakout'], prior_swing_low_raw, np.nan)
    df['prior_swing_low'] = pd.Series(df['prior_swing_low'].values, index=df.index).ffill()

    # 突破K线的低点 (突破日的low, 即缺口的物理上沿)
    _breakout_low = np.where(df['is_breakout'], df['low'], np.nan)
    df['_breakout_low'] = pd.Series(_breakout_low, index=df.index).ffill()

    # 突破编号与K线计数
    df['_bo_group'] = df['is_breakout'].cumsum()
    df['_bo_bar_count'] = df.groupby('_bo_group').cumcount()

    # 回调期内的最低低点 (用于确定有效缺口上沿)
    df['_group_min_low'] = df['low'].groupby(df['_bo_group']).expanding().min().droplevel(0)

    # 缺口是否仍然完好 (回调期内最低低点未跌破 gap_floor)
    df['gap_open'] = df['_group_min_low'] > (df['gap_floor'] - 1e-3)

    return df


# ==============================================================================
# 第二步：Pinbar 缺口测试信号识别
# ==============================================================================
def detect_pinbar_signals(df):
    """
    在突破缺口后的回调期内，寻找"测试缺口"的 Pinbar:

    核心形态 (来自 Al Brooks PA 理论):
      突破缺口 -> 回调 -> Pinbar 下影线向下探测缺口区域 -> 收盘守住缺口上方
      -> 证明缺口是 Measuring Gap (测量缺口) -> Buy Stop at Pinbar High

    判定条件:
      1. 在回调窗口 [MIN_PULLBACK_BARS, MAX_PULLBACK_BARS] 内
      2. 下影线 ≥ 40% K线范围 (Pinbar 形态)
      3. 收盘在K线上半部 (close_loc ≥ 0.50, 表示多头拒绝)
      4. 【缺口测试】Pinbar 低点接近或探入缺口区域:
         - 低点距 gap_floor ≤ GAP_TEST_ATR 个 ATR
         - 或低点已进入缺口区间 (low <= gap_top, 即低于回调期前的最低低点)
      5. 【缺口存活】收盘 > gap_floor (缺口保持开放)
      6. 收盘在 EMA20 附近 (±1.5 ATR)
      7. 每次突破只取第一个有效 Pinbar (去重)

    返回新增列:
      - signal_pinbar: 是否为有效的缺口测试 Pinbar 信号
    """
    # 1. 回调窗口
    in_window = (df['_bo_bar_count'] >= MIN_PULLBACK_BARS) & \
                (df['_bo_bar_count'] <= MAX_PULLBACK_BARS) & \
                (df['_bo_group'] > 0)

    # 2 & 3. Pinbar 形态
    pinbar_shape = (df['lower_wick_pct'] >= PINBAR_LOWER_WICK_MIN) & \
                   (df['close_loc'] >= PINBAR_CLOSE_LOC_MIN)

    # 4. 【核心】缺口测试: Pinbar 低点必须接近或探入缺口区域
    # 缺口区间: [gap_floor, gap_top], 其中 gap_top 是回调期内到目前为止的最低低点
    # 条件 a: 低点在缺口区间内 (low <= 当前的有效缺口上沿)
    # 条件 b: 低点虽然还没进入缺口，但已经非常接近 (距 gap_floor 在 GAP_TEST_ATR 个 ATR 以内)
    gap_test_a = df['low'] <= df['_group_min_low'].shift(1)  # 探入了缺口上沿以下
    gap_test_b = (df['low'] - df['gap_floor']) <= (GAP_TEST_ATR * df['atr'])  # 接近缺口下沿
    gap_test = gap_test_a | gap_test_b

    # 5. 缺口存活: 收盘必须在 gap_floor 上方 (缺口没有被回填)
    gap_survives = df['close'] > df['gap_floor']

    # 6. 在均线附近 (收盘距 EMA20 在容差范围内)
    near_ema = (df['close'] - df['ema20']).abs() <= (PINBAR_EMA_DIST_ATR * df['atr'])

    # 组合所有条件
    raw_signal = in_window & pinbar_shape & gap_test & gap_survives & near_ema

    # 7. 去重: 每次突破只保留第一个有效 Pinbar
    already_found = raw_signal.groupby(df['_bo_group']).cumsum().shift(1).fillna(0) > 0
    df['signal_pinbar'] = raw_signal & ~already_found

    return df


# ==============================================================================
# 第三步：前瞻追踪引擎
# ==============================================================================
def evaluate_trade_forward(df, signal_idx, entry, sl_a, tp_a, sl_b, tp_b):
    """
    从信号日次日开始，逐日追踪 Buy Stop 订单的命运。
    同时评估方案A (SL=缺口下沿) 和方案B (SL=波段低点) 的结果。

    保守优先原则:
    - 未入场前跌破SL -> INVALIDATED (撤单)
    - 入场当日同时碰SL+TP -> LOSS
    - 持仓中先检SL再检TP

    返回 dict 包含两个方案的结果。
    """
    post = df.iloc[signal_idx + 1:]
    if post.empty:
        return None

    def _track(post_df, entry_price, sl_price, tp_price):
        """单方案追踪"""
        # 参数校验
        if entry_price <= 0 or sl_price <= 0 or tp_price <= 0:
            return {'status': 'INVALID_PARAMS'}
        if tp_price <= entry_price:
            return {'status': 'INVALID_TP'}
        if sl_price >= entry_price:
            return {'status': 'INVALID_SL'}

        triggered = False
        actual_entry = entry_price
        trigger_bar = 0

        for j in range(len(post_df)):
            bar = post_df.iloc[j]
            h, l, o = bar['high'], bar['low'], bar['open']

            if not triggered:
                # 未入场前跌破止损 -> 撤单
                if l < sl_price:
                    return {'status': 'INVALIDATED', 'bars': j}

                # 检查是否触发入场
                if h >= entry_price:
                    triggered = True
                    actual_entry = max(entry_price, o)  # 跳空高开
                    trigger_bar = j

                    # 入场当日双杀 (保守按LOSS)
                    if l <= sl_price:
                        return {
                            'status': 'LOSS', 'bars': 0,
                            'entry_price': actual_entry, 'exit_price': sl_price,
                            'pnl_pct': (sl_price - actual_entry) / actual_entry * 100,
                            'r_multiple': -1.0
                        }
                    # 入场当日即止盈
                    if h >= tp_price:
                        risk = actual_entry - sl_price
                        return {
                            'status': 'WIN', 'bars': 0,
                            'entry_price': actual_entry, 'exit_price': tp_price,
                            'pnl_pct': (tp_price - actual_entry) / actual_entry * 100,
                            'r_multiple': (tp_price - actual_entry) / risk if risk > 0 else 0
                        }
            else:
                # 已入场: 先检SL再检TP
                if l <= sl_price:
                    actual_exit = min(sl_price, o)  # 跳空低开
                    risk = actual_entry - sl_price
                    return {
                        'status': 'LOSS', 'bars': j - trigger_bar,
                        'entry_price': actual_entry, 'exit_price': actual_exit,
                        'pnl_pct': (actual_exit - actual_entry) / actual_entry * 100,
                        'r_multiple': (actual_exit - actual_entry) / risk if risk > 0 else -1.0
                    }
                if h >= tp_price:
                    risk = actual_entry - sl_price
                    return {
                        'status': 'WIN', 'bars': j - trigger_bar,
                        'entry_price': actual_entry, 'exit_price': tp_price,
                        'pnl_pct': (tp_price - actual_entry) / actual_entry * 100,
                        'r_multiple': (tp_price - actual_entry) / risk if risk > 0 else 0
                    }

        # 数据走完未出局
        if triggered:
            last_close = post_df.iloc[-1]['close']
            risk = actual_entry - sl_price
            return {
                'status': 'ACTIVE',
                'bars': len(post_df) - trigger_bar,
                'entry_price': actual_entry,
                'exit_price': last_close,
                'pnl_pct': (last_close - actual_entry) / actual_entry * 100,
                'r_multiple': (last_close - actual_entry) / risk if risk > 0 else 0
            }
        else:
            return {'status': 'NOT_TRIGGERED', 'bars': len(post_df)}

    result_a = _track(post, entry, sl_a, tp_a)
    result_b = _track(post, entry, sl_b, tp_b)

    return {'plan_a': result_a, 'plan_b': result_b}


# ==============================================================================
# 第四步：全市场扫描主控
# ==============================================================================
def run_research(timeframe='daily', stock_limit=0):
    """
    主控函数：遍历全市场，执行突破缺口+Pinbar 研究。

    Args:
        timeframe: 'daily' 或 'weekly'
        stock_limit: 限制扫描的股票数量 (0=全部)
    """
    tf_label = '日线' if timeframe == 'daily' else '周线'
    print(f"\n{'='*70}")
    print(f"  突破缺口 + Pinbar (长下影线) 组合形态 EV 研究")
    print(f"  时间维度: {tf_label} | 突破窗口: {LOOKBACK_WINDOW} 根 | 回调窗口: {MAX_PULLBACK_BARS} 根")
    print(f"  Pinbar 条件: 下影线≥{PINBAR_LOWER_WICK_MIN*100:.0f}%, 收盘位≥{PINBAR_CLOSE_LOC_MIN*100:.0f}%, 距EMA20≤{PINBAR_EMA_DIST_ATR}ATR")
    print(f"{'='*70}")

    all_codes = dp.get_stock_list()
    if not all_codes:
        print("❌ 获取股票列表失败")
        return

    if stock_limit > 0:
        all_codes = all_codes[:stock_limit]

    print(f"  待扫描: {len(all_codes)} 只股票\n")

    records = []
    total_signals = 0
    errors = 0

    for i, code in enumerate(all_codes):
        if (i + 1) % 200 == 0:
            sys.stdout.write(f"\r  ⏳ 进度: {i+1}/{len(all_codes)} | 信号: {total_signals}")
            sys.stdout.flush()

        try:
            # 获取数据
            if timeframe == 'daily':
                df = dp.get_stock_data(code, limit=1500)
            else:
                df = dp.get_stock_data_weekly(code, limit=None)

            if df is None or len(df) < LOOKBACK_WINDOW + 20:
                continue

            # 统一日期列名
            if 'trade_date' in df.columns and 'date' not in df.columns:
                df = df.rename(columns={'trade_date': 'date'})

            # 计算指标
            df = add_indicators(df)

            # 检测突破缺口
            df = detect_breakout_gaps(df)

            # 检测 Pinbar 信号
            df = detect_pinbar_signals(df)

            # 提取信号
            signals = df[df['signal_pinbar'] == True]
            if signals.empty:
                continue

            for sig_iloc_pos in range(len(df)):
                if not df.iloc[sig_iloc_pos].get('signal_pinbar', False):
                    continue

                sig_row = df.iloc[sig_iloc_pos]
                entry = sig_row['high']  # Buy Stop at Pinbar High
                gap_floor = sig_row['gap_floor']
                prior_low = sig_row['prior_swing_low']

                # 缺口有效上沿: 回调期内的最低低点 (当前K线之前)
                gap_top = df.iloc[:sig_iloc_pos]['_group_min_low'].iloc[-1] if sig_iloc_pos > 0 else sig_row['low']

                # Measured Move 计算
                gap_mid = (gap_top + gap_floor) / 2
                tp_mm = 2 * gap_mid - prior_low

                # 方案A: SL = 缺口下沿
                sl_a = gap_floor
                tp_a = tp_mm

                # 方案B: SL = 前期波段低点
                sl_b = prior_low
                tp_b = tp_mm

                # 参数合理性检查
                if pd.isna(entry) or pd.isna(sl_a) or pd.isna(tp_a) or pd.isna(sl_b):
                    continue
                if entry <= 0 or sl_a <= 0 or sl_b <= 0 or tp_a <= 0:
                    continue

                # 前瞻追踪
                result = evaluate_trade_forward(df, sig_iloc_pos, entry, sl_a, tp_a, sl_b, tp_b)
                if result is None:
                    continue

                # 提取特征
                sig_date = sig_row.get('date', '')
                if hasattr(sig_date, 'strftime'):
                    sig_date = sig_date.strftime('%Y-%m-%d')
                else:
                    sig_date = str(sig_date)

                bar_range = sig_row['high'] - sig_row['low']
                lower_wick = sig_row['lower_wick_pct'] if bar_range > 0 else 0
                close_loc = sig_row['close_loc']
                sig_quality = (sig_row['close'] - sig_row['low']) / bar_range if bar_range > 0 else 0
                dist_ema = abs(sig_row['close'] - sig_row['ema20']) / sig_row['atr'] if sig_row['atr'] > 0 else 0
                gap_size_pct = (gap_top - gap_floor) / gap_floor * 100 if gap_floor > 0 else 0
                rr_a = (tp_a - entry) / (entry - sl_a) if entry > sl_a else 0
                rr_b = (tp_b - entry) / (entry - sl_b) if entry > sl_b else 0
                pb_bars = int(sig_row.get('_bo_bar_count', 0))

                record = {
                    'code': code,
                    'sig_date': sig_date,
                    'timeframe': timeframe,
                    'entry': round(entry, 3),
                    'gap_floor': round(gap_floor, 3),
                    'prior_low': round(prior_low, 3),
                    'gap_top': round(gap_top, 3),
                    'tp_mm': round(tp_mm, 3),
                    'rr_plan_a': round(rr_a, 2),
                    'rr_plan_b': round(rr_b, 2),
                    'lower_wick_pct': round(lower_wick, 3),
                    'close_loc': round(close_loc, 3),
                    'dist_ema_atr': round(dist_ema, 3),
                    'gap_size_pct': round(gap_size_pct, 2),
                    'pb_bars': pb_bars,
                    # 方案A结果
                    'status_a': result['plan_a'].get('status', 'ERROR'),
                    'bars_a': result['plan_a'].get('bars', 0),
                    'pnl_pct_a': round(result['plan_a'].get('pnl_pct', 0), 2),
                    'r_mult_a': round(result['plan_a'].get('r_multiple', 0), 2),
                    # 方案B结果
                    'status_b': result['plan_b'].get('status', 'ERROR'),
                    'bars_b': result['plan_b'].get('bars', 0),
                    'pnl_pct_b': round(result['plan_b'].get('pnl_pct', 0), 2),
                    'r_mult_b': round(result['plan_b'].get('r_multiple', 0), 2),
                }
                records.append(record)
                total_signals += 1

        except Exception as e:
            errors += 1
            continue

    print(f"\n\n✅ 扫描完成! {tf_label}信号总计: {total_signals} 个 (异常跳过: {errors})")

    if not records:
        print("❌ 未发现任何有效信号。")
        return pd.DataFrame()

    rdf = pd.DataFrame(records)

    # ==================================================================
    # 统计分析
    # ==================================================================
    print_statistics(rdf, tf_label)

    # 导出 CSV
    csv_name = f'gap_pinbar_ev_{timeframe}.csv'
    csv_path = os.path.join(project_root, 'data', csv_name)
    rdf.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n  📁 明细数据已导出: {csv_path}")

    return rdf


def print_statistics(rdf, tf_label):
    """输出完整统计报告"""

    for plan, suffix, sl_desc in [('A', '_a', '缺口下沿'), ('B', '_b', '波段低点')]:
        status_col = f'status{suffix}'
        bars_col = f'bars{suffix}'
        pnl_col = f'pnl_pct{suffix}'
        r_col = f'r_mult{suffix}'
        rr_col = f'rr_plan{suffix}'

        print(f"\n{'='*70}")
        print(f"  方案 {plan}: SL = {sl_desc} | TP = 测量缺口对称目标")
        print(f"  时间维度: {tf_label}")
        print(f"{'='*70}")

        total = len(rdf)
        wins = (rdf[status_col] == 'WIN').sum()
        losses = (rdf[status_col] == 'LOSS').sum()
        active = (rdf[status_col] == 'ACTIVE').sum()
        invalidated = (rdf[status_col] == 'INVALIDATED').sum()
        not_triggered = (rdf[status_col] == 'NOT_TRIGGERED').sum()
        invalid_params = rdf[status_col].isin(['INVALID_PARAMS', 'INVALID_TP', 'INVALID_SL']).sum()

        print(f"  总信号: {total}")
        print(f"  参数无效过滤: {invalid_params}")
        print(f"  未入场撤单: {invalidated}")
        print(f"  未触发入场: {not_triggered}")
        print(f"  持仓中: {active}")

        closed = rdf[rdf[status_col].isin(['WIN', 'LOSS'])]
        if len(closed) == 0:
            print("  ⚠️ 无已闭环交易，无法计算胜率")
            continue

        win_rate = wins / len(closed) * 100

        # 赢单/亏单的 R 分布
        win_r = closed[closed[status_col] == 'WIN'][r_col]
        loss_r = closed[closed[status_col] == 'LOSS'][r_col]
        avg_win_r = win_r.mean() if len(win_r) > 0 else 0
        avg_loss_r = loss_r.mean() if len(loss_r) > 0 else 0
        ev = (win_rate / 100) * avg_win_r + (1 - win_rate / 100) * avg_loss_r

        # 赢家/输家持仓时间
        avg_bars_win = closed[closed[status_col] == 'WIN'][bars_col].mean() if wins > 0 else 0
        avg_bars_loss = closed[closed[status_col] == 'LOSS'][bars_col].mean() if losses > 0 else 0

        # 预设盈亏比
        avg_rr = closed[rr_col].mean()

        print(f"\n  ┌─────────────────────────────────────────┐")
        print(f"  │  已闭环: {len(closed)} 笔                          │")
        print(f"  │  胜率: {win_rate:.1f}% ({wins}W / {losses}L)             │")
        print(f"  │  赢单平均 R: +{avg_win_r:.2f}R (持仓 {avg_bars_win:.0f} 根)     │")
        print(f"  │  亏单平均 R: {avg_loss_r:.2f}R (持仓 {avg_bars_loss:.0f} 根)     │")
        print(f"  │  期望值 (EV): {ev:+.3f} R/笔                  │")
        print(f"  │  平均预设盈亏比: {avg_rr:.2f}                    │")
        print(f"  └─────────────────────────────────────────┘")

        # --- 单因子分箱分析 ---
        print(f"\n  --- 单因子分析 ---")

        def _bin_analysis(df_closed, col, name, bins, labels):
            df_closed = df_closed.copy()
            df_closed['_bin'] = pd.cut(df_closed[col], bins=bins, labels=labels)
            for lbl in labels:
                subset = df_closed[df_closed['_bin'] == lbl]
                if len(subset) >= 3:
                    wr = (subset[status_col] == 'WIN').sum() / len(subset) * 100
                    avg_r = subset[r_col].mean()
                    print(f"    {name} [{lbl}]: 样本 {len(subset):>4d} | 胜率 {wr:5.1f}% | 平均R {avg_r:+.2f}")

        _bin_analysis(closed, 'lower_wick_pct', '下影线占比',
                      [0, 0.40, 0.55, 0.70, 1.0],
                      ['40-55%', '55-70%', '70-85%', '85%+'])

        _bin_analysis(closed, 'dist_ema_atr', '距EMA20(ATR)',
                      [-0.01, 0.3, 0.6, 1.0, 99],
                      ['<0.3', '0.3-0.6', '0.6-1.0', '>1.0'])

        _bin_analysis(closed, 'gap_size_pct', '缺口宽度%',
                      [-999, 5, 15, 30, 999],
                      ['<5%', '5-15%', '15-30%', '>30%'])

        _bin_analysis(closed, 'pb_bars', '回调K线数',
                      [-1, 5, 10, 20, 999],
                      ['2-5根', '6-10根', '11-20根', '20+根'])

        _bin_analysis(closed, rr_col, '预设盈亏比',
                      [-999, 0.5, 1.0, 2.0, 999],
                      ['<0.5', '0.5-1.0', '1.0-2.0', '>2.0'])

        # --- 持仓中浮动统计 ---
        active_df = rdf[rdf[status_col] == 'ACTIVE']
        if len(active_df) > 0:
            float_profit = (active_df[pnl_col] > 0).sum()
            print(f"\n  持仓中: {len(active_df)} 笔 | 浮盈 {float_profit} 笔 ({float_profit/len(active_df)*100:.0f}%) | 平均浮动 {active_df[r_col].mean():+.2f}R")


# ==============================================================================
# 主入口
# ==============================================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='突破缺口+Pinbar EV 研究')
    parser.add_argument('--tf', choices=['daily', 'weekly', 'both'], default='both',
                        help='时间维度: daily / weekly / both')
    parser.add_argument('--limit', type=int, default=0,
                        help='限制扫描股票数量 (0=全部)')
    args = parser.parse_args()

    results = {}

    if args.tf in ('daily', 'both'):
        results['daily'] = run_research('daily', args.limit)

    if args.tf in ('weekly', 'both'):
        results['weekly'] = run_research('weekly', args.limit)

    print("\n" + "=" * 70)
    print("  研究完毕!")
    print("=" * 70)
