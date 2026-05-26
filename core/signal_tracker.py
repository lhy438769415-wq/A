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
# 1b. Watchlist 兼容方法 (P2: 供 WatchlistManager Facade 调用)
# =====================================================================
def check_signal_exists(code: str, strategy: str = None, signal_date: str = None) -> bool:
    """
    检查信号是否已存在。

    Args:
        code: 股票代码
        strategy: 策略名 (可选, 与 signal_date 一起精确匹配)
        signal_date: 信号日期 (可选, 与 strategy 一起精确匹配)

    Returns:
        bool: 信号是否存在
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            if strategy and signal_date:
                strategy = strategy.upper().replace('MTR_V35_STRUCTURAL', 'STRUCTURAL_GAP')
                signal_id = f"{code}_{strategy}_{signal_date}"
                row = conn.execute(
                    "SELECT 1 FROM signal_archive WHERE signal_id = ?", (signal_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM signal_archive WHERE code = ? LIMIT 1", (code,)
                ).fetchone()
            return row is not None
    except Exception as e:
        logger.error(f"检查信号存在失败 {code}: {e}")
        return False


def add_signal_entry(code: str, entry: float, sl: float, score: float = 0.0,
                     signal_date: str = '', strategy: str = '', timeframe: str = 'daily',
                     name: str = '', tp: float = 0.0, ev_rating: str = '',
                     ev_score: int = 0, rr: float = 0.0, **extra) -> str:
    """
    添加一个信号到 signal_archive (兼容 WatchlistManager.add_signal 接口)。
    内部调用 archive_signal(), 保持幂等性。

    Args:
        code: 股票代码
        entry: 入场价
        sl: 止损价
        score: 信号质量分
        signal_date: 信号日期
        strategy: 策略名
        timeframe: 时间周期 (daily/weekly)
        name: 股票名
        tp: 止盈价
        ev_rating: EV 评级
        ev_score: EV 积分
        rr: 盈亏比
        **extra: 额外参数 (序列化为 extra_json)

    Returns:
        str: signal_id 或 ''
    """
    if not strategy:
        strategy = 'UNKNOWN'
    if not signal_date:
        signal_date = datetime.now().strftime('%Y-%m-%d')
    return archive_signal(
        code=code, strategy=strategy, timeframe=timeframe,
        entry=entry, sl=sl, tp=tp,
        ev_rating=ev_rating, signal_date=signal_date,
        ev_score=ev_score, rr=rr, name=name,
        sig_quality=score, **extra
    )


def get_signal_status(code: str, strategy: str = None) -> str:
    """
    获取信号的当前状态。

    Args:
        code: 股票代码
        strategy: 策略名 (可选, 用于精确匹配)

    Returns:
        str: 信号状态 (PENDING/ACTIVE/WIN/LOSS/INVALIDATED/EXPIRED) 或空字符串
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            if strategy:
                strategy = strategy.upper().replace('MTR_V35_STRUCTURAL', 'STRUCTURAL_GAP')
                row = conn.execute(
                    "SELECT status FROM signal_archive WHERE code = ? AND strategy = ? ORDER BY updated_at DESC LIMIT 1",
                    (code, strategy)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT status FROM signal_archive WHERE code = ? ORDER BY updated_at DESC LIMIT 1",
                    (code,)
                ).fetchone()
            return row[0] if row else ''
    except Exception as e:
        logger.error(f"获取信号状态失败 {code}: {e}")
        return ''


