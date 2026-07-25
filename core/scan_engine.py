# -*- coding: utf-8 -*-
"""
core/scan_engine.py — 共享扫描编排核心 (P2-heavy Phase 1)

把周线扫描器原先各自内联的「取数 + 扫描 + 多信号/生命周期/pending 语义 + 评级」
抽成单一来源, 供 scanner_weekly_gap.py 等周线脚本薄封装委托, 消除双引擎的编排层重复。

设计约束 (零行为变化优先):
- 本阶段**复用现有 dict 结构** (list[dict]) 而非引入 ScanHit dataclass, 因为:
  1) 格式化层 (_format_and_push_results) 与 JSON 消费方 (web_viewer.py:71 /
     deploy_dashboard.py:15 强依赖 weekly_gap_watchlist.json['signals_gap'] 形状)
     都按 dict 字段消费, 引入中间层会扩大改动面;
  2) 方案 v2 的 ScanHit 是理想契约, 留待 Phase 3 统一日/周引擎时再引入。
- scan_weekly_gap_signals 产出的 hit dict 字段集合与旧 scanner_weekly_gap.scan_weekly_gap
  逐字段一致 (见 §等价验证), 故 scanner 格式化层可零改动直接消费。
"""
import logging
import sys

import pandas as pd
import numpy as np

from core.calculator import add_indicators
from core.strategy_registry import StrategyRegistry
import core.data_provider as dp
from core.rating import band, clamp
from core.log_config import get_logger

logger = get_logger(__name__)


# =====================================================
# 评级字母 -> 兼容原中文 ev_rating 文本 (与旧 scanner 一致)
# =====================================================
def _letter_to_ev_text(letter: str) -> str:
    """RATING_PLAN: 字母评级 -> 兼容原中文 ev_rating 文本."""
    return {
        'A+': '🌟🌟 极品 (A+)',
        'A': '🌟 高预期 (A)',
        'B': '👍 常态 (B)',
        'C': '⚠️ 低预期 (C)',
        'D': '💀 毒性 (D)',
    }.get(letter, '👍 常态 (B)')


# 🟢 [P1] 使用策略元数据替代硬编码 STRATEGY_COLS 映射表
def _get_strategy_cols(strategy_name: str) -> dict:
    """
    从 StrategyRegistry 获取策略的列名映射 (signal/entry/sl/tp/quality/bars_since_breakout/gap_top_exact)。

    替代原先的硬编码 STRATEGY_COLS 字典，未来新增策略只需在策略类中
    声明 get_metadata()，此处自动适配。

    对于 bars_since_breakout 和 gap_top_exact 等周线扫描专用列，
    优先从 metadata 的 bars_since_breakout_column / gap_top_exact_column 读取
    (P2 消除后缀 hack)；未声明的策略回退到按策略名后缀推导。
    """
    try:
        meta = StrategyRegistry.get_metadata(strategy_name)
    except Exception as e:
        logger.debug(f"[{strategy_name}] 获取策略列映射失败: {e}")
        return {}

    tp_cols = meta.get('tp_columns', [])
    cols = {
        'signal': meta.get('signal_column', ''),
        'entry': meta.get('entry_column', ''),
        'sl': meta.get('sl_column', ''),
        'tp': tp_cols[0] if tp_cols else '',
        'quality': meta.get('score_column', ''),
    }

    # 🟢 [P2] 周线扫描专用列: 优先从 metadata 读取，消除后缀 hack
    # bars_since_breakout_column / gap_top_exact_column 由策略在 get_metadata 声明；
    # 未声明的策略回退到原后缀推导逻辑（兼容）。
    bsb = meta.get('bars_since_breakout_column', '')
    gte = meta.get('gap_top_exact_column', '')
    if bsb and gte:
        cols['bars_since_breakout'] = bsb
        cols['gap_top_exact'] = gte
    else:
        name_upper = strategy_name.upper()
        if 'PINBAR' in name_upper:
            cols['bars_since_breakout'] = 'bars_since_breakout_gp'
            cols['gap_top_exact'] = 'gap_pinbar_top_exact'
        elif 'H2' in name_upper:
            cols['bars_since_breakout'] = 'bars_since_breakout_h2'
            cols['gap_top_exact'] = 'gap_h2_top_exact'
        else:
            cols['bars_since_breakout'] = 'bars_since_breakout'
            cols['gap_top_exact'] = 'struct_gap_top_exact'

    return cols


# =====================================================
# 从本地周线数据库极速提取数据 (供扫描 + 图表复用)
# =====================================================
def fetch_weekly_data(full_code: str, weeks: int = 200) -> pd.DataFrame:
    """周线数据提取: 委托 data_provider 读本地 db (SQLite)."""
    return dp.get_stock_data_weekly(full_code, limit=weeks)


