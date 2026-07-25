# -*- coding: utf-8 -*-
"""
交互式信号追踪仪表盘 + Discord 异动推送 (Signal Tracker 职责 4)

run_tracker_dashboard(): 一站式追踪仪表盘 (Step0 数据新鲜度 → Step1 追踪 → Step2 取价分组 → Step3 控制台 → Step4 Discord 推送)。
_get_latest_price / _format_entry_distance / _simplify_rating: 仪表盘辅助。
_push_dashboard_discord(): Discord 纯文本·异动高亮推送。
拆分自 core/signal_tracker.py (P11)。
"""

from datetime import datetime

from core.database import get_db_connection, init_signal_archive
import core.data_provider as dp

from ._shared import logger
from .tracking import track_signals


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
    except Exception:
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



def _simplify_rating(rating_str):
    """'🌟🌟 极品 (A+)' → 'A+'"""
    if not rating_str:
        return '?'
    for tag in ['A+', 'A', 'B', 'C', 'D']:
        if tag in str(rating_str):
            return tag
    return '?'



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
    msg1 += f"━━━━━━━━━━━━━━\n"
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