def get_signals_by_status(status: str, strategy: str = None, timeframe: str = None) -> list:
    """
    获取指定状态的所有信号。

    Args:
        status: 信号状态 (PENDING/ACTIVE/WIN/LOSS/INVALIDATED/EXPIRED)
        strategy: 策略名 (可选)
        timeframe: 时间周期 (可选)

    Returns:
        list[dict]: 信号记录列表
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            conditions = ["status = ?"]
            params = [status]
            if strategy:
                strategy = strategy.upper().replace('MTR_V35_STRUCTURAL', 'STRUCTURAL_GAP')
                conditions.append("strategy = ?")
                params.append(strategy)
            if timeframe:
                conditions.append("timeframe = ?")
                params.append(timeframe)

            where = " AND ".join(conditions)
            rows = conn.execute(
                f"SELECT * FROM signal_archive WHERE {where} ORDER BY ev_score DESC, updated_at DESC",
                params
            ).fetchall()
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
            return [dict(zip(col_names, row)) for row in rows]
    except Exception as e:
        logger.error(f"获取信号列表失败: {e}")
        return []


def update_signal_entry(code: str, strategy: str = None, **fields) -> bool:
    """
    更新信号的指定字段 (兼容 WatchlistManager.update_signal_bar 接口)。

    Args:
        code: 股票代码
        strategy: 策略名 (可选, 用于精确匹配)
        **fields: 要更新的字段 (如 entry_price, sl_price, status 等)

    Returns:
        bool: 是否更新成功
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            conditions = ["code = ?"]
            params = []
            params.append(code)
            if strategy:
                strategy = strategy.upper().replace('MTR_V35_STRUCTURAL', 'STRUCTURAL_GAP')
                conditions.append("strategy = ?")
                params.append(strategy)

            # 限定只更新未结算的信号
            conditions.append("status IN ('PENDING', 'ACTIVE')")

            set_clauses = ["updated_at = ?"]
            values = [datetime.now().strftime('%Y-%m-%d %H:%M:%S')]

            for k, v in fields.items():
                # 映射 Watchlist 字段名到 signal_archive 字段名
                if k == 'entry':
                    k = 'entry_price'
                elif k == 'sl':
                    k = 'sl_price'
                elif k == 'tp':
                    k = 'tp_price'
                elif k == 'signal_bar_idx':
                    # signal_archive 没有 signal_bar_idx 字段, 存入 extra_json
                    continue
                set_clauses.append(f"{k} = ?")
                values.append(v)

            if len(set_clauses) <= 1:
                return False  # 没有实际要更新的字段

            values.extend(params)
            where = " AND ".join(conditions)
            sql = f"UPDATE signal_archive SET {', '.join(set_clauses)} WHERE {where}"
            conn.execute(sql, values)
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"更新信号失败 {code}: {e}")
        return False



def track_signals(timeframe=None):
    """
    检查所有 PENDING/ACTIVE 信号的最新价格, 推进状态机。
    
    🟢 V9.3: 不再逐个推送结算通知, 改为收集事件列表, 由仪表盘统一按状态分组推送。
    
    Returns:
        dict: {'updated': N, 'activated': N, 'wins': N, 'losses': N, 'expired': N}
    """
    init_signal_archive()
    stats = {'updated': 0, 'activated': 0, 'wins': 0, 'losses': 0, 'expired': 0}
    
    try:
        with get_db_connection() as conn:
            where = "WHERE status IN ('PENDING', 'ACTIVE')"
            params = []
            if timeframe:
                where += " AND timeframe = ?"
                params.append(timeframe)
            
            rows = conn.execute(f"SELECT * FROM signal_archive {where}", params).fetchall()
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
                        # 🟢 V9.3: 不再逐个推送, 由仪表盘统一推送
                    elif new_status == 'LOSS':
                        stats['losses'] += 1
                    elif new_status == 'INVALIDATED':
                        stats['expired'] += 1
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
    # 始终用日线数据追踪 — 周线 bar 要等周五收盘才出现, 周中追踪会完全看不到价格变动
    try:
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


def _get_bar_date(df, iloc_idx):
    """[Phase2 辅助] 从 DataFrame 行中提取日期字符串"""
    row = df.iloc[iloc_idx]
    if 'date' in df.columns:
        return str(row['date'])
    elif 'trade_date' in df.columns:
        return str(row['trade_date'])
    return datetime.now().strftime('%Y-%m-%d')