# =====================================================
# 共享: 取数 + 指标 + 策略信号 (供周线脚本去重复用, 尤其 3K)
# =====================================================
def prepare_weekly_df(full_code: str, weeks: int = 200) -> pd.DataFrame:
    """
    周线单只股票的标准预处理管线: 取数 -> 加指标 (不含策略 calculate_signals,
    因不同策略需各自实例调用). 供 scanner_weekly_3k 等去重复用.

    返回已含指标列的 DataFrame; 数据不足 (None / <60 行) 时返回原样 df
    (信号列由策略层兜底).
    """
    df = fetch_weekly_data(full_code, weeks=weeks)
    if df is None or len(df) < 60:
        return df
    df = add_indicators(df)
    return df


# =====================================================
# 主扫描逻辑 (原 scanner_weekly_gap._scan_single_code, 原样搬入)
# =====================================================
# 🟢 [P3 Opt 2] 单只股票的扫描逻辑（顶层函数，可跨进程序列化）
def scan_single_code_weekly(code: str, recent_weeks: int = 4, strategies: list = None) -> list:
    """扫描单只股票的周线缺口信号，返回命中的信号列表 (dict 结构, 与旧 scanner 一致)"""
    if strategies is None:
        strategies = ['STRATEGY_STRUCTURAL_GAP']
    results = []
    try:
        df = fetch_weekly_data(code, weeks=300)
        if df is None or len(df) < 100:
            return results

        df = add_indicators(df)

        for strat_name in strategies:
            # 🟢 [P1] 使用 _get_strategy_cols() 替代硬编码 STRATEGY_COLS
            cols = _get_strategy_cols(strat_name)
            if not cols or not cols.get('signal'):
                continue

            strategy = StrategyRegistry.get_strategy(strat_name)
            df_strat = strategy.calculate_signals(df.copy())

            # 获取当前策略的信号列
            sig_col = cols['signal']
            recent = df_strat.tail(60)
            gt_rows = recent[recent.get(sig_col, pd.Series(dtype=bool)) == True]

            # 只有 STRATEGY_STRUCTURAL_GAP 才执行 pending 逻辑
            if gt_rows.empty and strat_name == 'STRATEGY_STRUCTURAL_GAP':
                recent_breakouts = df_strat[df_strat.get('is_breakout', pd.Series(dtype=bool)) == True].tail(1)
                if not recent_breakouts.empty:
                    bo_date = recent_breakouts.index[-1]
                    idx_bo = df_strat.index.get_loc(bo_date)
                    recent_slice = df_strat.iloc[idx_bo:]

                    if recent_slice['struct_gap_open'].all() and len(recent_slice) <= strategy.MAX_PULLBACK_WINDOW:
                        bo_row = recent_breakouts.iloc[-1]

                        past_highs = df_strat['high'].iloc[max(0, idx_bo - 60):max(0, idx_bo - 1)]
                        temp_sl = past_highs.max() if not past_highs.empty else bo_row['low']

                        past_lows = df_strat['low'].iloc[max(0, idx_bo - 60):max(0, idx_bo - 1)]
                        prior_sl = past_lows.min() if not past_lows.empty else bo_row['low']

                        current_min_low = recent_slice['low'].min()
                        temp_mid = (current_min_low + temp_sl) / 2
                        temp_tp = 2 * temp_mid - prior_sl

                        q = 0  # Pending 没有确认的信号K线

                        name = dp.get_stock_name(code)
                        results.append({
                            'code': code,
                            'name': name,
                            'strategy_name': strat_name,
                            'date': bo_row['trade_date'] if 'trade_date' in bo_row else (bo_row['date'] if 'date' in bo_row else str(bo_row.name)),
                            'entry': df_strat['high'].iloc[-1] + 0.01,
                            'sl': temp_sl,
                            'tp': temp_tp,
                            'rr': round((temp_tp - df_strat['high'].iloc[-1]) / (df_strat['high'].iloc[-1] - temp_sl), 1) if df_strat['high'].iloc[-1] > temp_sl else 0,
                            'sig_quality': q,
                            'bears': sum(recent_slice['close'] < recent_slice['open']),
                            'ev_rating': '🔎 潜在缺口追踪 (尚未翻转)',
                            'is_pending': True
                        })
                continue

            for sig_date, row in gt_rows.iterrows():
                entry = row.get(cols['entry'], np.nan)
                sl = row.get(cols['sl'], np.nan)
                tp = row.get(cols['tp'], np.nan)

                # 检查此信号发出的时间距离当下有多远
                idx = df_strat.index.get_loc(sig_date)
                bars_passed_since_signal = len(df_strat) - 1 - idx

                # 【生命周期过滤 1】 检查此信号之后，缺口是否已被填补 (击穿 SL)
                if not np.isnan(sl):
                    if idx < len(df_strat) - 1:
                        post_signal_min_low = df_strat['low'].iloc[idx+1:].min()
                        if post_signal_min_low <= sl:
                            continue # 缺口已死，直接无视

                # 【生命周期过滤 2】 检查此信号之后，是否已经达到了目标位 (TP)
                if not np.isnan(tp):
                    if idx < len(df_strat) - 1:
                        post_signal_max_high = df_strat['high'].iloc[idx+1:].max()
                        if post_signal_max_high >= tp:
                            continue # 目标已达，无需再扫

                # 如果走到这里，说明这是一个【缺口仍然开放，且未达到止盈】的存活信号

                risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
                rr = round(reward / risk, 1) if risk > 0 else 0

                q = row.get(cols['quality'], 0)

                # 回调周期 (bars_since_breakout)
                pb_bars = 0
                pb_consec_bear = 0

                # 🟢 [P1] 使用 cols 字典替代硬编码列名
                pb_bars_col = cols.get('bars_since_breakout', 'bars_since_breakout')
                if pb_bars_col in df_strat.columns and not pd.isna(row.get(pb_bars_col)):
                    pb_bars = int(row[pb_bars_col])
                    if pb_bars > 0 and idx >= pb_bars:
                        pb_df = df_strat.iloc[idx - pb_bars : idx]
                        is_bear = pb_df['close'] < pb_df['open']
                        shifts = is_bear != is_bear.shift()
                        groups = shifts.cumsum()
                        bear_groups = is_bear.groupby(groups).sum()
                        pb_consec_bear = int(bear_groups.max()) if not bear_groups.empty else 0

                # 计算因为持有时间过长导致的衰减，如果拖了太久还没走出来，也要扣分
                time_decay_penalty = 0
                if bars_passed_since_signal > 10:
                    time_decay_penalty = -2
                elif bars_passed_since_signal > 5:
                    time_decay_penalty = -1

                # 🟢 缺口宽度计算
                # 🟢 [P1] 使用 cols 字典替代硬编码列名
                gap_top_col = cols.get('gap_top_exact', 'struct_gap_top_exact')
                gap_top = row.get(gap_top_col, entry)
                if pd.isna(gap_top): gap_top = entry
                gap_size_pct = round((gap_top - sl) / sl * 100, 2) if sl > 0 else 0

                # 🟢 [RATING_PLAN] 统一经 strategy.compute_rating 产出四因子评级 (删除内联重复)
                try:
                    _rating = strategy.compute_rating(df_strat)
                except Exception as _e:
                    logging.warning(f"compute_rating failed for {strat_name} {code}: {_e}")
                    _rating = None
                if _rating is not None:
                    ev_score = _rating.raw_score
                    ev_rating = _letter_to_ev_text(_rating.letter)
                    rating_dict = _rating.to_dict()
                else:
                    # 兜底: 保留原四因子逻辑 (极端路径, 不应触发)
                    ev_score = 0
                    if pb_bars <= 4:    ev_score += 2
                    elif pb_bars > 7:   ev_score -= 2
                    if gap_size_pct > 7:   ev_score += 2
                    elif gap_size_pct < 3: ev_score -= 1
                    if q > 0.8:         ev_score += 1
                    elif q < 0.5:       ev_score -= 1
                    if pb_consec_bear >= 3: ev_score -= 1
                    ev_score += time_decay_penalty
                    ev_rating = _letter_to_ev_text(band(clamp(50 + 10 * ev_score)))
                    rating_dict = None

                name = dp.get_stock_name(code)
                results.append({
                    'code': code,
                    'name': name,
                    'strategy_name': strat_name,
                    'date': row['trade_date'] if 'trade_date' in row else (row['date'] if 'date' in row else str(row.name)),
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'rr': rr,
                    'sig_quality': q,
                    'bears': pb_consec_bear,
                    'pb_bars': pb_bars,
                    'gap_size_pct': gap_size_pct,
                    'ev_score': ev_score,
                    'ev_rating': ev_rating,
                    'rating': rating_dict,
                    'is_pending': False,
                    'bars_passed': bars_passed_since_signal
                })

    except Exception as e:
        logger.debug(f"扫描 {code} 失败: {e}")

    return results


