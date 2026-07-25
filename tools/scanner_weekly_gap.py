# -*- coding: utf-8 -*-
"""
周线 结构性测量缺口策略独立扫描器 (scanner_weekly_gap.py)

独立于日线系统，直接调用 Baostock frequency='w' 获取周线数据。
不修改 data_provider / database / scanner 等现有核心模块。

# 用法:
#   1. 在周末手动运行网络同步：python tools/update_weekly_db.py
#   2. 运行纯离线本地扫描：python tools/scanner_weekly_gap.py [--limit N] [--weeks D]
#
# 会自动在 strategy_lab 目录下生成每周埋伏计划报告 (weekly_struct_gap_plan.md)
# 并在 data 目录下生成监控文件 (weekly_gap_watchlist.json)
"""
import sys, os, io, argparse, logging, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.paths import ensure_importable
ensure_importable()

import pandas as pd
import numpy as np
import baostock as bs

from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
from core.strategy_registry import StrategyRegistry
import core.data_provider as dp
from core.rating import band, clamp

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
    根据 strategy_name 后缀匹配来补充 (这些列不属于 metadata 标准字段)。
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

    # 🟢 [P1] 周线扫描专用列: bars_since_breakout / gap_top_exact
    # 这些列名含策略后缀，无法从标准 metadata 推导，根据策略名后缀匹配
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
from tools.notifier import generate_chart_bytes, stitch_images, send_discord_image, send_discord_message, send_discord_images, format_push_brief
from core.log_config import get_logger

logger = get_logger(__name__)

# =====================================================
# 从本地周线数据库极速提取数据
# =====================================================
def fetch_weekly_data(full_code: str, weeks: int = 200) -> pd.DataFrame:
    return dp.get_stock_data_weekly(full_code, limit=weeks)


# =====================================================
# 主扫描逻辑
# =====================================================
# 🟢 [P3 Opt 2] 单只股票的扫描逻辑（顶层函数，可跨进程序列化）
def _scan_single_code(code: str, recent_weeks: int = 4, strategies: list = None) -> list:
    """扫描单只股票的周线缺口信号，返回命中的信号列表"""
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


