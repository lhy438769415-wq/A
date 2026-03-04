# -*- coding: utf-8 -*-
"""
Signal Tracker 信号追踪器 (core/signal_tracker.py)

借鉴 QuantConnect InsightManager + TradeStation MFE/MAE 分析。
职责:
  1. archive_signal()  — 信号归档 (扫描完成后写入, 幂等)
  2. track_signals()   — 追踪更新 (每日/每周检查价格, 推进状态)
  3. generate_report() — 统计报表 (按评级/策略/周期分组)
"""

import json
import logging
import sqlite3
import os
from datetime import datetime, timedelta

from config import settings
from core.database import get_db_connection
import core.data_provider as dp

logger = logging.getLogger(__name__)

# =====================================================================
# 常量: 信号生命周期参数
# =====================================================================
# 有效期: 超过此根数未触发入场, 标记 EXPIRED
PENDING_EXPIRY = {'daily': 20, 'weekly': 8}
# 持仓期限: 入场后超过此根数仍未触达 TP/SL, 标记 EXPIRED
ACTIVE_EXPIRY = {'daily': 60, 'weekly': 20}


# =====================================================================
# 建表 (由 database.py init_db 调用)
# =====================================================================
SIGNAL_ARCHIVE_DDL = """
CREATE TABLE IF NOT EXISTS signal_archive (
    signal_id      TEXT PRIMARY KEY,
    code           TEXT NOT NULL,
    name           TEXT,
    strategy       TEXT NOT NULL,
    timeframe      TEXT DEFAULT 'daily',
    signal_date    TEXT NOT NULL,
    scan_date      TEXT NOT NULL,
    entry_price    REAL,
    sl_price       REAL,
    tp_price       REAL,
    rr_ratio       REAL,
    ev_rating      TEXT,
    ev_score       INTEGER,
    status         TEXT DEFAULT 'PENDING',
    activated_date TEXT,
    resolved_date  TEXT,
    exit_price     REAL,
    max_favorable  REAL,
    max_adverse    REAL,
    bars_to_resolve INTEGER,
    extra_json     TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
SIGNAL_ARCHIVE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sa_status ON signal_archive (status);",
    "CREATE INDEX IF NOT EXISTS idx_sa_code ON signal_archive (code);",
    "CREATE INDEX IF NOT EXISTS idx_sa_strategy ON signal_archive (strategy);",
]


def init_signal_archive():
    """初始化 signal_archive 表 (幂等, 重复调用无副作用)"""
    try:
        with get_db_connection() as conn:
            conn.execute(SIGNAL_ARCHIVE_DDL)
            for idx_sql in SIGNAL_ARCHIVE_INDEXES:
                conn.execute(idx_sql)
            conn.commit()
            logger.debug("✅ signal_archive 表初始化完成")
    except Exception as e:
        logger.error(f"signal_archive 初始化失败: {e}")


# =====================================================================
# 1. 信号归档
# =====================================================================
def archive_signal(code, strategy, timeframe, entry, sl, tp,
                   ev_rating='', signal_date='', ev_score=None,
                   rr=0, name='', **extra) -> str:
    """
    将新信号写入 signal_archive 表。幂等操作 — 相同 signal_id 不会重复插入。
    
    Returns:
        signal_id: 归档成功返回 ID, 已存在返回已有 ID, 失败返回 ''
    """
    if not signal_date:
        signal_date = datetime.now().strftime('%Y-%m-%d')
    
    # 标准化策略名
    strategy = strategy.upper().replace('MTR_V35_STRUCTURAL', 'STRUCTURAL_GAP')
    
    signal_id = f"{code}_{strategy}_{signal_date}"
    scan_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if not name:
        try:
            name = dp.get_stock_name(code)
        except:
            name = ''
    
    # 将额外因子信息序列化为 JSON
    extra_json = json.dumps(extra, ensure_ascii=False, default=str) if extra else '{}'
    
    try:
        with get_db_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO signal_archive 
                (signal_id, code, name, strategy, timeframe, signal_date, scan_date,
                 entry_price, sl_price, tp_price, rr_ratio, ev_rating, ev_score, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (signal_id, code, name, strategy, timeframe, signal_date, scan_date,
                  entry, sl, tp, rr, ev_rating, ev_score, extra_json))
            conn.commit()
            
            if conn.total_changes > 0:
                logger.info(f"📥 信号归档: {name}({code}) [{strategy}/{timeframe}] {ev_rating}")
            else:
                logger.debug(f"信号已存在, 跳过: {signal_id}")
            return signal_id
    except Exception as e:
        logger.error(f"信号归档失败 {code}: {e}")
        return ''


# =====================================================================
# 2. 信号追踪
# =====================================================================
def track_signals(timeframe=None):
    """
    检查所有 PENDING/ACTIVE 信号的最新价格, 推进状态机。
    
    Returns:
        dict: {'updated': N, 'activated': N, 'wins': N, 'losses': N, 'expired': N}
    """
    init_signal_archive()
    stats = {'updated': 0, 'activated': 0, 'wins': 0, 'losses': 0, 'expired': 0}
    
    try:
        with get_db_connection() as conn:
            where = "WHERE status IN ('PENDING', 'ACTIVE')"
            if timeframe:
                where += f" AND timeframe = '{timeframe}'"
            
            rows = conn.execute(f"SELECT * FROM signal_archive {where}").fetchall()
            col_names = [desc[0] for desc in conn.execute(f"SELECT * FROM signal_archive LIMIT 0").description]
            
            signals = [dict(zip(col_names, row)) for row in rows]
            logger.info(f"🔍 追踪 {len(signals)} 个未结信号...")
            
            for sig in signals:
                result = _track_single(sig)
                if result:
                    result['_signal_id'] = sig['signal_id']
                    _update_signal(conn, result)
                    stats['updated'] += 1
                    new_status = result.get('status', '')
                    if new_status == 'ACTIVE' and sig['status'] == 'PENDING':
                        stats['activated'] += 1
                    elif new_status == 'WIN':
                        stats['wins'] += 1
                        _push_resolved_alert(sig, new_status)
                    elif new_status == 'LOSS':
                        stats['losses'] += 1
                        _push_resolved_alert(sig, new_status)
                    elif new_status == 'INVALIDATED':
                        stats['expired'] += 1
                        _push_resolved_alert(sig, new_status)
                    elif new_status == 'EXPIRED':
                        stats['expired'] += 1
            
            conn.commit()
    except Exception as e:
        logger.error(f"追踪失败: {e}")
    
    # 控制台汇总
    logger.info(f"📊 追踪完成: 更新 {stats['updated']} | "
                f"新入场 {stats['activated']} | "
                f"胜 {stats['wins']} | 负 {stats['losses']} | "
                f"过期 {stats['expired']}")
    return stats


def _track_single(sig: dict) -> dict:
    """追踪单个信号, 返回需要更新的字段 (如果无变化返回 None)"""
    code = sig['code']
    tf = sig['timeframe']
    status = sig['status']
    entry = sig['entry_price']
    sl = sig['sl_price']
    tp = sig['tp_price']
    
    # 获取信号日期之后的行情数据
    try:
        if tf == 'weekly':
            df = dp.get_stock_data_weekly(code)
        else:
            df = dp.get_stock_data(code, limit=200)
        
        if df is None or df.empty:
            return None
        
        # 确保日期列可比较 (日线用 'date', 周线用 'trade_date')
        date_col = None
        if 'date' in df.columns:
            date_col = 'date'
        elif 'trade_date' in df.columns:
            date_col = 'trade_date'
        
        if date_col:
            df[date_col] = df[date_col].astype(str)
            sig_date = sig['signal_date']
            # 只看信号日期之后的数据
            post_df = df[df[date_col] > sig_date]
        else:
            post_df = df
        
        if post_df.empty:
            return None
    except Exception as e:
        logger.debug(f"获取 {code} 行情失败: {e}")
        return None
    
    updates = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    
    if status == 'PENDING':
        return _track_pending(sig, post_df, updates)
    elif status == 'ACTIVE':
        return _track_active(sig, post_df, updates)
    
    return None


def _track_pending(sig, post_df, updates):
    """追踪 PENDING 状态: 逐 bar 检查是否触发入场 / 过期 / 失效"""
    entry = sig['entry_price']
    sl = sig['sl_price']
    tf = sig['timeframe']
    expiry_bars = PENDING_EXPIRY.get(tf, 20)
    
    bars_elapsed = len(post_df)
    
    # 逐 bar 时间顺序检查 — 入场优先于失效
    # (如果某根 bar 触发了 Buy Stop, 即使后续 SL 被击穿, 也应该是 ACTIVE→LOSS, 不是 INVALIDATED)
    for _, bar in post_df.iterrows():
        # 先检查入场触发 (Buy Stop: high >= entry)
        if entry and entry > 0 and bar['high'] >= entry:
            bar_date = str(bar.get('date', bar.get('trade_date', datetime.now().strftime('%Y-%m-%d'))))
            updates['status'] = 'ACTIVE'
            updates['activated_date'] = bar_date
            updates['max_favorable'] = entry
            updates['max_adverse'] = entry
            logger.info(f"🎯 {sig['name']}({sig['code']}) 入场触发！入场价 {entry:.2f}")
            return updates
        
        # 再检查缺口回补 (low <= SL)
        if sl and sl > 0 and bar['low'] <= sl:
            bar_date = str(bar.get('date', bar.get('trade_date', datetime.now().strftime('%Y-%m-%d'))))
            updates['status'] = 'INVALIDATED'
            updates['resolved_date'] = bar_date
            updates['exit_price'] = bar['low']
            updates['bars_to_resolve'] = bars_elapsed
            logger.info(f"💀 {sig['name']}({sig['code']}) 信号失效 (缺口被回补)")
            return updates
    
    # 检查是否过期
    if bars_elapsed >= expiry_bars:
        updates['status'] = 'EXPIRED'
        updates['resolved_date'] = datetime.now().strftime('%Y-%m-%d')
        updates['bars_to_resolve'] = bars_elapsed
        logger.info(f"⏰ {sig['name']}({sig['code']}) 信号过期 ({bars_elapsed} bars 未触发)")
        return updates
    
    return None  # 无变化


def _track_active(sig, post_df, updates):
    """追踪 ACTIVE 状态: 检查是否触达 TP / SL / 过期"""
    entry = sig['entry_price']
    sl = sig['sl_price']
    tp = sig['tp_price']
    tf = sig['timeframe']
    activated_date = sig.get('activated_date', sig['signal_date'])
    expiry_bars = ACTIVE_EXPIRY.get(tf, 60)
    
    # 从入场日开始的数据
    date_col = 'date' if 'date' in post_df.columns else ('trade_date' if 'trade_date' in post_df.columns else None)
    if date_col:
        active_df = post_df[post_df[date_col].astype(str) >= str(activated_date)]
    else:
        active_df = post_df
    
    if active_df.empty:
        return None
    
    bars_elapsed = len(active_df)
    
    # 更新 MFE / MAE
    max_high = active_df['high'].max()
    min_low = active_df['low'].min()
    prev_mfe = sig.get('max_favorable') or entry
    prev_mae = sig.get('max_adverse') or entry
    updates['max_favorable'] = max(max_high, prev_mfe) if prev_mfe else max_high
    updates['max_adverse'] = min(min_low, prev_mae) if prev_mae else min_low
    
    # 逐 bar 检查 — 判断 SL 和 TP 谁先触达
    for _, bar in active_df.iterrows():
        # 先检查止损 (保守原则: 同一根K线先看最低买)
        if sl and sl > 0 and bar['low'] <= sl:
            updates['status'] = 'LOSS'
            updates['exit_price'] = sl
            updates['resolved_date'] = str(bar.get('date', bar.get('trade_date', datetime.now().strftime('%Y-%m-%d'))))
            updates['bars_to_resolve'] = bars_elapsed
            logger.info(f"🔴 {sig['name']}({sig['code']}) 止损 @ {sl:.2f}")
            return updates
        
        # 再检查止盈
        if tp and tp > 0 and bar['high'] >= tp:
            updates['status'] = 'WIN'
            updates['exit_price'] = tp
            updates['resolved_date'] = str(bar.get('date', bar.get('trade_date', datetime.now().strftime('%Y-%m-%d'))))
            updates['bars_to_resolve'] = bars_elapsed
            logger.info(f"🟢 {sig['name']}({sig['code']}) 止盈 @ {tp:.2f} ✨")
            return updates
    
    # 检查持仓过期
    if bars_elapsed >= expiry_bars:
        last_close = active_df.iloc[-1]['close']
        pnl = (last_close - entry) / entry * 100 if entry > 0 else 0
        updates['status'] = 'EXPIRED'
        updates['exit_price'] = last_close
        updates['resolved_date'] = datetime.now().strftime('%Y-%m-%d')
        updates['bars_to_resolve'] = bars_elapsed
        logger.info(f"⏰ {sig['name']}({sig['code']}) 持仓过期 ({bars_elapsed} bars, 浮盈 {pnl:+.1f}%)")
        return updates
    
    # 仍然在持仓中, 仅更新 MFE/MAE
    return updates


def _update_signal(conn, updates: dict):
    """将追踪结果写回数据库 (由 track_signals 内部调用, conn 已在上下文中)"""
    signal_id = updates.pop('_signal_id', None)
    if not signal_id:
        return
    
    set_clauses = []
    values = []
    for k, v in updates.items():
        if v is not None:
            set_clauses.append(f"{k} = ?")
            values.append(v)
    
    if not set_clauses:
        return
    
    values.append(signal_id)
    sql = f"UPDATE signal_archive SET {', '.join(set_clauses)} WHERE signal_id = ?"
    conn.execute(sql, values)


# =====================================================================
# 3. 统计报表
# =====================================================================
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


# =====================================================================
# 4. 交互式信号追踪仪表盘 (hunter.py 选项 2 调用)
# =====================================================================
def run_tracker_dashboard():
    """
    一站式追踪仪表盘:
    1. 自动追踪所有未结信号
    2. 获取最新价格, 分为盈利/亏损/等待三组
    3. 控制台打印个股仪表盘
    4. 推送到 Discord
    """
    init_signal_archive()
    
    # Step 0: 数据新鲜度检查
    try:
        with get_db_connection() as conn:
            r = conn.execute('SELECT MAX(trade_date) FROM daily_bars').fetchone()
            if r and r[0]:
                data_date = r[0]
                today = datetime.now().strftime('%Y-%m-%d')
                if data_date < today:
                    d1 = datetime.strptime(data_date, '%Y-%m-%d')
                    d2 = datetime.strptime(today, '%Y-%m-%d')
                    lag = (d2 - d1).days
                    logger.warning(f"⚠️ 日线数据停留在 {data_date} (滞后 {lag} 天)")
    except Exception:
        pass
    
    # Step 1: 追踪更新
    stats = track_signals()
    
    # Step 2: 获取所有未结信号
    try:
        with get_db_connection() as conn:
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
            
            active_rows = conn.execute(
                "SELECT * FROM signal_archive WHERE status = 'ACTIVE' ORDER BY ev_score DESC"
            ).fetchall()
            pending_rows = conn.execute(
                "SELECT * FROM signal_archive WHERE status = 'PENDING' ORDER BY ev_score DESC"
            ).fetchall()
            
            # 本月已结算统计
            from datetime import datetime, timedelta
            month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')
            resolved_rows = conn.execute(
                "SELECT status, entry_price, sl_price, exit_price FROM signal_archive "
                "WHERE status IN ('WIN','LOSS') AND resolved_date >= ?", (month_start,)
            ).fetchall()
    except Exception as e:
        logger.error(f"仪表盘查询失败: {e}")
        return
    
    active_signals = [dict(zip(col_names, r)) for r in active_rows]
    pending_signals = [dict(zip(col_names, r)) for r in pending_rows]
    
    # Step 3: 获取最新价格并分组
    profit_group = []  # 盈利中
    loss_group = []    # 亏损中
    
    for sig in active_signals:
        current_price = _get_latest_price(sig['code'], sig['timeframe'])
        if current_price is None:
            continue
        
        entry = sig['entry_price'] or 0
        sl = sig['sl_price'] or 0
        tp = sig['tp_price'] or 0
        
        pnl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0
        
        # 计算进度: 从 SL → Entry → TP 的百分比位置
        total_range = tp - sl if tp > sl > 0 else 1
        progress = (current_price - sl) / total_range if total_range > 0 else 0.5
        progress = max(0, min(1, progress))
        
        # 距离 TP / SL 的百分比
        dist_tp = (tp - current_price) / current_price * 100 if tp > 0 and current_price > 0 else 0
        dist_sl = (current_price - sl) / current_price * 100 if sl > 0 and current_price > 0 else 0
        
        # MFE/MAE 百分比
        mfe_pct = ((sig.get('max_favorable') or entry) - entry) / entry * 100 if entry > 0 else 0
        mae_pct = ((sig.get('max_adverse') or entry) - entry) / entry * 100 if entry > 0 else 0
        
        # 持仓天数
        activated = sig.get('activated_date', sig['signal_date'])
        try:
            days_held = (datetime.now() - datetime.strptime(str(activated)[:10], '%Y-%m-%d')).days
        except:
            days_held = 0
        
        # 简化评级
        rating = _simplify_rating(sig.get('ev_rating', ''))
        
        item = {
            'name': sig['name'] or sig['code'],
            'code': sig['code'],
            'rating': rating,
            'entry': entry, 'sl': sl, 'tp': tp,
            'current': current_price,
            'pnl_pct': pnl_pct,
            'progress': progress,
            'dist_tp': dist_tp, 'dist_sl': dist_sl,
            'mfe_pct': mfe_pct, 'mae_pct': mae_pct,
            'days_held': days_held,
        }
        
        if pnl_pct >= 0:
            profit_group.append(item)
        else:
            loss_group.append(item)
    
    # 排序: 盈利按 pnl 从高到低, 亏损按 pnl 从低到高 (最危险的在前)
    profit_group.sort(key=lambda x: x['pnl_pct'], reverse=True)
    loss_group.sort(key=lambda x: x['pnl_pct'])
    
    # Step 4: 控制台输出
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\n{'═' * 60}")
    print(f"  📊 信号追踪仪表盘 | {today}")
    print(f"{'═' * 60}")
    
    # 盈利组
    if profit_group:
        print(f"\n  🟢 盈利中 ({len(profit_group)}只)")
        for item in profit_group:
            bar = _make_progress_bar(item['progress'], 10)
            print(f"  ┌─ {item['name']} {item['code']} [{item['rating']}]")
            print(f"  │  入场 {item['entry']:.2f} → 现价 {item['current']:.2f} ({item['pnl_pct']:+.1f}%)  {bar} TP {item['tp']:.2f}")
            print(f"  └─ 距止盈 {item['dist_tp']:.1f}% | 持仓 {item['days_held']} 天 | MFE {item['mfe_pct']:+.1f}%")
    
    # 亏损组
    if loss_group:
        print(f"\n  🔴 亏损预警 ({len(loss_group)}只)")
        for item in loss_group:
            danger = " ⚠️ 危险！" if item['dist_sl'] < 3 else ""
            print(f"  ┌─ {item['name']} {item['code']} [{item['rating']}]{danger}")
            print(f"  │  入场 {item['entry']:.2f} → 现价 {item['current']:.2f} ({item['pnl_pct']:+.1f}%)")
            print(f"  └─ 距止损 {item['dist_sl']:.1f}% | 持仓 {item['days_held']} 天 | MAE {item['mae_pct']:+.1f}%")
    
    if not profit_group and not loss_group:
        print(f"\n  (暂无持仓中的信号)")
    
    # 等待入场 — 详细视图
    pending_details = []
    if pending_signals:
        for sig in pending_signals:
            current_price = _get_latest_price(sig['code'], sig['timeframe'])
            if current_price is None:
                continue
            
            entry = sig['entry_price'] or 0
            sl = sig['sl_price'] or 0
            tp = sig['tp_price'] or 0
            rating = _simplify_rating(sig.get('ev_rating', ''))
            
            # 距 entry 还差多少 (正数=还没到, 负数=已超过)
            dist_entry_pct = (entry - current_price) / current_price * 100 if current_price > 0 and entry > 0 else 0
            # 距 SL 百分比 (越小越危险)
            dist_sl_pct = (current_price - sl) / current_price * 100 if current_price > 0 and sl > 0 else 999
            
            pending_details.append({
                'name': sig['name'] or sig['code'],
                'code': sig['code'],
                'rating': rating,
                'entry': entry, 'sl': sl, 'tp': tp,
                'current': current_price,
                'dist_entry_pct': dist_entry_pct,
                'dist_sl_pct': dist_sl_pct,
            })
        
        # 按距 entry 从近到远排序 (快要触发的排前面)
        pending_details.sort(key=lambda x: x['dist_entry_pct'])
        
        # 分组: A+ / A / 其他
        vip_pending = [p for p in pending_details if p['rating'] == 'A+']
        a_pending = [p for p in pending_details if p['rating'] == 'A']
        other_pending = [p for p in pending_details if p['rating'] not in ('A+', 'A')]
        
        print(f"\n  ⏳ 等待入场 ({len(pending_details)}只)")
        
        # A+ VIP — 每只都详细展示
        if vip_pending:
            print(f"  ┏━ 🌟🌟 A+ 极品 ({len(vip_pending)}只) ━━━")
            for p in vip_pending:
                fire = "🔥" if p['dist_entry_pct'] < 3 else ""
                danger = " ⚠️" if p['dist_sl_pct'] < 5 else ""
                entry_desc = _format_entry_distance(p['dist_entry_pct'])
                print(f"  ┃ {fire}{p['name']} {p['code']}")
                print(f"  ┃   现价 {p['current']:.2f} | {entry_desc} | 距止损 {p['dist_sl_pct']:.1f}%{danger}")
                print(f"  ┃   入场 {p['entry']:.2f} → 止盈 {p.get('tp', 0):.2f} (止损 {p['sl']:.2f})")
            print(f"  ┗━━━━━━━━━━━━━━━━━")
        
        # A 级 — 正常展示
        if a_pending:
            print(f"  ── 🌟 A 级 ({len(a_pending)}只) ──")
            for p in a_pending:
                fire = "🔥" if p['dist_entry_pct'] < 3 else "  "
                entry_desc = _format_entry_distance(p['dist_entry_pct'])
                print(f"  {fire} {p['name']} {p['code']} | 现价 {p['current']:.2f} | {entry_desc}")
        
        # 其他 — 只摘要
        if other_pending:
            print(f"  ── 其他 ({len(other_pending)}只) ──")
            close_ones = [p for p in other_pending if p['dist_entry_pct'] < 5]
            if close_ones:
                for p in close_ones:
                    entry_desc = _format_entry_distance(p['dist_entry_pct'])
                    print(f"    {p['name']}[{p['rating']}] {entry_desc}")
            far_count = len(other_pending) - len(close_ones)
            if far_count > 0:
                print(f"    ... 另有 {far_count} 只距入场较远")
    
    # 月度统计
    wins = sum(1 for r in resolved_rows if r[0] == 'WIN')
    losses_count = sum(1 for r in resolved_rows if r[0] == 'LOSS')
    total = wins + losses_count
    wr = wins / total * 100 if total > 0 else 0
    
    # 计算平均 R
    r_list = []
    for r in resolved_rows:
        e, s, ex = r[1] or 0, r[2] or 0, r[3] or 0
        risk = e - s if e and s else 1
        if risk > 0:
            r_list.append((ex - e) / risk)
    avg_r = sum(r_list) / len(r_list) if r_list else 0
    
    print(f"\n  📈 本月战绩: 胜 {wins} 负 {losses_count} | 胜率 {wr:.0f}% | 平均 {avg_r:+.2f}R")
    print(f"{'═' * 60}\n")
    
    # Step 5: Discord 推送
    try:
        discord_msg = _format_dashboard_discord(profit_group, loss_group, pending_details, wins, losses_count, wr, avg_r)
        from tools.notifier import send_discord_message
        send_discord_message(discord_msg)
        logger.info("✅ 仪表盘已推送 Discord")
    except Exception as e:
        logger.warning(f"Discord 推送失败: {e}")


def _get_latest_price(code, timeframe='daily'):
    """获取最新收盘价 (仪表盘展示用, 始终取日线最新价)"""
    try:
        # 仪表盘始终用日线数据获取最新价, 即使是周线信号
        # (周线数据可能滞后最多5个交易日)
        df = dp.get_stock_data(code, limit=5)
        if df is not None and not df.empty:
            return float(df.iloc[-1]['close'])
    except:
        pass
    return None


def _format_entry_distance(dist_pct):
    """将入场距离百分比转为人话: 正数=还没到, 负数=已超过"""
    if dist_pct > 0:
        return f"还差 {dist_pct:.1f}%"
    elif dist_pct < 0:
        return f"已超入场 {abs(dist_pct):.1f}%"
    else:
        return "刚好到入场价"


def _push_resolved_alert(sig, status):
    """信号结算时推送 Discord 通知 (含K线图)"""
    try:
        code = sig['code']
        name = sig.get('name', code)
        rating = _simplify_rating(sig.get('ev_rating', ''))
        entry = sig.get('entry_price', 0)
        sl = sig.get('sl_price', 0)
        tp = sig.get('tp_price', 0)
        
        # 构造消息
        if status == 'WIN':
            icon = "🟢"
            title = "止盈达成"
            detail = f"入场 {entry:.2f} → 止盈 {tp:.2f}"
        elif status == 'LOSS':
            icon = "🔴"
            title = "触发止损"
            detail = f"入场 {entry:.2f} → 止损 {sl:.2f}"
        elif status == 'INVALIDATED':
            icon = "💀"
            title = "缺口被回补"
            detail = f"入场价 {entry:.2f} 未触发, SL {sl:.2f} 已击穿"
        else:
            return
        
        msg = f"{icon} **{name}** ({code}) [{rating}]\n"
        msg += f"状态: **{title}**\n"
        msg += f"{detail}\n"
        
        from tools.notifier import send_discord_message, generate_chart_bytes, send_discord_image
        send_discord_message(msg)
        
        # 生成并推送K线图
        # 周线信号需要用周线数据, 否则 generate_chart_bytes 默认走日线
        df_override = None
        timeframe = sig.get('timeframe', 'daily')
        if timeframe == 'weekly':
            try:
                df_w = dp.get_stock_data_weekly(code, limit=300)
                if df_w is not None and not df_w.empty:
                    from core.calculator import add_indicators
                    from core.strategies.structural_gap_strategy import StructuralGapStrategy
                    df_w = add_indicators(df_w)
                    df_w = StructuralGapStrategy().calculate_signals(df_w)
                    df_override = df_w
            except Exception as e:
                logger.warning(f"周线数据获取失败, 回退日线: {e}")
        
        chart_buf = generate_chart_bytes(
            code=code, stock_name=name,
            strategy_type=sig.get('strategy', 'STRUCTURAL_GAP'),
            sl_price=sl, tp1=tp,
            ev_rating=sig.get('ev_rating', ''),
            df_override=df_override
        )
        if chart_buf:
            send_discord_image(chart_buf, filename=f"{code}_{status}.png")
            logger.info(f"📤 {name}({code}) [{status}] 图表已推送 Discord")
    except Exception as e:
        logger.warning(f"推送结算通知失败: {e}")


def _simplify_rating(rating_str):
    """'🌟🌟 极品 (A+)' → 'A+'"""
    if not rating_str:
        return '?'
    for tag in ['A+', 'A', 'B', 'C', 'D']:
        if tag in str(rating_str):
            return tag
    return '?'


def _make_progress_bar(progress, width=10):
    """生成进度条: ██████░░░░"""
    filled = int(progress * width)
    filled = max(0, min(width, filled))
    return '█' * filled + '░' * (width - filled)


def _format_dashboard_discord(profit, loss, pending, wins, losses_count, wr, avg_r):
    """格式化 Discord 推送消息 (严格控制 2000 字符限制)"""
    msg = "📊 **信号追踪仪表盘**\n"
    
    if profit:
        msg += f"\n🟢 **盈利中 ({len(profit)}只)**\n"
        for p in profit[:5]:
            msg += f"  {p['name']} [{p['rating']}] | {p['pnl_pct']:+.1f}% | 距TP {p['dist_tp']:.1f}%\n"
    
    if loss:
        msg += f"\n🔴 **亏损预警 ({len(loss)}只)**\n"
        for l in loss[:3]:
            danger = " ⚠️" if l['dist_sl'] < 3 else ""
            msg += f"  {l['name']} [{l['rating']}] | {l['pnl_pct']:+.1f}% | 距SL {l['dist_sl']:.1f}%{danger}\n"
    
    if pending:
        vip_p = [p for p in pending if p.get('rating') == 'A+']
        a_p = [p for p in pending if p.get('rating') == 'A']
        msg += f"\n⏳ **等待入场 ({len(pending)}只)**\n"
        if vip_p:
            show_vip = vip_p[:5]  # 只显示最近 5 只 A+
            msg += f"🌟🌟 **A+ 极品 ({len(vip_p)}只):**\n"
            for p in show_vip:
                fire = "🔥" if p['dist_entry_pct'] < 3 else ""
                msg += f"  {fire}{p['name']} | {_format_entry_distance(p['dist_entry_pct'])}\n"
            if len(vip_p) > 5:
                msg += f"  ...另有 {len(vip_p) - 5} 只 A+\n"
        if a_p:
            # A 级只显示名字列表
            names = [p['name'] for p in a_p[:5]]
            msg += f"🌟 A级 ({len(a_p)}只): {' / '.join(names)}\n"
        other_count = len(pending) - len(vip_p) - len(a_p)
        if other_count > 0:
            msg += f"  + {other_count} 只 B/C 级\n"
    
    msg += f"\n📈 本月: 胜{wins} 负{losses_count} | 胜率{wr:.0f}% | {avg_r:+.2f}R"
    
    # 安全截断 (Discord 限制 2000 字符)
    if len(msg) > 1950:
        msg = msg[:1940] + "\n... (已截断)"
    
    return msg

