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
import os
import json

import pandas as pd
import numpy as np

from core.calculator import add_indicators
from core.strategy_registry import StrategyRegistry
import core.data_provider as dp
from core.rating import band, clamp, get_strategy_cuts
from core.log_config import get_logger
from tools.notifier import (
    generate_chart_bytes, send_discord_message, send_discord_images, format_push_brief,
    factor_evidence_list, factor_evidence_text, format_signal_one_line,
    strategy_priority, signal_chart_key
)

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
def _drop_incomplete_current_week(df, today=None):
    """[P1-8] 丢弃'当前未完成周'的半成品周K, 避免周中扫描产生幻影信号。

    判定以'本周一'为周边界(而非自然周五), 对假日缩短周/周五盘前扫描均鲁棒:
      - 最新周K属于'本周' 且 今天为周一~周五(本周尚未收盘) -> 是半成品, 丢弃;
      - 最新周K属于'本周' 且 今天已周六/周日(本周已收盘) -> 完整周, 保留;
      - 最新周K属于先前某完整周 -> 保留。
    边界取舍: 若本周因周五休市而周四收盘(假日缩短周), 周中扫描会丢弃该完整周K
    (仅损失1周即时性, 次周即归为先前完整周); 这是'宁丢1周、不产幻影'的安全优先。
    """
    if df is None or len(df) == 0 or 'trade_date' not in df.columns:
        return df
    try:
        last = pd.to_datetime(df['trade_date'].iloc[-1]).normalize()
        today = (today if today is not None else pd.Timestamp.now()).normalize()
        this_monday = today - pd.Timedelta(days=today.weekday())
        last_monday = last - pd.Timedelta(days=last.weekday())
        if last_monday == this_monday and today.weekday() < 5:
            # 最新周K属于本周 且 本周尚未收盘 -> 半成品, 丢弃
            return df.iloc[:-1]
    except Exception:
        pass
    return df


def fetch_weekly_data(full_code: str, weeks: int = 200) -> pd.DataFrame:
    """周线数据提取: 委托 data_provider 读本地 db (SQLite). [S1] 自动丢弃未完成周."""
    df = dp.get_stock_data_weekly(full_code, limit=weeks)
    if df is None:
        return None
    return _drop_incomplete_current_week(df)


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
def scan_single_code_weekly(code: str, signal_lookback: int = 60, strategies: list = None) -> list:
    """扫描单只股票的周线缺口信号，返回命中的信号列表 (dict 结构, 与旧 scanner 一致)

    signal_lookback: 缺口家族需回看较宽窗口(默认60周)以捕捉仍存活的 pending/active 信号,
    这与 3K 的紧 recent_weeks 语义不同 — 故独立命名, 不再复用 recent_weeks(P1-9 修正:
    原先 recent_weeks 参数被本函数静默忽略, 与 scan_weekly_3k_signals 行为不一致)。
    """
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
                    _rating = strategy.compute_rating(df_strat, timeframe='weekly')
                except Exception as _e:
                    logging.warning(f"compute_rating failed for {strat_name} {code}: {_e}")
                    _rating = None
                if _rating is not None:
                    ev_score = _rating.raw_score
                    ev_rating = ''  # [P0-5] 不再渲染经回测证明为噪声的假字母; rating_dict 仍保留供因子证据
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
                    ev_rating = ''  # [P0-5]
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
        # 缺口家族使用自身宽窗口(默认60周)捕捉存活信号, 不套用 3K 的紧 recent_weeks (P1-9)
        futures = {executor.submit(scan_single_code_weekly, code, strategies=strategies): code for code in all_codes}

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
                    # [P0-5] 去字母化: 控制台命中打印不再显示经回测证明为噪声的 A+/A/B/C/D 假字母
                    _ev = factor_evidence_text(hit.get('rating'))
                    _tail = f" | {_ev}" if _ev else ""
                    print(f"\n  {tag} 命中: {hit['code']} {hit['name']}{_tail}")
            except Exception as e:
                logger.debug(f"获取 {code} 结果失败: {e}")

    print(f"\n  ✅ 扫描完成! 共命中 {len(results_gap)} 只")
    return {'signals_gap': results_gap}