def scan_weekly_gap(all_codes: list, strategies: list = None, recent_weeks: int = 4) -> dict:
    """
    🟢 [P3 Opt 2] 并行扫描全市场周线 Structural Gap 信号
    使用 ThreadPoolExecutor 进行多线程并发（因数据读取涉及 SQLite，线程比进程更安全）
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    results_gap = []
    total = len(all_codes)
    completed = 0
    MAX_WORKERS = 4  # 线程数，可根据机器性能调整
    
    print(f"  🚀 启动 {MAX_WORKERS} 线程并行扫描 {total} 只股票...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_scan_single_code, code, recent_weeks, strategies): code for code in all_codes}
        
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


def _format_and_push_results(results, total_stocks=0):
    """控制台输出 + JSON/MD 导出 + Discord 推送 (可被 hunter.py 调用)"""
    
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
    
    sg_best = [s for s in sig_gap if '🌟' in s.get('ev_rating', '') and not s.get('is_pending')]
    sg_good = [s for s in sig_gap if '👍' in s.get('ev_rating', '') and not s.get('is_pending')]
    sg_warn = [s for s in sig_gap if '⚠️' in s.get('ev_rating', '') and not s.get('is_pending')]
    sg_pend = [s for s in sig_gap if s.get('is_pending')]
    
    # 确保按评级重新排序
    sig_gap = sg_best + sg_good + sg_warn + sg_pend
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
                ev_rating=s.get('ev_rating', ''), signal_date=sig_date,
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
    
    def _print_sg_console(group, title):
        if group:
            print(f"\n[{title}] ({len(group)}只):")
            for s in group:
                tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
                rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
                gap_str = f" | 缺口={s.get('gap_size_pct', 0):.1f}%" if 'gap_size_pct' in s else ""
                pb_str = f" | 回调={s.get('pb_bars', '?')}周" if 'pb_bars' in s else ""
                strat_short = s.get('strategy_name', '').replace('STRATEGY_', '')
                print(f"  {s['code']:>12s} {s['name']:<6s} | 策略:{strat_short:<10s} | 买入:>={s['entry']:.2f} | 止损:{s['sl']:.2f} | 止盈:{tp_str} | R:R={rr_str}{gap_str}{pb_str}")
                    
    _print_sg_console(sg_best, "🌟 高预期")
    _print_sg_console(sg_good, "👍 常态")
    _print_sg_console(sg_warn, "⚠️ 低预期")
    _print_sg_console(sg_pend, "🔎 潜在追踪缺口 (尚未出现日历翻转信号)")
    
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
            report_md += f"| `{s['code']}` | **{s['name']}** | {strat_short} | {s['ev_rating']} | **>={s['entry']:.2f}** | *{s['sl']:.2f}* | {tp_str} | {rr_str} |\n"
            
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(report_md)
    print(f"✅ 生成本周末复盘报告: {md_path}")
    
    # === Discord 图文推送 ===
    print("\n🚀 正在生成 Discord 图文全量推送...")
    
    # 🟢 [Fix] 按评级分组构建完整推送消息，不再截断任何标的
    msg = f"🔔 **【周线 {strat_display} 雷达扫描完成】**\n"
    msg += f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d')}\n"
    if total_stocks > 0:
        msg += f"池子: 全市场 {total_stocks} 只个股\n"
    msg += f"----------------------\n"
    msg += f"🎯 **命中结果**: 共 {len(sig_gap)} 只\n"

    if not sig_gap:
        msg += f"\n💤 【周线/{strat_display}】，本次未发现信号"
    else:
        # v5 分组简报: 按策略聚合→组内按评级分组→同评级顿号→仅6位代码
        msg += format_push_brief(sig_gap)
        msg += f"\n\n🌟 A+/A 级图表即将推送..."

    # send_discord_message 已支持自动分段，不会截断
    send_discord_message(msg)
    
    if not sig_gap:
        print("✅ Discord 空结果推送成功！")
    else:
        # 🟢 A+/A 优先, 无则降级取 B/C 前 5 只 (与日线对齐)
        top_sigs = [s for s in sig_gap if 'A' in s.get('ev_rating', '')]
        if top_sigs:
            chart_label = "🌟 **A+/A 级 K线图**"
        else:
            top_sigs = sig_gap[:5]
            chart_label = "📋 **信号 K线图**"
        
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
                            reason=f"周线大底确认 | {s['ev_rating']}", df_override=df,
                            ev_rating=s['ev_rating'], sig_quality=s['sig_quality'], bears=s['bears'],
                            entry=s.get('entry', 0), rating=s.get('rating'), timeframe='周K'
                        )
                        if buf:
                            chart_bufs.append(buf)
                            chart_names.append(f"{s['code']}.png")
                            print(f"  ✅ {s['code']} {s['name']} [{s['ev_rating'][:10]}]")
                except Exception as e:
                    logger.warning(f"绘图失败 {s['code']}: {e}")
            
            # 🟢 分批推送 (VPN 环境下一次性推 20 张会被 Discord 断连)
            if chart_bufs:
                BATCH_SIZE = 5
                for batch_start in range(0, len(chart_bufs), BATCH_SIZE):
                    batch_bufs = chart_bufs[batch_start:batch_start + BATCH_SIZE]
                    batch_names = chart_names[batch_start:batch_start + BATCH_SIZE]
                    batch_msg = f"{chart_label} ({batch_start+1}-{batch_start+len(batch_bufs)}/{len(chart_bufs)})"
                    send_discord_images(batch_bufs, batch_names, content=batch_msg)
                print(f"✅ {len(chart_bufs)} 张图表分 {(len(chart_bufs)-1)//BATCH_SIZE+1} 批推送完成！")


def main():
    parser = argparse.ArgumentParser(description='周线 Structural Gap 策略扫描器')
    parser.add_argument('--limit', type=int, default=0, help='限制扫描股票数量')
    parser.add_argument('--weeks', type=int, default=4, help='检查最近N周的信号')
    parser.add_argument('--strategy', type=str, default=None, help='要运行的策略，多个以逗号隔开')
    args = parser.parse_args()
    
    # ⚠️ 提示用户确认周线数据是否已更新
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
    except Exception:        pass
        
    try:
        all_codes = dp.get_stock_list()
        if not all_codes:
            print("❌ 获取股票列表失败")
            return
        
        if args.limit > 0:
            all_codes = all_codes[:args.limit]
            
        active_strategies = None
        if args.strategy:
            if args.strategy.upper() == 'ALL':
                active_strategies = StrategyRegistry.get_strategies_by_timeframe('weekly')
            else:
                active_strategies = [s.strip().upper() for s in args.strategy.split(',')]
        else:
            active_strategies = StrategyRegistry.get_strategies_by_timeframe('weekly')[:1]  # 默认第一个周线策略
        
        print(f"\n🚀 周线扫描: {len(all_codes)} 只股票, 检查最近 {args.weeks} 周, 策略: {', '.join(active_strategies)}")
        print("=" * 80)
        
        results = scan_weekly_gap(all_codes, strategies=active_strategies, recent_weeks=args.weeks)
        _format_and_push_results(results, total_stocks=len(all_codes))
        
    except Exception as e:
        logger.error(f"严重异常: {e}")


if __name__ == '__main__':
    main()