def _track_pending(sig, post_df, updates):
    """[Phase2 向量化] 追踪 PENDING 状态: 入场优先于失效"""
    entry = sig['entry_price']
    sl = sig['sl_price']
    tf = sig['timeframe']
    expiry_bars = PENDING_EXPIRY.get(tf, 20)
    bars_elapsed = len(post_df)
    
    if post_df.empty:
        return None
    
    # 🟢 向量化查找第一个满足条件的 bar 位置
    first_entry_pos = None
    first_sl_pos = None
    
    if entry and entry > 0:
        entry_mask = post_df['high'] >= entry
        if entry_mask.any():
            first_entry_pos = entry_mask.values.argmax()  # 第一个 True 的位置
    
    if sl and sl > 0:
        sl_mask = post_df['low'] <= sl
        if sl_mask.any():
            first_sl_pos = sl_mask.values.argmax()
    
    # 入场优先于失效（如果同一根K线同时触发，以入场为准）
    if first_entry_pos is not None and (first_sl_pos is None or first_entry_pos <= first_sl_pos):
        bar_date = _get_bar_date(post_df, first_entry_pos)
        updates['status'] = 'ACTIVE'
        updates['activated_date'] = bar_date
        updates['max_favorable'] = entry
        updates['max_adverse'] = entry
        logger.info(f"🎯 {sig['name']}({sig['code']}) 入场触发！入场价 {entry:.2f}")
        return updates
    
    if first_sl_pos is not None:
        bar_date = _get_bar_date(post_df, first_sl_pos)
        updates['status'] = 'INVALIDATED'
        updates['resolved_date'] = bar_date
        updates['exit_price'] = post_df.iloc[first_sl_pos]['low']
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
    
    # 🟢 [Phase2 向量化] 判断 SL 和 TP 谁先触达（止损优先于止盈 — 保守原则）
    first_sl_pos = None
    first_tp_pos = None
    
    if sl and sl > 0:
        sl_mask = active_df['low'] <= sl
        if sl_mask.any():
            first_sl_pos = sl_mask.values.argmax()
    
    if tp and tp > 0:
        tp_mask = active_df['high'] >= tp
        if tp_mask.any():
            first_tp_pos = tp_mask.values.argmax()
    
    # 止损优先（同一根K线同时触发，以止损为准）
    if first_sl_pos is not None and (first_tp_pos is None or first_sl_pos <= first_tp_pos):
        updates['status'] = 'LOSS'
        updates['exit_price'] = sl
        updates['resolved_date'] = _get_bar_date(active_df, first_sl_pos)
        updates['bars_to_resolve'] = bars_elapsed
        logger.info(f"🔴 {sig['name']}({sig['code']}) 止损 @ {sl:.2f}")
        return updates
    
    if first_tp_pos is not None:
        updates['status'] = 'WIN'
        updates['exit_price'] = tp
        updates['resolved_date'] = _get_bar_date(active_df, first_tp_pos)
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
    
    # Step 2: 获取所有信号 (按状态)
    try:
        with get_db_connection() as conn:
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]
            # 过滤逻辑 (V9.8):
            # 1. 活跃的持仓 (ACTIVE) 始终显示
            # 2. 等待入场 (PENDING) 始终显示 (因为周线Gap属于结构性机会，有效性不依附于单根K线时间，而是根据空间有效性即Gap是否被回补)
            # 3. 结算信号 (WIN/LOSS/INVALIDATED) 仅显示今天刚结算的
            today_dt = datetime.now()
            today_str = today_dt.strftime('%Y-%m-%d')
            
            all_rows = conn.execute(
                "SELECT * FROM signal_archive "
                "WHERE status IN ('PENDING', 'ACTIVE') "
                "   OR (status IN ('WIN', 'LOSS', 'INVALIDATED') AND resolved_date >= ?) "
                "ORDER BY ev_score DESC", (today_str,)
            ).fetchall()
            
            # 本月已结算统计
            month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')
            resolved_rows = conn.execute(
                "SELECT status, entry_price, sl_price, exit_price FROM signal_archive "
                "WHERE status IN ('WIN','LOSS') AND resolved_date >= ?", (month_start,)
            ).fetchall()
    except Exception as e:
        logger.error(f"仪表盘查询失败: {e}")
        return
    
    all_signals = [dict(zip(col_names, r)) for r in all_rows]
    
    # Step 3: 为所有信号获取最新价 + 分组
    enriched = []  # 所有带最新价的信号
    for sig in all_signals:
        rating = _simplify_rating(sig.get('ev_rating', ''))
        current_price = _get_latest_price(sig['code'], sig['timeframe'])
        entry = sig['entry_price'] or 0
        sl = sig['sl_price'] or 0
        tp = sig['tp_price'] or 0
        
        item = {
            'name': sig['name'] or sig['code'],
            'code': sig['code'],
            'rating': rating,
            'status': sig['status'],
            'entry': entry, 'sl': sl, 'tp': tp,
            'current': current_price or 0,
            'ev_rating': sig.get('ev_rating', ''),
            'ev_score': sig.get('ev_score', 0),
            'strategy': sig.get('strategy', ''),
        }
        
        # 计算关键指标
        if current_price and entry > 0:
            item['pnl_pct'] = (current_price - entry) / entry * 100
            item['dist_entry_pct'] = (entry - current_price) / current_price * 100
            item['dist_tp'] = (tp - current_price) / current_price * 100 if tp > 0 else 0
            item['dist_sl'] = (current_price - sl) / current_price * 100 if sl > 0 else 0
        else:
            item['pnl_pct'] = 0
            item['dist_entry_pct'] = 0
            item['dist_tp'] = 0
            item['dist_sl'] = 0
        
        enriched.append(item)
    
    # 状态优先级映射 (数字越小优先级越高，绝对保护真实持仓不被 PENDING 覆盖)
    priority_map = {
        'ACTIVE': 1,
        'PENDING': 2,
        'WIN': 3,
        'LOSS': 3,
        'INVALIDATED': 4
    }
    
    # 根据优先级 + ev_score(降序) 对整合列表进行排序
    enriched.sort(key=lambda x: (
        priority_map.get(x['status'], 99), 
        -x.get('ev_score', 0)
    ))
    
    # 去重逻辑: 此时排名第一的肯定是优先级最高(如 ACTIVE)且分数最高的记录
    seen_codes = set()
    dedup_enriched = []
    for s in enriched:
        if s['code'] not in seen_codes:
            dedup_enriched.append(s)
            seen_codes.add(s['code'])
            
    enriched = dedup_enriched
    
    # 按维度分组
    active_all = [s for s in enriched if s['status'] == 'ACTIVE']
    pending_all = [s for s in enriched if s['status'] == 'PENDING']
    invalidated_all = [s for s in enriched if s['status'] == 'INVALIDATED']
    win_all = [s for s in enriched if s['status'] == 'WIN']
    loss_all = [s for s in enriched if s['status'] == 'LOSS']
    
    # A+ 分组 (跨所有状态)
    aplus_active = [s for s in active_all if s['rating'] == 'A+']
    aplus_pending = sorted([s for s in pending_all if s['rating'] == 'A+'], key=lambda x: x['dist_entry_pct'])
    aplus_invalidated = [s for s in invalidated_all if s['rating'] == 'A+']
    aplus_total = len(aplus_active) + len(aplus_pending) + len(aplus_invalidated)
    
    # 非 A+ 入场
    other_active = [s for s in active_all if s['rating'] != 'A+']
    other_active.sort(key=lambda x: x['pnl_pct'], reverse=True)
    
    # 月度统计
    wins_count = sum(1 for r in resolved_rows if r[0] == 'WIN')
    losses_count = sum(1 for r in resolved_rows if r[0] == 'LOSS')
    total_resolved = wins_count + losses_count
    wr = wins_count / total_resolved * 100 if total_resolved > 0 else 0
    r_list = []
    for r in resolved_rows:
        e, s, ex = r[1] or 0, r[2] or 0, r[3] or 0
        risk = e - s if e and s else 1
        if risk > 0:
            r_list.append((ex - e) / risk)
    avg_r = sum(r_list) / len(r_list) if r_list else 0
    
    # =====================================================================
    # Step 4: 控制台输出 — 先概述 → 再 A+ → 再入场 → 再其他
    # =====================================================================
    weekday_map = {0: '周一', 1: '周二', 2: '周三', 3: '周四', 4: '周五', 5: '周六', 6: '周日'}
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')
    weekday = weekday_map.get(today.weekday(), '')
    
    print(f"\n{'═' * 58}")
    print(f"  📊 周线信号追踪 | {today_str} ({weekday})")
    print(f"{'═' * 58}")
    
    # 📌 概览 (一行)
    print(f"\n  📌 概览: 追踪 {len(enriched)} | "
          f"入场 {len(active_all)} | 等待 {len(pending_all)} | "
          f"失效 {len(invalidated_all)} | "
          f"胜 {wins_count} 负 {losses_count}")
    
    # 🌟🌟 A+ 极品动态 — 主角
    if aplus_total > 0:
        gap_ok = len(aplus_active) + len(aplus_pending)
        gap_dead = len(aplus_invalidated)
        print(f"\n  🌟🌟 A+ 极品动态 ({aplus_total}只)")
        print(f"  ┃ 缺口完好: {gap_ok}只 | 缺口已补: {gap_dead}只")
        
        if aplus_active:
            print(f"  ┃")
            print(f"  ┃ 🎯 已入场 ({len(aplus_active)}只):")
            for s in aplus_active:
                dist_tp_desc = f"距止盈 {s['dist_tp']:.0f}%" if s['dist_tp'] > 0 else "已达止盈区"
                print(f"  ┃  {s['name']} | 现价 {s['current']:.2f} | 浮盈 {s['pnl_pct']:+.1f}% | {dist_tp_desc}")
        
        if aplus_pending:
            print(f"  ┃")
            close_p = [s for s in aplus_pending if s['dist_entry_pct'] < 5]
            far_p = [s for s in aplus_pending if s['dist_entry_pct'] >= 5]
            if close_p:
                print(f"  ┃ 🔥 快要入场 ({len(close_p)}只):")
                for s in close_p:
                    print(f"  ┃  {s['name']} | {_format_entry_distance(s['dist_entry_pct'])} → 入场 {s['entry']:.2f}")
            if far_p:
                print(f"  ┃ ⏳ 等待中 ({len(far_p)}只):")
                for s in far_p:
                    print(f"  ┃  {s['name']} | {_format_entry_distance(s['dist_entry_pct'])}")
        
        if aplus_invalidated:
            print(f"  ┃")
            names = " / ".join([s['name'] for s in aplus_invalidated])
            print(f"  ┃ 💀 已失效 ({len(aplus_invalidated)}只): {names}")
    
    # 🎯 非 A+ 入场汇总
    if other_active:
        print(f"\n  🎯 其他入场 ({len(other_active)}只)")
        # 只展示盈亏前3
        profit_ones = [s for s in other_active if s['pnl_pct'] >= 0]
        loss_ones = [s for s in other_active if s['pnl_pct'] < 0]
        if profit_ones:
            top3 = profit_ones[:3]
            print(f"  浮盈: " + " | ".join([f"{s['name']} {s['pnl_pct']:+.0f}%" for s in top3]))
        if loss_ones:
            loss_ones.sort(key=lambda x: x['pnl_pct'])
            bot3 = loss_ones[:3]
            print(f"  浮亏: " + " | ".join([f"{s['name']} {s['pnl_pct']:+.0f}%" for s in bot3]))
    
    # 📊 尾部一行汇总
    a_pending = [s for s in pending_all if s['rating'] == 'A']
    other_pending = [s for s in pending_all if s['rating'] not in ('A+', 'A')]
    rest_parts = []
    if a_pending:
        rest_parts.append(f"A级等待 {len(a_pending)}只")
    if other_pending:
        rest_parts.append(f"B/C级等待 {len(other_pending)}只")
    if invalidated_all:
        non_aplus_inv = len(invalidated_all) - len(aplus_invalidated)
        if non_aplus_inv > 0:
            rest_parts.append(f"其他失效 {non_aplus_inv}只")
    if rest_parts:
        print(f"\n  📊 " + " | ".join(rest_parts))
    
    if total_resolved > 0:
        print(f"  📈 本月: 胜{wins_count} 负{losses_count} | 胜率{wr:.0f}% | {avg_r:+.2f}R")
    print(f"{'═' * 58}\n")
    
    # =====================================================================
    # Step 5: Discord 按状态分类推送 (V9.3 重构)
    # =====================================================================
    _push_dashboard_discord(
        enriched=enriched,
        win_all=win_all, loss_all=loss_all,
        invalidated_all=invalidated_all,
        active_all=active_all, pending_all=pending_all,
        wins_count=wins_count, losses_count=losses_count,
        wr=wr, avg_r=avg_r, today_str=today_str, weekday=weekday
    )


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
        
        from tools.notifier import send_discord_message
        send_discord_message(msg)
        
        logger.info(f"📤 {name}({code}) [{status}] 文字战报已推送 Discord (无图模式)")
        
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


