# -*- coding: utf-8 -*-
"""
统计报表生成与格式化 (Signal Tracker 职责 3 + 报表 Discord 格式化)

generate_report() / _print_report(): 按评级/策略/周期分组统计胜率、盈亏比、MFE/MAE。
format_tracker_discord_msg(): 将报表格式化为 Discord 推送消息。
拆分自 core/signal_tracker.py (P11)。
"""

import json
import os
from datetime import datetime, timedelta

from config import settings
from core.database import get_db_connection, init_signal_archive

from ._shared import logger


def generate_report(timeframe=None, strategy=None, days=90) -> dict:
    """
    按评级/策略/周期分组统计信号的胜率、盈亏比、MFE/MAE 分布。
    
    Returns:
        dict: 包含分组统计数据
    """
    init_signal_archive()
    
    try:
        with get_db_connection() as conn:
            # 构建查询条件
            conditions = ["status IN ('WIN', 'LOSS', 'EXPIRED')"]
            params = []
            
            if timeframe:
                conditions.append("timeframe = ?")
                params.append(timeframe)
            if strategy:
                conditions.append("strategy = ?")
                params.append(strategy)
            if days > 0:
                cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
                conditions.append("scan_date >= ?")
                params.append(cutoff)
            
            where = " AND ".join(conditions)
            
            rows = conn.execute(
                f"SELECT * FROM signal_archive WHERE {where} ORDER BY scan_date",
                params
            ).fetchall()
            
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
            resolved = [dict(zip(col_names, row)) for row in rows]
            
            # 同时获取仍在进行中的信号数
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM signal_archive WHERE status IN ('PENDING', 'ACTIVE')"
            ).fetchone()[0]
    except Exception as e:
        logger.error(f"报表查询失败: {e}")
        return {}
    
    if not resolved:
        report = {
            'total_resolved': 0,
            'pending_active': pending_count,
            'message': '暂无已结算信号'
        }
        _print_report(report)
        return report
    
    # 整体统计
    total = len(resolved)
    wins = sum(1 for r in resolved if r['status'] == 'WIN')
    losses = sum(1 for r in resolved if r['status'] == 'LOSS')
    expired = sum(1 for r in resolved if r['status'] == 'EXPIRED')
    
    win_rate = wins / total * 100 if total > 0 else 0
    
    # 计算平均 R 倍数
    r_multiples = []
    for r in resolved:
        entry = r['entry_price'] or 0
        sl = r['sl_price'] or 0
        exit_p = r['exit_price'] or entry
        risk = entry - sl if entry and sl else 1
        if risk > 0:
            r_mult = (exit_p - entry) / risk
            r_multiples.append(r_mult)
    
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
    
    # 按评级分组
    rating_groups = {}
    for r in resolved:
        # 简化评级标签
        rating = r.get('ev_rating', 'N/A') or 'N/A'
        if 'A+' in rating or '极品' in rating:
            key = 'A+'
        elif 'A' in rating and 'A+' not in rating and '高预期' in str(rating):
            key = 'A'
        elif 'B' in rating or '常态' in rating:
            key = 'B'
        elif 'C' in rating or '低预期' in rating:
            key = 'C'
        elif 'D' in rating or '毒性' in rating:
            key = 'D'
        else:
            key = 'N/A'
        
        if key not in rating_groups:
            rating_groups[key] = {'total': 0, 'wins': 0, 'r_list': []}
        rating_groups[key]['total'] += 1
        if r['status'] == 'WIN':
            rating_groups[key]['wins'] += 1
        
        entry = r['entry_price'] or 0
        sl = r['sl_price'] or 0
        exit_p = r['exit_price'] or entry
        risk = entry - sl if entry and sl else 1
        if risk > 0:
            rating_groups[key]['r_list'].append((exit_p - entry) / risk)
    
    # 生成报表数据
    report = {
        'total_resolved': total,
        'pending_active': pending_count,
        'wins': wins,
        'losses': losses,
        'expired': expired,
        'win_rate': round(win_rate, 1),
        'avg_r': round(avg_r, 2),
        'by_rating': {},
        'timeframe': timeframe or 'all',
        'strategy': strategy or 'all',
        'days': days,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    for key in ['A+', 'A', 'B', 'C', 'D', 'N/A']:
        if key in rating_groups:
            g = rating_groups[key]
            g_wr = g['wins'] / g['total'] * 100 if g['total'] > 0 else 0
            g_avg_r = sum(g['r_list']) / len(g['r_list']) if g['r_list'] else 0
            report['by_rating'][key] = {
                'total': g['total'],
                'wins': g['wins'],
                'win_rate': round(g_wr, 1),
                'avg_r': round(g_avg_r, 2)
            }
    
    _print_report(report)
    
    # 保存到 JSON
    try:
        report_path = os.path.join(os.path.dirname(settings.DB_PATH), 'signal_tracker_report.json')
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        logger.info(f"📄 报表已保存: {report_path}")
    except Exception as e:
        logger.warning(f"报表保存失败: {e}")
    
    return report


def _print_report(report: dict):
    """控制台打印报表"""
    print("\n" + "=" * 55)
    print(f"  📊 Signal Tracker 信号追踪报告")
    print("=" * 55)
    
    if report.get('message'):
        print(f"\n  {report['message']}")
        if report.get('pending_active', 0) > 0:
            print(f"  (当前仍有 {report['pending_active']} 个信号在追踪中)")
        print("=" * 55)
        return
    
    tf = report.get('timeframe', 'all')
    strat = report.get('strategy', 'all')
    print(f"  周期: {tf} | 策略: {strat} | 最近 {report.get('days', 90)} 天")
    print(f"  仍在追踪: {report.get('pending_active', 0)} 个")
    print("-" * 55)
    
    print(f"\n  已结信号: {report['total_resolved']} 个")
    print(f"  ✅ 胜: {report['wins']}  ❌ 负: {report['losses']}  ⏰ 过期: {report['expired']}")
    print(f"  📈 胜率: {report['win_rate']}%  |  平均 R: {report['avg_r']:+.2f}R")
    
    by_rating = report.get('by_rating', {})
    if by_rating:
        print(f"\n  {'评级':<6s} {'信号数':>6s} {'胜率':>8s} {'平均R':>8s}")
        print("  " + "-" * 32)
        for key in ['A+', 'A', 'B', 'C', 'D', 'N/A']:
            if key in by_rating:
                g = by_rating[key]
                print(f"  {key:<6s} {g['total']:>6d} {g['win_rate']:>7.1f}% {g['avg_r']:>+7.2f}R")
    
    print("\n" + "=" * 55)


def format_tracker_discord_msg(report: dict) -> str:
    """将报表格式化为 Discord 推送消息 (注重可读性)"""
    if report.get('message'):
        return f"📊 {report['message']}"
    
    msg = "📊 **信号追踪 · 月度战报**\n\n"
    
    # 核心成绩
    total = report['total_resolved']
    pending = report.get('pending_active', 0)
    msg += f"本月已结算 **{total}** 笔信号\n"
    msg += f"✅ 盈利 **{report['wins']}** 笔 | ❌ 亏损 **{report['losses']}** 笔\n"
    if report['expired'] > 0:
        msg += f"⏰ 过期未触发 {report['expired']} 笔\n"
    msg += f"\n"
    
    # 突出胜率和盈亏比
    msg += f"📈 胜率 **{report['win_rate']}%** | 平均盈亏比 **{report['avg_r']:+.2f}R**\n"
    msg += f"🔍 仍在追踪 **{pending}** 个信号\n"
    
    # 按评级展示 (只显示有数据的)
    by_rating = report.get('by_rating', {})
    if by_rating:
        msg += f"\n**各评级表现:**\n"
        for key in ['A+', 'A', 'B', 'C', 'D']:
            if key in by_rating:
                g = by_rating[key]
                bar = "🟢" if g['avg_r'] > 0 else "🔴"
                msg += f"{bar} {key} 级: {g['total']}笔 · 胜率{g['win_rate']:.0f}% · {g['avg_r']:+.2f}R\n"
    
    return msg
