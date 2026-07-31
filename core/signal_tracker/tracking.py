# -*- coding: utf-8 -*-
"""
信号追踪状态机 (Signal Tracker 职责 2)

track_signals() 检查所有 PENDING/ACTIVE 信号的最新价格, 推进状态机;
_tracking_single/_track_pending/_track_active/_update_signal 为内部推进逻辑;
_get_bar_date 为日期提取辅助。
拆分自 core/signal_tracker.py (P11)。
"""

from datetime import datetime

from core.database import init_signal_archive
import core.data_provider as dp

from ._shared import logger, PENDING_EXPIRY, ACTIVE_EXPIRY


def track_signals(timeframe=None):
    """
    检查所有 PENDING/ACTIVE 信号的最新价格, 推进状态机。
    
    🟢 V9.3: 不再逐个推送结算通知, 改为收集事件列表, 由仪表盘统一按状态分组推送。
    
    Returns:
        dict: {'updated': N, 'activated': N, 'wins': N, 'losses': N, 'expired': N}
    """
    init_signal_archive()
    stats = {'updated': 0, 'activated': 0, 'wins': 0, 'losses': 0, 'expired': 0}

    # P11: 通过包命名空间延迟解析 get_db_connection,
    # 保证 test_phase1_regression 的 patch('core.signal_tracker.get_db_connection')
    # 仍能命中本函数 (原单文件模块靠模块级全局名解析, 拆包后需显式走包命名空间)
    from core.signal_tracker import get_db_connection

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

    # [P0-2.5] 生命周期计数: 周线信号必须用周线 bar 数, 否则 8周≈8日 提前过期。
    # 触发判断仍用日线 post_df (实时性), 仅过期判断用正确的 lifecycle_bars。
    if tf == 'weekly':
        try:
            wdf = dp.get_stock_data_weekly(code, limit=200)
            if wdf is not None and not wdf.empty:
                wdate_col = 'trade_date' if 'trade_date' in wdf.columns else ('date' if 'date' in wdf.columns else None)
                if wdate_col:
                    wdf[wdate_col] = wdf[wdate_col].astype(str)
                    lifecycle_bars = len(wdf[wdf[wdate_col] > sig['signal_date']])
                else:
                    lifecycle_bars = len(post_df)
            else:
                lifecycle_bars = len(post_df)
        except Exception:
            lifecycle_bars = len(post_df)
    else:
        lifecycle_bars = len(post_df)

    updates = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    if status == 'PENDING':
        return _track_pending(sig, post_df, updates, lifecycle_bars)
    elif status == 'ACTIVE':
        return _track_active(sig, post_df, updates, lifecycle_bars)

    return None


def _get_bar_date(df, iloc_idx):
    """[Phase2 辅助] 从 DataFrame 行中提取日期字符串"""
    row = df.iloc[iloc_idx]
    if 'date' in df.columns:
        return str(row['date'])
    elif 'trade_date' in df.columns:
        return str(row['trade_date'])
    return datetime.now().strftime('%Y-%m-%d')


def _track_pending(sig, post_df, updates, lifecycle_bars):
    """[Phase2 向量化] 追踪 PENDING 状态: 入场优先于失效"""
    entry = sig['entry_price']
    sl = sig['sl_price']
    tf = sig['timeframe']
    expiry_bars = PENDING_EXPIRY.get(tf, 20)
    bars_elapsed = lifecycle_bars
    
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


def _track_active(sig, post_df, updates, lifecycle_bars):
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
    
    bars_elapsed = lifecycle_bars
    
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