def scan_weekly_gap_signals(all_codes: list, strategies: list = None, recent_weeks: int = 4) -> dict:
    """
    🟢 [P3 Opt 2] 并行扫描全市场周线 Structural Gap 信号
    使用 ThreadPoolExecutor 进行多线程并发（因数据读取涉及 SQLite，线程比进程更安全）

    Returns:
        {'signals_gap': [...]}  — 与旧 scanner_weekly_gap.scan_weekly_gap 返回结构一致
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results_gap = []
    total = len(all_codes)
    completed = 0
    MAX_WORKERS = 4  # 线程数，可根据机器性能调整

    print(f"  🚀 启动 {MAX_WORKERS} 线程并行扫描 {total} 只股票...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(scan_single_code_weekly, code, recent_weeks, strategies): code for code in all_codes}

        for future in as_completed(futures):
            completed += 1
            if completed % 50 == 0:
                sys.stdout.write(f"\r  ⏳ 扫描进度: {completed}/{total}... 累计命中: {len(results_gap)}")
                sys.stdout.flush()

            code = futures[future]
            try:
                hits = future.result()
                for hit in hits:
                    results_gap.append(hit)
                    tag = "👀" if hit.get('is_pending') else "✨"
                    print(f"\n  {tag} 命中: {hit['code']} {hit['name']} | [{hit['ev_rating']}]")
            except Exception as e:
                logger.debug(f"获取 {code} 结果失败: {e}")

    print(f"\n  ✅ 扫描完成! 共命中 {len(results_gap)} 只")
    return {'signals_gap': results_gap}
