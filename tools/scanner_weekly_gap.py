# -*- coding: utf-8 -*-
"""
周线 结构性测量缺口策略独立扫描器 (scanner_weekly_gap.py)

[P2-heavy Phase 2] 薄封装: 扫描核心已提取至 core/scan_engine.py
(scan_weekly_gap_signals / scan_single_code_weekly / _get_strategy_cols /
fetch_weekly_data), 本文件只负责:
  - 命令行入口 (main)
  - 结果格式化 / Discord 推送 / JSON+MD 报告 / Signal Tracker 归档
  - 图表生成 (复用 notifier.generate_chart_bytes)

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

from core.calculator import add_indicators
from core.strategy_registry import StrategyRegistry
import core.data_provider as dp

# 🟢 [P2-heavy] 扫描核心委托 core.scan_engine (消除双引擎编排层重复)
from core.scan_engine import (
    scan_weekly_gap_signals,
    scan_single_code_weekly,
    _get_strategy_cols,
    _letter_to_ev_text,
    fetch_weekly_data,
)
# 兼容别名 (供 main / 现有测试 tools.scanner_weekly_gap.* 零改动)
scan_weekly_gap = scan_weekly_gap_signals
_scan_single_code = scan_single_code_weekly

from tools.notifier import generate_chart_bytes, stitch_images, send_discord_image, send_discord_message, send_discord_images, format_push_brief
from core.log_config import get_logger

logger = get_logger(__name__)


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