# =====================================================
# 周线 3K 扫描 (原 scanner_weekly_3k.scan_weekly_3k, 原样搬入)
# =====================================================
def scan_weekly_3k_signals(all_codes: list, recent_weeks: int = 4) -> dict:
    """
    周线 3K 策略扫描 (原 scanner_weekly_3k.scan_weekly_3k, 原样搬入 scan_engine).

    Returns:
        {'signals_3k': [...], 'signals_gap_test': [...]}  — 与旧 scanner 结构一致
    """
    from core.strategies.three_k_strategy import ThreeKStrategy
    strategy = ThreeKStrategy()
    results_3k = []
    results_gt = []

    for i, code in enumerate(all_codes):
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{len(all_codes)}...")

        try:
            df = prepare_weekly_df(code, weeks=200)
            if df is None or len(df) < 60:
                continue

            df = strategy.calculate_signals(df)

            # 检查最近 N 周是否有信号
            recent = df.tail(recent_weeks)

            # 3K 信号
            sig_rows = recent[recent['signal_3k'] == True]
            for _, row in sig_rows.iterrows():
                results_3k.append({
                    'code': code,
                    'name': dp.get_stock_name(code),
                    'date': row['trade_date'] if 'trade_date' in row else (row['date'] if 'date' in row else str(row.name)),
                    'close': row['close'],
                    'sl': row.get('sl_3k', np.nan),
                })

            # 缺口测试确认
            gt_rows = recent[recent.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True]
            for _, row in gt_rows.iterrows():
                entry = row.get('entry_3k_gap_test', np.nan)
                sl = row.get('sl_3k_gap_test', np.nan)
                tp = row.get('tp_3k_gap_test', np.nan)
                risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
                rr = round(reward / risk, 1) if risk > 0 else 0

                # 🟢 [R-A] 接入策略层 compute_rating, 与 gap 扫描器统一评级
                try:
                    _rating = strategy.compute_rating(df, timeframe='weekly')
                except Exception as _e:
                    logging.warning(f"compute_rating failed for STRATEGY_3K {code}: {_e}")
                    _rating = None
                if _rating is not None:
                    ev_score = _rating.raw_score
                    ev_rating = ''  # [P0-5] 不再渲染经回测证明为噪声的假字母; rating_dict 仍保留供因子证据
                    rating_dict = _rating.to_dict()
                else:
                    ev_score = 0
                    ev_rating = ''
                    rating_dict = None

                results_gt.append({
                    'code': code,
                    'name': dp.get_stock_name(code),
                    'date': row['trade_date'] if 'trade_date' in row else (row['date'] if 'date' in row else str(row.name)),
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'rr': rr,
                    'ev_score': ev_score,
                    'ev_rating': ev_rating,
                    'rating': rating_dict,
                })

        except Exception as e:
            continue

    return {'signals_3k': results_3k, 'signals_gap_test': results_gt}