def _push_dashboard_discord(enriched, win_all, loss_all, invalidated_all,
                             active_all, pending_all,
                             wins_count, losses_count, wr, avg_r, today_str, weekday):
    """
    [V9.6] Discord 纯文本·异动高亮推送
    
    推送顺序: 概览 → 🟢 止盈 → 🔴 止损 → 💀 失效 → 🎯 持仓(异动置顶) → ⏳ 等待(异动置顶)
    核心逻辑: 彻底放弃图表推送, 改为抓取距离关键阈值极近的核心标的进行异动追踪。
    """
    try:
        from tools.notifier import send_discord_message
    except ImportError:
        logger.warning("Discord notifier 不可用")
        return
    
    import time as _time
    
    # ── Helper: 分组纯文字异动排序推送 ──
    def _push_status_group(signals, status_icon, status_title):
        """为一个状态组生成文字战报"""
        if not signals:
            return
            
        if status_title == '持仓中':
            # 对于持仓中，分为浮盈和浮亏两组
            profit_items = []
            loss_items = []
            
            for s in signals:
                name = s['name']
                rating = s.get('rating', '?')
                code = s['code']
                pnl = s.get('pnl_pct', 0)
                dist_tp = s.get('dist_tp', 999)
                dist_sl = s.get('dist_sl', 999)
                
                # 确定详情文字 (区分异动和普通)
                if dist_tp > 0 and dist_tp < 5:
                    detail = f"现价{s['current']:.2f} | 浮盈{pnl:+.1f}% | 🎯 **逼近止盈 (相距 {dist_tp:.1f}%)**"
                elif dist_sl > 0 and dist_sl < 5:
                    detail = f"现价{s['current']:.2f} | 浮亏{pnl:+.1f}% | ⚠️ **逼近止损 (相距 {dist_sl:.1f}%)**"
                else:
                    detail = f"现价{s['current']:.2f} | 浮动 {pnl:+.1f}%"
                    
                line = f"  [{rating}] {name}({code}) | {detail}"
                
                if pnl >= 0:
                    profit_items.append((s, line))
                else:
                    loss_items.append((s, line))
                    
            # 排序：浮盈按盈利率降序 (越赚越靠前)
            profit_items.sort(key=lambda x: x[0].get('pnl_pct', 0), reverse=True)
            # 排序：浮亏按亏损率升序 (负数越小，绝对值越大，越亏越靠前)
            loss_items.sort(key=lambda x: x[0].get('pnl_pct', 0))
            
            profit_msgs = [item[1] for item in profit_items]
            loss_msgs = [item[1] for item in loss_items]
            
            # 智能折叠 (合并 profit 和 loss 进行折叠计算)
            msg_lines_header = [f"{status_icon} **{status_title} ({len(signals)}只)**"]
            
            if profit_msgs:
                msg_lines_header.append("\n  >>> **📈 浮盈榜** <<<")
                
            ellipsis_str = "  ... [内容过长，部分浮动居中的标的已折叠] ..."
            
            # 将内容组合以计算总长度
            # 注意：我们将 profit_msgs (前面是最高盈) 和 loss_msgs_with_header (后面是最大亏) 拼在一起
            loss_header = "\n  >>> **📉 浮亏榜** <<<"
            all_content_lines = msg_lines_header + profit_msgs
            if loss_msgs:
                all_content_lines.append(loss_header)
                all_content_lines.extend(loss_msgs)
                
            test_content = "\n".join(all_content_lines)
            
            if len(test_content) > 1850:
                base_len = len("\n".join(msg_lines_header)) + (len(loss_header) if loss_msgs else 0) + len(ellipsis_str) + 2
                allowed_chars = 1850 - base_len
                
                final_profit_msgs = []
                final_loss_msgs = []
                current_len = 0
                
                # 双指针：left 从浮盈最大开始取，right 从浮亏最大(loss_msgs[0])开始取
                p_idx, l_idx = 0, 0
                
                while p_idx < len(profit_msgs) or l_idx < len(loss_msgs):
                    added_something = False
                    
                    if p_idx < len(profit_msgs):
                        len_p = len(profit_msgs[p_idx]) + 1
                        if current_len + len_p <= allowed_chars:
                            final_profit_msgs.append(profit_msgs[p_idx])
                            current_len += len_p
                            p_idx += 1
                            added_something = True
                            
                    if l_idx < len(loss_msgs):
                        len_l = len(loss_msgs[l_idx]) + 1
                        if current_len + len_l <= allowed_chars:
                            final_loss_msgs.append(loss_msgs[l_idx])
                            current_len += len_l
                            l_idx += 1
                            added_something = True
                            
                    if not added_something:
                        break  # 容量耗尽
                
                msg_lines_header.extend(final_profit_msgs)
                if p_idx < len(profit_msgs) or l_idx < len(loss_msgs):
                     msg_lines_header.append(ellipsis_str)
                if loss_msgs:
                    msg_lines_header.append(loss_header)
                    msg_lines_header.extend(final_loss_msgs)
            else:
                msg_lines_header.extend(profit_msgs)
                if loss_msgs:
                    msg_lines_header.append(loss_header)
                    msg_lines_header.extend(loss_msgs)
                    
            msg = "\n".join(msg_lines_header) + "\n"
                
        else:
            # 原有的非持仓状态逻辑 (等待入场, 止盈, 止损, 失效) 保持异动分类
            urgent_msgs = []
            normal_msgs = []
            
            for s in signals:
                name = s['name']
                rating = s.get('rating', '?')
                code = s['code']
                
                is_urgent = False
                detail = ""
                
                if status_title == '止盈达成':
                    detail = f"入场{s['entry']:.2f} ➔ **止盈{s['tp']:.2f}**"
                elif status_title == '触发止损':
                    detail = f"入场{s['entry']:.2f} ➔ **止损{s['sl']:.2f}**"
                elif status_title == '缺口失效':
                    detail = f"SL **{s['sl']:.2f}** 已击穿"
                elif status_title == '等待入场':
                    dist_entry = s.get('dist_entry_pct', 999)
                    if dist_entry > 0 and dist_entry < 3:
                        is_urgent = True
                        detail = f"距离入场只差 **{dist_entry:.1f}%** ➔ 入场位 {s['entry']:.2f} 🔥"
                    else:
                        detail = f"{_format_entry_distance(dist_entry)} ➔ 入场位 {s['entry']:.2f}"
                
                line = f"  [{rating}] {name}({code}) | {detail}"
                
                if is_urgent:
                    urgent_msgs.append(line)
                else:
                    normal_msgs.append(line)
            
            # 拼装头部报文 (异动永远置于最上方)
            msg_lines_header = [f"{status_icon} **{status_title} ({len(signals)}只)**"]
            
            if urgent_msgs:
                msg_lines_header.append("\n  >>> **🔥 异动追踪区** <<<")
                msg_lines_header.extend(urgent_msgs)
                msg_lines_header.append("  ------------------------")
                
            if normal_msgs:
                # 智能折叠中间的内容以防止 Discord 截断
                ellipsis_str = "  ... [内容过长，部分浮动居中的标的已折叠] ..."
                test_content = "\n".join(msg_lines_header + normal_msgs)
                
                if len(test_content) > 1850:
                    allowed_chars = 1850 - len("\n".join(msg_lines_header)) - len(ellipsis_str) - 2
                    if allowed_chars < 50:
                        normal_msgs = [ellipsis_str]
                    else:
                        head_msgs = []
                        tail_msgs = []
                        current_len = 0
                        left, right = 0, len(normal_msgs) - 1
                        
                        while left <= right:
                            len_l = len(normal_msgs[left]) + 1
                            if current_len + len_l > allowed_chars:
                                break
                            head_msgs.append(normal_msgs[left])
                            current_len += len_l
                            left += 1
                            
                            if left > right: break
                                
                            len_r = len(normal_msgs[right]) + 1
                            if current_len + len_r > allowed_chars:
                                break
                            tail_msgs.insert(0, normal_msgs[right])
                            current_len += len_r
                            right -= 1
                            
                        normal_msgs = head_msgs + [ellipsis_str] + tail_msgs
                        
                msg_lines_header.extend(normal_msgs)
                
            msg = "\n".join(msg_lines_header) + "\n"
        
        try:
            send_discord_message(msg)
        except Exception as e:
            logger.warning(f"Discord {status_title} 文字推送失败: {e}")
            return
        
        _time.sleep(1)
    
    # ══════════════════════════════════════════════════════
    # 消息 1: 📊 概览
    # ══════════════════════════════════════════════════════
    msg1 = f"📊 **信号追踪 | {today_str} ({weekday})**\n"
    msg1 += f"━━━━━━━━━━━━━━━━\n"
    msg1 += f"追踪 {len(enriched)}"
    msg1 += f" | 🎯入场 {len(active_all)}"
    msg1 += f" | ⏳等待 {len(pending_all)}"
    msg1 += f" | 💀失效 {len(invalidated_all)}\n"
    if wins_count + losses_count > 0:
        msg1 += f"📈 本月: 🟢胜{wins_count} 🔴负{losses_count} | 胜率{wr:.0f}% | {avg_r:+.2f}R\n"
    msg1 += f"🟢止盈 {len(win_all)}只 | 🔴止损 {len(loss_all)}只\n"
    
    try:
        send_discord_message(msg1)
    except Exception as e:
        logger.warning(f"Discord 概览推送失败: {e}")
        return
    
    # ══════════════════════════════════════════════════════
    # 按状态分组纯文本推送 (置顶异动)
    # ══════════════════════════════════════════════════════
    _push_status_group(pending_all,      '⏳', '等待入场')
    _push_status_group(win_all,          '🟢', '止盈达成')
    _push_status_group(loss_all,         '🔴', '触发止损')
    _push_status_group(active_all,       '🎯', '持仓中')
    _push_status_group(invalidated_all,  '💀', '缺口失效')
    
    logger.info("✅ 仪表盘已推送 Discord (V9.6 查无图·聚焦异动模式)")