# =====================================================
# 周线 gap 家族格式化 / 推送 (原 scanner_weekly_gap._format_and_push_results)
# =====================================================
def format_push_weekly_gap(results, total_stocks=0):
    """控制台输出 + JSON/MD 导出 + Discord 推送 + Signal Tracker 归档 (周线 gap 家族)."""

    # === 控制台输出 ===
    sig_gap = results['signals_gap']

    # 🟢 根据实际命中信号动态生成策略标签（不再硬编码单一策略名）
    _active_strats = sorted(set(s.get('strategy_name', '') for s in sig_gap if s.get('strategy_name')))
    _strat_labels = []
    for _sn in _active_strats:
        try:
            _dn = StrategyRegistry.get_metadata(_sn).get('display_name', _sn.replace('STRATEGY_', ''))
        except Exception as e:
            logger.debug(f"获取策略 {_sn} display_name 失败: {e}")
            _dn = _sn.replace('STRATEGY_', '')
        _strat_labels.append(_dn)
    strat_display = ' / '.join(_strat_labels) if _strat_labels else '缺口策略'

    print("\n" + "=" * 80)
    print(f"  周线 {strat_display} 信号汇总")
    print("=" * 80)

    # 去字母化: 不再按字母 emoji 分档冷落信号; 全量保留 (is_pending 仅作状态标注, 不丢信号)
    results['signals_gap'] = sig_gap

    # 📥 Signal Tracker: 归档周线信号 (仅确认信号, 不含 pending)
    try:
        from core.signal_tracker import archive_signal, init_signal_archive
        init_signal_archive()
        confirmed = [s for s in sig_gap if not s.get('is_pending')]
        for s in confirmed:
            sig_date = s['date'].strftime('%Y-%m-%d') if hasattr(s['date'], 'strftime') else str(s['date'])
            archive_signal(
                code=s['code'], strategy=s.get('strategy_name', 'STRUCTURAL_GAP'), timeframe='weekly',
                entry=s['entry'], sl=s['sl'], tp=s['tp'] if not np.isnan(s['tp']) else 0,
                ev_rating='',  # [P0-5] 不再写经回测证明为噪声的假字母
                evidence=factor_evidence_text(s.get('rating')),
                signal_date=sig_date,
                signal_bar_idx=s.get('signal_bar_idx', -1),
                ev_score=s.get('ev_score', 0), rr=s.get('rr', 0), name=s.get('name', ''),
                gap_size_pct=s.get('gap_size_pct', 0), pb_bars=s.get('pb_bars', 0),
                sig_quality=s.get('sig_quality', 0)
            )
        if confirmed:
            logger.info(f"📥 {len(confirmed)} 个周线信号已归档到 Signal Tracker")
    except Exception as e:
        logger.warning(f"周线信号归档失败: {e}")

    print(f"\n📌 重点埋伏区 - 周线结构跨越+神级洗盘已确认 (共 {len(sig_gap)} 个):")
    print("-" * 60)

    # 去字母化: 控制台按策略分组打印一行精简 (与 Discord 一致)
    def _print_sg_console(group, title):
        if group:
            print(f"\n[{title}] ({len(group)}只):")
            for s in group:
                print(f"  {format_signal_one_line(s['code'], s['name'], s.get('strategy_name', ''), s, timeframe='weekly')}")

    # 按策略优先级分组打印
    from collections import OrderedDict
    from core.strategy_registry import StrategyRegistry
    _grp = OrderedDict()
    for s in sig_gap:
        _sn = StrategyRegistry.get_metadata(s.get('strategy_name', '')).get('display_name') or s.get('strategy_name', '')
        _grp.setdefault(_sn, []).append(s)
    _ranked = sorted(_grp.items(),
                     key=lambda kv: strategy_priority(kv[1][0].get('strategy_name', ''), 'weekly'),
                     reverse=True)
    for _sn, _ss in _ranked:
        _print_sg_console(_ss, _sn)

    print("\n" + "=" * 80)

    # === 导出报告与数据 ===
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. 导出 JSON 数据
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, 'weekly_gap_watchlist.json')
    def default_serializer(obj):
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()
        return str(obj)

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=4, ensure_ascii=False, default=default_serializer)
    print(f"✅ 生成监控名单: {json_path}")

    # 2. 导出 Markdown 报告
    lab_dir = os.path.join(project_root, 'strategy_lab')
    os.makedirs(lab_dir, exist_ok=True)
    md_path = os.path.join(lab_dir, 'weekly_struct_gap_plan.md')

    report_md = f"# 下周交易埋伏计划 (基于周线 {strat_display})\n\n"
    report_md += f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    if total_stocks > 0:
        report_md += f"**扫描范围**: 全市场 {total_stocks} 只个股\n\n"

    report_md += f"## 🎯 神级波段埋伏区 (待挂单)\n\n"
    if not sig_gap:
        report_md += "本周无符合条件的突破标的。\n\n"
    else:
        report_md += "| 代码 | 名称 | 策略 | 信号特征 | 下周买点 (Buy Stop) | 绝对止损 (Gap Floor) | 测距翻倍 (TP) | 盈亏比预估 |\n"
        report_md += "|:---:|:---|:---|:---|:---|:---|:---|:---|\n"
        for s in sig_gap:
            tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
            rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
            date_str = s['date'].strftime('%Y-%m-%d') if hasattr(s['date'], 'strftime') else str(s['date'])
            strat_short = s.get('strategy_name', '').replace('STRATEGY_', '')
            ev = factor_evidence_text(s.get('rating'))
            report_md += f"| `{s['code']}` | **{s['name']}** | {strat_short} | {ev} | **>={s['entry']:.2f}** | *{s['sl']:.2f}* | {tp_str} | {rr_str} |\n"

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report_md)
    print(f"✅ 生成本周末复盘报告: {md_path}")

    # === Discord 图文推送 ===
    print("\n🚀 正在生成 Discord 图文全量推送...")

    msg = f"🔔 **【周线 {strat_display} 雷达扫描完成】**\n"
    msg += f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d')}\n"
    if total_stocks > 0:
        msg += f"池子: 全市场 {total_stocks} 只个股\n"
    msg += f"----------------------\n"
    msg += f"🎯 **命中结果**: 共 {len(sig_gap)} 只\n"

    if not sig_gap:
        msg += f"\n💤 【周线/{strat_display}】，本次未发现信号"
    else:
        # 去字母化: 按策略分组 + 每条一行精简 (全推送, 因子证据, 不按字母筛)
        from collections import OrderedDict
        from core.strategy_registry import StrategyRegistry
        grp = OrderedDict()
        for s in sig_gap:
            sn = StrategyRegistry.get_metadata(s.get('strategy_name', '')).get('display_name') or s.get('strategy_name', '')
            grp.setdefault(sn, []).append(s)
        ranked = sorted(grp.items(),
                        key=lambda kv: strategy_priority(kv[1][0].get('strategy_name', ''), 'weekly'),
                        reverse=True)
        for sn, ss in ranked:
            msg += f"\n📌 **{sn} ({len(ss)}只)**:\n"
            for s in ss:
                msg += format_signal_one_line(s['code'], s['name'], s.get('strategy_name', ''), s, timeframe='weekly') + "\n"
        msg += f"\n\n📊 信号 K线图即将推送..."

    send_discord_message(msg)

    if not sig_gap:
        print("✅ Discord 空结果推送成功！")
    else:
        # 去字母化: 按策略优先级+因子证据排序取 Top-N (高优先策略恒出图, 去掉仅A+门禁)
        from config import settings
        max_charts = settings.MAX_CHARTS_PER_RUN
        top_sigs = sorted(sig_gap, key=lambda x: signal_chart_key(x, 'weekly'), reverse=True)[:max_charts]
        chart_label = "📊 **信号 K线图 (按策略优先级)**"

        if top_sigs:
            print(f"\n🎨 为 {len(top_sigs)} 只标的生成图表...")
            chart_bufs = []
            chart_names = []

            for s in top_sigs:
                try:
                    df = fetch_weekly_data(s['code'], weeks=300)
                    if df is not None:
                        df = add_indicators(df)
                        strat = StrategyRegistry.get_strategy(s.get('strategy_name', 'STRATEGY_STRUCTURAL_GAP'))
                        df = strat.calculate_signals(df)
                        buf = generate_chart_bytes(
                            code=s['code'], stock_name=s['name'],
                            strategy_type=s.get('strategy_name', 'STRATEGY_STRUCTURAL_GAP'),
                            sl_price=s['sl'], tp1=s['tp'] if not np.isnan(s['tp']) else 0,
                            reason="周线大底确认", df_override=df,
                            ev_rating=None,
                            sig_quality=s.get('sig_quality'), bears=s.get('bears'),
                            entry=s.get('entry', 0), rating=s.get('rating'), timeframe='周K'
                        )
                        if buf:
                            chart_bufs.append(buf)
                            chart_names.append(f"{s['code']}.png")
                            print(f"  ✅ {s['code']} {s['name']}")
                except Exception as e:
                    logger.warning(f"绘图失败 {s['code']}: {e}")

            if chart_bufs:
                BATCH_SIZE = 5
                for batch_start in range(0, len(chart_bufs), BATCH_SIZE):
                    batch_bufs = chart_bufs[batch_start:batch_start + BATCH_SIZE]
                    batch_names = chart_names[batch_start:batch_start + BATCH_SIZE]
                    batch_msg = f"{chart_label} ({batch_start+1}-{batch_start+len(batch_bufs)}/{len(chart_bufs)})"
                    send_discord_images(batch_bufs, batch_names, content=batch_msg)
                print(f"✅ {len(chart_bufs)} 张图表分 {(len(chart_bufs)-1)//BATCH_SIZE+1} 批推送完成！")