# =====================================================================
# P2: WatchlistManager 兼容层
# =====================================================================
# 状态映射: JSON Watchlist → SQLite Signal Tracker
_STATUS_MAP_JSON_TO_SQL = {
    'NEW': 'PENDING',
    'WATCHING': 'PENDING',
    'UPDATED': 'PENDING',
    'TRIGGERED': 'ACTIVE',
    'INVALIDATED': 'INVALIDATED',
    'EXPIRED': 'EXPIRED',
}
_STATUS_MAP_SQL_TO_JSON = {
    'PENDING': 'WATCHING',     # PENDING 在 Watchlist 视角 = 等待/观察中
    'ACTIVE': 'TRIGGERED',     # ACTIVE = 已入场触发
    'WIN': 'TRIGGERED',        # WIN = 已触发后止盈
    'LOSS': 'INVALIDATED',     # LOSS = 已触发后止损
    'EXPIRED': 'EXPIRED',      # 过期
    'INVALIDATED': 'INVALIDATED',
}


def check_signal_exists(code: str, timeframe: str = 'daily') -> bool:
    """
    检查指定代码是否存在信号记录。

    Args:
        code: 股票代码
        timeframe: 时间周期 (daily/weekly)

    Returns:
        bool: 是否存在信号
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM signal_archive WHERE code = ? AND timeframe = ? LIMIT 1",
                (code, timeframe)
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.error(f"检查信号存在性失败 {code}: {e}")
        return False


def add_signal_entry(code: str, entry: float, sl: float, score: float = 0,
                     signal_bar_idx: int = -1, date: str = '',
                     timeframe: str = 'daily', strategy: str = '') -> str:
    """
    添加信号记录 (WatchlistManager.add_signal 的兼容接口)。

    将 JSON Watchlist 的 NEW 状态映射为 SQLite 的 PENDING 状态。

    Args:
        code: 股票代码
        entry: 入场价
        sl: 止损价
        score: 评分
        signal_bar_idx: 信号K线索引
        date: 信号日期
        timeframe: 时间周期
        strategy: 策略名称

    Returns:
        str: signal_id (空字符串表示失败)
    """
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')

    if not strategy:
        strategy = 'UNKNOWN'

    signal_id = archive_signal(
        code=code, strategy=strategy, timeframe=timeframe,
        entry=entry, sl=sl, tp=0,
        signal_date=date, name='',
        signal_bar_idx=signal_bar_idx, score=score
    )
    return signal_id


def get_signal_status(code: str, timeframe: str = 'daily') -> str:
    """
    获取指定代码的最新信号状态 (映射为 JSON Watchlist 的状态名)。

    Args:
        code: 股票代码
        timeframe: 时间周期

    Returns:
        str: JSON Watchlist 状态 (NEW/WATCHING/TRIGGERED/INVALIDATED/EXPIRED)
             空字符串表示无记录
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT status FROM signal_archive WHERE code = ? AND timeframe = ? "
                "ORDER BY scan_date DESC LIMIT 1",
                (code, timeframe)
            ).fetchone()
            if row is None:
                return ''
            sql_status = row[0]
            return _STATUS_MAP_SQL_TO_JSON.get(sql_status, 'WATCHING')
    except Exception as e:
        logger.error(f"获取信号状态失败 {code}: {e}")
        return ''