# =====================================================
# 周线 3K 格式化 / 推送 (原 scanner_weekly_3k.main 格式化段; 不归档 signal_tracker)
# =====================================================
def format_push_weekly_3k(results: dict, total_stocks: int = 0, weeks: int = 4):
    """控制台输出 + JSON/MD 导出 + Discord 推送 (周线 3K).

    注意: 3K 信号一并归档 signal_tracker (P0-2.6: 消除独立状态源, 统一复盘闭环).
    """
    from core.strategies.three_k_strategy import ThreeKStrategy
    pd_ts = pd.Timestamp.now()

    # === 控制台输出 ===
    print("\n" + "=" * 80)
    print(f"  周线 3K 信号汇总")
    print("=" * 80)

    sig_3k = results['signals_3k']
    sig_gt = results['signals_gap_test']

    print(f"\n📌 重点观察区 - 周线 3K 形态刚确认 (共 {len(sig_3k)} 个):")
    print("-" * 60)
    for s in sig_3k:
        print(f"{s['code']:>12s} {s['name']:<6s} 周线日期:{s['date']}  最新收盘:{s['close']:.2f}  破位参考(SL):{s['sl']:.2f}")

    print(f"\n📌 下周埋伏区 - 周线缺口测试已确认，待触发 Buy Stop (共 {len(sig_gt)} 个):")
    print("-" * 60)
    for s in sig_gt:
        tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
        rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
        print(f"{s['code']:>12s} {s['name']:<6s} 周线日期:{s['date']}  下周买入(Buy Stop):>={s['entry']:.2f}  防守(SL):{s['sl']:.2f}  目标:{tp_str}  R:R={rr_str}")

    print("\n" + "=" * 80)

    # [P0-2.6] 周线 3K 信号归档进 SignalTracker (消除独立状态源 weekly_watchlist.json)
    try:
        from core.signal_tracker import archive_signal, init_signal_archive
        init_signal_archive()
        _archived = 0
        for s in sig_gt + sig_3k:
            _sdate = s['date'].strftime('%Y-%m-%d') if hasattr(s['date'], 'strftime') else str(s['date'])
            _tp = s.get('tp', 0)
            if isinstance(_tp, float) and np.isnan(_tp):
                _tp = 0
            archive_signal(
                code=s['code'], strategy='STRATEGY_3K', timeframe='weekly',
                entry=s.get('entry', 0), sl=s.get('sl', 0), tp=_tp,
                signal_date=_sdate, name=s.get('name', ''),
                phase='缺口确认' if s in sig_gt else '新雏形',
                signal_bar_idx=s.get('signal_bar_idx', -1)
            )
            _archived += 1
        if _archived:
            logger.info(f"📥 周线 3K 信号已归档 {_archived} 个到 Signal Tracker")
    except Exception as e:
        logger.warning(f"周线 3K 归档失败: {e}")

    # === 导出报告与数据 ===
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. 导出 JSON 数据 (用于盘中监控)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_path = os.path.join(data_dir, 'weekly_watchlist.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=4, ensure_ascii=False)
    print(f"✅ 生成监控名单: {json_path}")

    # 2. 导出 Markdown 报告 (供人工审阅)
    lab_dir = os.path.join(project_root, 'strategy_lab')
    os.makedirs(lab_dir, exist_ok=True)
    md_path = os.path.join(lab_dir, 'weekly_ambush_plan.md')

    report_md = f"# 下周交易埋伏计划 (基于周线 3K V2.3)\n\n"
    report_md += f"**生成时间**: {pd_ts.strftime('%Y-%m-%d %H:%M:%S')}\n"
    if total_stocks > 0:
        report_md += f"**扫描范围**: 全市场 {total_stocks} 只个股\n\n"

    report_md += f"## 🎯 下周重点埋伏区 (待挂单)\n\n"
    report_md += f"> [!IMPORTANT]\n> 以下标的在最近 {weeks} 周内已走出 `缺口测试确认(Gap Test) 阳线`。**一旦下周冲破测缺K线高点，即可按规则挂单追涨或平开现价买入。**\n\n"
    if not sig_gt:
        report_md += "本周无符合【缺口测试确认】的埋伏标的。\n\n"
    else:
        report_md += "| 代码 | 名称 | 周线信号日期 | 下周买点 (Buy Stop) | 止损 (Gap Floor) | 目标价 (TP) | 盈亏比预估 |\n"
        report_md += "|:---:|:---|:---|:---|:---|:---|:---|\n"
        for s in sig_gt:
            tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
            rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
            report_md += f"| `{s['code']}` | **{s['name']}** | {s['date']} | **>={s['entry']:.2f}** | *{s['sl']:.2f}* | {tp_str} | {rr_str} |\n"

    report_md += f"\n## 🔭 下周观察池 (刚出 3K 雏形)\n\n"
    report_md += f"> [!NOTE]\n> 以下标的已出现强劲的周线 3K 突破形态。尚未进行缺口测试，不建议盲目追高。**下周可重点观察它们的周线回撤（是否能守住下方参考位并在下周或下下周收出企稳阳线）。**\n\n"
    if not sig_3k:
        report_md += "本周无新出的【3K 突破】观察标的。\n\n"
    else:
        report_md += "| 代码 | 名称 | 周线 3K 日期 | 最新收盘 | 回撤防守底线 (不宜跌破) |\n"
        report_md += "|:---:|:---|:---|:---|:---|\n"
        for s in sig_3k:
            report_md += f"| `{s['code']}` | **{s['name']}** | {s['date']} | {s['close']:.2f} | *{s['sl']:.2f}* |\n"

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report_md)
    print(f"✅ 生成本周末复盘报告: {md_path}")

    # === 微信图文推送 ===
    print("\n🚀 正在生成微信图文推送...")
    chart_buffers = []
    # 优先推送 Gap Test (最多 3 个)
    for s in sig_gt[:3]:
        try:
            df = fetch_weekly_data(s['code'], weeks=200)
            if df is not None:
                df = add_indicators(df)
                df = ThreeKStrategy().calculate_signals(df)
                buf = generate_chart_bytes(
                    code=s['code'], stock_name=s['name'], strategy_type='STRATEGY_3K',
                    sl_price=s['sl'], tp1=s['tp'] if not np.isnan(s['tp']) else 0,
                    reason="周线缺口测试确认，待Buy Stop", df_override=df, timeframe='周K',
                    draw_panel=False
                )
                if buf: chart_buffers.append(buf)
        except Exception as e:
            logger.warning(f"绘图失败 {s['code']}: {e}")

    # 补充推送新 3K (补齐到最多 5 个)
    remain = 5 - len(chart_buffers)
    if remain > 0:
        for s in sig_3k[:remain]:
            try:
                df = fetch_weekly_data(s['code'], weeks=200)
                if df is not None:
                    df = add_indicators(df)
                    df = ThreeKStrategy().calculate_signals(df)
                    buf = generate_chart_bytes(
                        code=s['code'], stock_name=s['name'], strategy_type='STRATEGY_3K',
                        sl_price=s['sl'], reason="周线刚出3K雏形，重点观察回抽", df_override=df, timeframe='周K',
                        draw_panel=False
                    )
                    if buf: chart_buffers.append(buf)
            except Exception as e:
                logger.warning(f"绘图失败 {s['code']}: {e}")

    # 推送到 Discord
    if chart_buffers:
        unified = []
        for s in sig_gt:
            unified.append({'code': s['code'], 'strategy_name': 'STRATEGY_3K', 'phase': '缺口确认'})
        for s in sig_3k:
            unified.append({'code': s['code'], 'strategy_name': 'STRATEGY_3K', 'phase': '新雏形'})
        msg = "🔔 【周线 3K 雷达扫描完成】\n"
        msg += f"时间: {pd_ts.strftime('%Y-%m-%d')}\n"
        if total_stocks > 0:
            msg += f"池子: 全市场 {total_stocks} 只个股\n"
        msg += f"----------------------\n"
        msg += format_push_brief(unified, group_key='phase', order=['缺口确认', '新雏形'])

        filenames = [f"weekly_3k_{i}.png" for i in range(len(chart_buffers))]
        send_discord_images(chart_buffers, filenames, content=msg)
        print("✅ Discord 图文推送成功！")
    else:
        if not sig_gt and not sig_3k:
            send_discord_message("💤 【周线/3K】，本次未发现信号")
            print("✅ Discord 空结果推送成功！")
        else:
            print("⚠️ 没有成功生成任何图表。")


# =====================================================
# Phase 3 统一编排入口: 周线单引擎 (消除 hunter 两处周线委托重复 + 独立 3K 脚本)
# =====================================================
def run_weekly_scan(active_strategies, weeks=4, limit=0, all_codes=None):
    """
    周线统一编排入口 (Phase 3 入口/编排层单引擎; 注: 3K 硬编码例外刻意保留, core->tools 倒置见 :30 为已知债): 取列表 -> 按家族路由 -> 扫描 + 格式化/推送.

    - 含 STRATEGY_3K -> 走 3K 路径 (不归档 signal_tracker, 产物 weekly_watchlist.json / weekly_ambush_plan.md)
    - 否则 -> 走 gap 家族路径 (STRUCTURAL_GAP/PINBAR/H2, 含 Signal Tracker 归档)
    - 日线 _scan_market 路径不受影响 (本函数仅服务周线)

    返回: 无 (扫描 + 格式化 + 推送 + 归档 全部在此完成, 与旧 scanner 主流程行为一致)
    """
    if all_codes is None:
        all_codes = dp.get_stock_list()
    if not all_codes:
        print("❌ 获取股票列表失败")
        return
    if limit > 0:
        all_codes = all_codes[:limit]

    # ⚠️ 周线数据表存在性提示 (沿用旧 scanner 的 UX 守护)
    try:
        from config.settings import DB_PATH
        import sqlite3
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='weekly_bars'")
            if c.fetchone()[0] == 0:
                print(f"\n❌ 周线数据库表 weekly_bars 不存在 ({DB_PATH})")
                print("👉 请先执行此命令同步数据: python tools/update_weekly_db.py\n")
                return
    except Exception:
        pass

    active = [s.upper() for s in (active_strategies or [])]
    # [P1-1 修复] 周线 3K 与缺口家族改为并行路由, 不再互斥:
    # 原 `if STRATEGY_3K in active` 的独占分支导致"选了 3K 就不扫 gap",
    # 既漏信号又违背用户多选意图。现各自独立判定、按需都跑。
    WEEKLY_GAP_STRATS = {
        'STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2',
    }
    do_3k = 'STRATEGY_3K' in active
    do_gap = bool(set(active) & WEEKLY_GAP_STRATS)
    if not (do_3k or do_gap):
        print(f"\n⚠️ 周线扫描: 未识别到任何周线策略 ({', '.join(active) or '空'})。"
              f"支持: STRATEGY_3K / {', '.join(sorted(WEEKLY_GAP_STRATS))}")
        return
    if do_gap:
        gap_strats = [s for s in active_strategies if s.upper() in WEEKLY_GAP_STRATS]
        print(f"\n🌙 周线缺口扫描: {len(all_codes)} 只股票, 检查最近 {weeks} 周, "
              f"策略: {', '.join(gap_strats)}")
        gap_results = scan_weekly_gap_signals(all_codes, strategies=gap_strats, recent_weeks=weeks)
        format_push_weekly_gap(gap_results, total_stocks=len(all_codes))
    if do_3k:
        print(f"\n🌙 周线 3K 扫描: {len(all_codes)} 只股票, 检查最近 {weeks} 周")
        k3_results = scan_weekly_3k_signals(all_codes, recent_weeks=weeks)
        format_push_weekly_3k(k3_results, total_stocks=len(all_codes), weeks=weeks)