def get_signals_by_status(statuses: list, timeframe: str = None) -> dict:
    """
    按状态筛选信号，返回 {code: data_dict} 格式 (兼容 WatchlistManager.get_by_status)。

    Args:
        statuses: JSON Watchlist 状态列表 (如 ['NEW', 'WATCHING'])
        timeframe: 可选时间周期过滤

    Returns:
        dict: {code: {status, entry, sl, score, signal_bar_idx, ...}}
    """
    init_signal_archive()

    # 将 JSON 状态映射为 SQL 状态
    sql_statuses = set()
    for s in statuses:
        mapped = _STATUS_MAP_JSON_TO_SQL.get(s, None)
        if mapped:
            sql_statuses.add(mapped)
    if not sql_statuses:
        return {}

    try:
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(sql_statuses))
            params = list(sql_statuses)
            query = f"SELECT * FROM signal_archive WHERE status IN ({placeholders})"
            if timeframe:
                query += " AND timeframe = ?"
                params.append(timeframe)
            query += " ORDER BY scan_date DESC"

            rows = conn.execute(query, params).fetchall()
            col_names = [desc[0] for desc in conn.execute("SELECT * FROM signal_archive LIMIT 0").description]

            result = {}
            seen_codes = set()
            for row in rows:
                sig = dict(zip(col_names, row))
                code = sig['code']
                # 去重：每个 code 只保留最新记录
                if code in seen_codes:
                    continue
                seen_codes.add(code)

                sql_status = sig['status']
                json_status = _STATUS_MAP_SQL_TO_JSON.get(sql_status, 'WATCHING')

                result[code] = {
                    'status': json_status,
                    'entry': sig.get('entry_price', 0) or 0,
                    'sl': sig.get('sl_price', 0) or 0,
                    'score': sig.get('ev_score', 0) or 0,
                    'signal_bar_idx': -1,
                    'signal_date': sig.get('signal_date', ''),
                    'added_date': sig.get('scan_date', ''),
                    'days_watching': 0,
                }
            return result
    except Exception as e:
        logger.error(f"按状态获取信号失败: {e}")
        return {}


def update_signal_entry(code: str, signal_bar_idx: int = -1, entry: float = None,
                        timeframe: str = 'daily') -> bool:
    """
    更新信号记录的入场价和信号K线索引 (WatchlistManager.update_signal_bar 兼容接口)。

    Args:
        code: 股票代码
        signal_bar_idx: 新的信号K线索引
        entry: 新的入场价
        timeframe: 时间周期

    Returns:
        bool: 是否更新成功
    """
    init_signal_archive()
    try:
        with get_db_connection() as conn:
            updates = []
            params = []

            if entry is not None:
                updates.append("entry_price = ?")
                params.append(entry)

            updates.append("updated_at = ?")
            params.append(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

            if not updates:
                return False

            params.append(code)
            params.append(timeframe)
            query = f"UPDATE signal_archive SET {', '.join(updates)} WHERE code = ? AND timeframe = ?"
            conn.execute(query, params)
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"更新信号记录失败 {code}: {e}")
        return False
