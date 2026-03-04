# -*- coding: utf-8 -*-
import sys
import os
import time
import logging
import psutil
import gc
import queue
import threading
import re
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 防止绘图卡死
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# 路径设置
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from config import settings
from core.api_client import query_deepseek
from core.formatter import format_for_ai, parse_response
from core.calculator import add_indicators

# 引用 data_manager 里的标准函数名
import core.data_provider as data_provider  # Runtime lookup

from core.scanner import run_scanner
from tools.notifier import fetch_stock_name, generate_chart_bytes, stitch_images, send_discord_message, send_discord_image
from tools.journal import init_journal_db, log_hunter_decision

DEBUG_MODE = False


def process_ai_daily(scanner_result: Dict[str, any]) -> Tuple[bool, str]:
    """[Phase 3] AI 日线级别筛选 (process_ai_daily)
    
    Args:
        scanner_result: 包含股票代码和数据的字典
        
    Returns:
        tuple: (是否通过, 拒绝理由或通过标识)
    """
    code = scanner_result['code']
    
    if 'df' in scanner_result:
        # 🟢 Optimize: Avoid recalculating if already done (Scanner passes valid indicators)
        if 'ema20' not in scanner_result['df'].columns:
             scanner_result['df'] = add_indicators(scanner_result['df'])
        
    try:
        # [Strategy Pattern]
        from core.strategy_registry import StrategyRegistry
        strat_name = scanner_result.get('strategy_name', 'HUNTER_V1')
        strategy = StrategyRegistry.get_strategy(strat_name)
        
        # Context data for strategy
        context_data = {
            'code': code,
            'df': scanner_result['df']
        }
        
        prompt = strategy.format_prompt(context_data)
    except Exception as e:
        logger.error(f"Strategy {strat_name} Prompt Error for {code}: {e}")
        return False, "格式化错误"
        
    # AI 请求
    response_text = query_deepseek(prompt)
    
    # 解析响应 (优先使用策略自带的解析器，支持 XML 结构)
    if hasattr(strategy, 'parse_result'):
        parsed = strategy.parse_result(response_text)
    else:
        parsed = parse_response(response_text)
        
    verdict = parsed.get('verdict', 'ERROR')
    reason = parsed.get('reason', '无具体理由')
    
    # 保存原始数据供后续图表使用
    scanner_result['ai_parsed'] = parsed
    scanner_result['ai_daily_view'] = response_text # 保留原始文本作为备份
    
    # 判定逻辑
    verdict_upper = verdict.upper()
    if "PASS" in verdict_upper or "TAKE TRADE" in verdict_upper or "YES" in verdict_upper:
        return True, reason
        
    return False, reason


def prepare_daily_chart(stage1_item: Dict[str, any], passed: bool = True, reason: str = "") -> Tuple[Optional[Dict[str, any]], Optional[bytes], str]:
    """[Phase 4] 准备日线图表 (支持通过和拒绝两种状态)
    
    Args:
        stage1_item: 包含初步筛选结果的字典
        passed: AI 是否通过
        reason: AI 拒绝或通过的具体理由
        
    Returns:
        tuple: (处理后的项目, 图片缓冲区, 结果描述)
    """
    code = stage1_item['code']
    name = fetch_stock_name(code)
    
    # 构造结果
    stage1_item['name_cn'] = name
    stage1_item['final_reason'] = reason if reason else ("通过" if passed else "拒绝")
    
    try:
        # 🟢 优先级：1. 传入的 reason, 2. 解析出的理由, 3. 原始文本
        raw_reason = reason
        if not raw_reason:
            raw_reason = stage1_item.get('ai_parsed', {}).get('reason', "")
        if not raw_reason:
            raw_reason = stage1_item.get('ai_daily_view', "N/A")

        # 1. 强力清洗所有 XML 标签 (针对用户反馈的 <ANALYSIS> 没内容问题)
        clean_view = re.sub(r"<[^>]+>", "", raw_reason).strip()
        # 2. 去除常见的开头确认词
        clean_view = re.sub(r"^(YES|PASS|FAIL|REJECT|VERDICT|通过|拒绝|日线拒绝)[:\.\s]*", "", clean_view, flags=re.IGNORECASE).strip()
        
        # 3. 如果包含换行，尝试保留第一段主要内容
        lines = [l.strip() for l in clean_view.split('\n') if l.strip()]
        if lines:
            clean_view = lines[0]
            # 如果第一行只是个标题（比如 "分析:"），那么拿第二行
            titles = ["分析:", "理由:", "观点:", "PA观点:", "原因:", "Analysis:", "Reason:"]
            if (len(clean_view) < 10 or any(clean_view.startswith(t) for t in titles)) and len(lines) > 1:
                clean_view = lines[1]
        
        # 4. 限制长度
        if len(clean_view) > 100:
            clean_view = clean_view[:97] + "..."
        
        # 🟢 使用符号 √ 和 × (标准中文字符，支持 SimHei)
        symbol = "√" if passed else "×"
        display_reason = f"[{symbol}] {clean_view}"
        
        # 获取 TP 参数
        info = stage1_item.get('info', {})
        tp1 = info.get('tp1', 0)
        tp2 = info.get('tp2', 0)
        
        chart_buf = generate_chart_bytes(
            code, name, stage1_item['type'], info.get('sl', 0), 
            tp1=tp1, tp2=tp2, 
            reason=display_reason
        )
        return stage1_item, chart_buf, "图表生成完成"
    except Exception as e:
        logger.error(f"Chart generation failed for {code}: {e}")
        return stage1_item, None, f"绘图失败({e})"


def ai_worker(worker_id, analysis_queue, result_queue, stop_event):
    """
    Consumer Thread: Process candidates with DeepSeek
    """
    # logger.debug(f"🤖 AI Worker {worker_id} started") # Reduced noise
    
    while not stop_event.is_set() or not analysis_queue.empty():
        try:
            item = analysis_queue.get(timeout=1)
        except queue.Empty:
            continue
            
        try:
            code = item['code']
            # Stage 1: Daily
            logger.info(f"   🤖 正在分析: {code} ...")  # 🟢 新增：进度提示
            passed_s1, reason_s1 = process_ai_daily(item)
            
            # 🟢 记录 AI 日志 (审计追踪)
            ai_parsed = item.get('ai_parsed', {})
            log_hunter_decision(
                symbol=code,
                strategy_type=item.get('strategy_name', 'MTR'),
                daily_res=ai_parsed,
                intraday_res={}, # Daily mode only
                final_decision='PASS' if passed_s1 else 'REJECT',
                sl_price=item.get('info', {}).get('sl', 0)
            )

            if not passed_s1:
                logger.info(f"   ❌ {code} 日线拒绝: {reason_s1[:30]}...") # 🟢 新增：拒绝也打印
                # 🟢 即使拒绝也生成图表（用户要求）
                res_item, chart_buf, _ = prepare_daily_chart(item, passed=False, reason=reason_s1)
                result_queue.put(('FAIL', (res_item, chart_buf, f"日线拒绝: {reason_s1}")))
                continue
                
            # Stage 2: Daily Chart Generation (Pure Daily Mode)
            logger.info(f"   ✨ {code} 日线通过，准备日线图表...")
            # 🟢 修复：透传已解析的 reason_s1 确保理由显示完整
            res_item, chart_buf, reason_s2 = prepare_daily_chart(item, passed=True, reason=reason_s1)
            
            if res_item:
                logger.info(f"   🚀 {code} 准备就绪: {reason_s2}")
                result_queue.put(('PASS', (res_item, chart_buf)))
            else:
                result_queue.put(('FAIL', (item, None, reason_s2)))
                
        except Exception as e:
            logger.error(f"Worker {worker_id} task failed: {e}")
        finally:
            analysis_queue.task_done()

# ==============================================================================
# 3. 主程序入口 (并行流水线版)
# ==============================================================================
def get_market_status():
    """
    返回当前市场状态
    """
    if not data_provider.is_trading_day():
        return 'CLOSED'

    now = datetime.now()
    t = now.time()
    current_minutes = t.hour * 60 + t.minute
    
    if current_minutes < 570: return 'PRE'
    if 570 <= current_minutes < 690: return 'OPEN'
    if 690 <= current_minutes < 780: return 'LUNCH'
    if 780 <= current_minutes < 895: return 'OPEN'
    if 895 <= current_minutes <= 905: return 'CLOSING'
    if current_minutes > 905: return 'CLOSED'
    return 'CLOSED'

def run_pipeline_once(all_codes, strategies: List[str] = None, seen_signals: set = None) -> set:
    if strategies is None:
        from core.strategy_registry import StrategyRegistry
        strategies = StrategyRegistry.list_strategies()
    
    if seen_signals is None: seen_signals = set()
    new_signals = set()
    
    logger.info("\n" + "="*50)
    logger.info("🚀 阶段 1/4: 全市场行情快照 (Snapshot)")
    logger.info("="*50)

    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute
    
    if data_provider.is_trading_day() and 570 <= current_minutes < 930:
        logger.warning("\n⚠️ 【严重警告】时空错位提醒 (Time-Space Paradox)")
        logger.warning("   当前运行在盘中/尾盘时段，但 Baostock 仅提供昨日收盘数据 (T-1)。")
        logger.warning("   👉 您现在分析的是【昨天】的K线形态！")
        logger.warning("   ⛔ 此结果仅可用于【复盘验证】或【制定明日计划】，严禁直接用于今日尾盘交易！")
        logger.warning("   (程序将在 3秒 后继续...)")
        time.sleep(3)
    
    analysis_queue = queue.Queue(maxsize=5000)
    result_queue = queue.Queue()
    stop_event = threading.Event()
    
    num_ai_workers = 6
    ai_threads = []
    for i in range(num_ai_workers):
        t = threading.Thread(target=ai_worker, args=(i, analysis_queue, result_queue, stop_event))
        t.start()
        ai_threads.append(t)
        
    logger.info("\n" + "="*50)
    logger.info(f"🔭 阶段 2/4: 技术面扫描 (Scanning {len(all_codes)} 标的)")
    logger.info("="*50)
    logger.info(f"🤖 已启动 {num_ai_workers} 个 AI 分析进程 (Stage 3 准备就绪)")

    scan_count = 0
    hit_count = 0
    all_hits = []
    
    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        futures = {executor.submit(run_scanner, code, strategy_name=strategies): code for code in all_codes}
        
        for i, future in enumerate(as_completed(futures)):
            scan_count += 1
            if scan_count % 200 == 0:
                print(f"   ⏳ 扫描: {scan_count}/{len(all_codes)} | 命中: {hit_count}", end='\r')
            try:
                res = future.result()
                if res and res['code']:
                    res['strategy_name'] = res['type']
                    sig_key = f"{res['code']}_{res['type']}"
                    
                    if sig_key in seen_signals:
                        continue
                        
                    hit_count += 1
                    all_hits.append(res)
                    new_signals.add(sig_key)
                    
                    # 📥 Signal Tracker: 归档日线信号
                    try:
                        from core.signal_tracker import archive_signal
                        info = res.get('info', {})
                        archive_signal(
                            code=res['code'], strategy=res['type'], timeframe='daily',
                            entry=info.get('entry', info.get('price', 0)),
                            sl=info.get('sl', 0), tp=info.get('tp1', 0),
                            ev_rating=info.get('ev_rating', ''),
                            signal_date=info.get('signal_date', ''),
                            rr=info.get('rr', 0), name=res.get('name_cn', '')
                        )
                    except Exception:
                        pass  # 归档失败不影响主流程
            except Exception:
                continue
                
    print(f"\n✅ 扫描结束. 初步命中: {hit_count}")

    # 阶段 2.5: Watchlist 生命周期管理
    from tools.watchlist import WatchlistManager
    watchlist = WatchlistManager()
    
    status_changes = []
    # 1. 更新观察中股票的状态 (TRIGGERED/INVALIDATED)
    watching_items = list(watchlist.get_watching().items()) # copy keys
    for code, data in watching_items:
        # 优先复用 scanner 取到的数据
        hit_res = next((x for x in all_hits if x['code'] == code), None)
        if hit_res:
            df = hit_res['df']
        else:
            df = data_provider.get_stock_data(code, limit=5)
            
        if df is not None and not df.empty:
            old_status = data['status']
            new_status = watchlist.update_status(code, df)
            if new_status != old_status and new_status in ['TRIGGERED', 'INVALIDATED']:
                status_changes.append((code, new_status, data))

    # 2. 从 Scanner 结果过滤出“新”信号 (🆕 NEW)
    new_hits = []
    for res in all_hits:
        code = res['code']
        info = res['info']
        sb_idx = info.get('signal_bar_idx', -1)
        entry = info.get('entry', info.get('price', 0))
        sl = info.get('sl', 0)
        score = info.get('score', 0)
        date_val = str(res['df']['date'].iloc[-1].strftime('%Y-%m-%d')) if hasattr(res['df']['date'].iloc[-1], 'strftime') else str(res['df']['date'].iloc[-1]) if 'date' in res['df'] else ''
        
        if code in watchlist.data:
            sig_data = watchlist.data[code]
            if sig_data['status'] in ['TRIGGERED', 'INVALIDATED', 'EXPIRED']:
                # 之前已失效，但现在又有新信号，视情况作为新信号处理 (sb_idx 不同)
                if sb_idx != -1 and sb_idx != sig_data['signal_bar_idx']:
                    watchlist.add_signal(code, entry, sl, score, sb_idx, date_val)
                    new_hits.append(res)
            else:
                # 正在 WATCHING / NEW 中，检查是否更新了内部 K 线
                if sb_idx != -1 and sb_idx != sig_data['signal_bar_idx']:
                    watchlist.update_signal_bar(code, sb_idx, entry)
                    new_hits.append(res) # 内部信号更新也推一遍
        else:
            # 完全新的
            watchlist.add_signal(code, entry, sl, score, sb_idx, date_val)
            new_hits.append(res)
            
    logger.info(f"📌 Watchlist 过滤后，新增/更新信号: {len(new_hits)}")

    # ==========================
    # 覆盖 all_hits，后续只让 AI 分析新信号
    # ==========================
    all_hits = new_hits

    # 🟢 [V2.1/V3.0] 策略分流: 3K/Struct Gap 不走 AI, MTR 走 AI
    ai_candidates_raw = []
    direct_picks = []  # 存放无需 AI 审计直接上图的技术面干信号
    
    for res in all_hits:
        strat_type = res.get('type', '').upper()
        if '3K' in strat_type or 'STRUCTURAL_GAP' in strat_type:
            # 3K & Struct Gap: 直接生成图表, 跳过 AI
            reason_txt = "[V3.0] 结构突破信号" if 'STRUCTURAL' in strat_type else "[3K] 技术面信号"
            res_item, chart_buf, _ = prepare_daily_chart(res, passed=True, reason=reason_txt)
            if chart_buf:
                res_item['chart_buf'] = chart_buf
            res_item['ai_verdict'] = True
            res_item['ai_parsed'] = {'verdict': 'PASS', 'reason': f'{reason_txt} (跳过 AI)'}
            direct_picks.append(res_item)
        else:
            ai_candidates_raw.append(res)
    
    if direct_picks:
        logger.info(f"⚡ 快速通道: {len(direct_picks)} 个结构/动能信号直接入池 (跳过 AI)")

    # MTR 信号: Token Saver 排序取 Top 10
    ai_candidates_raw.sort(key=lambda x: x.get('info', {}).get('score', 0), reverse=True)
    ai_candidates = ai_candidates_raw[:10]
    skipped_candidates = ai_candidates_raw[10:]
    
    if len(ai_candidates_raw) > 10:
        logger.info(f"💰 Token 保护: MTR {len(ai_candidates_raw)} 个信号取前 10 审计")
    
    # 将 MTR 放入 AI 队列
    for res in ai_candidates:
        try:
            analysis_queue.put(res, timeout=1.0)
        except queue.Full:
            break

    # 阶段 3: AI 分析 (仅 MTR)
    logger.info("\n" + "="*50)
    mtr_count = len(ai_candidates)
    logger.info(f"🧠 阶段 3/4: DeepSeek 深度分析 (MTR: {mtr_count} | 技术面专线: 已跳过)")
    logger.info("="*50)
    
    analysis_queue.join()
    stop_event.set()
    for t in ai_threads: t.join()
    
    final_picks = []
    rejected_list = []
    
    # 处理被跳过的 MTR 候选者
    for res in skipped_candidates:
        res['ai_parsed'] = {'verdict': 'SKIP', 'reason': '分数较低，已节省 Token 跳过审计'}
        res['ai_daily_view'] = "SKIP"
        res_item, chart_buf, _ = prepare_daily_chart(res, passed=True, reason="[跳过审计] 技术面评分第10名后")
        if chart_buf: res_item['chart_buf'] = chart_buf
        final_picks.append(res_item)
    
    # 处理 AI 工人产生的结果
    while not result_queue.empty():
        type_, data = result_queue.get()
        if type_ == 'PASS':
            item, chart = data
            if chart: item['chart_buf'] = chart
            final_picks.append(item)
        elif type_ == 'FAIL':
            if len(data) == 3:
                item, chart, reason = data
                if chart: item['chart_buf'] = chart
                rejected_list.append((item, reason))
            else:
                item, reason = data
                rejected_list.append((item, reason))
            
    # 1. Top 3 优先选取 AI 通过的信号 (精准狙击原则)
    passed_candidates = []
    for p in final_picks:
        p['ai_verdict'] = True
        passed_candidates.append(p)
    
    rejected_with_reason = []
    for p, reason in rejected_list:
        p['ai_verdict'] = False
        p['ai_reject_reason'] = reason
        rejected_with_reason.append(p)

    if not passed_candidates and not rejected_with_reason and not direct_picks:
        logger.info("💤 本轮无新信号 (Scanner 0 命中)")
        send_discord_message("💤 今日全市场扫描结束，未发现符合技术面雏形的信号。")
        return new_signals

    # 🎯 MTR: AI 通过的优先排序取 Top 3
    passed_candidates.sort(key=lambda x: x.get('info', {}).get('score', 0), reverse=True)
    top_mtr = passed_candidates[:3]
    
    if not top_mtr and rejected_with_reason:
        rejected_with_reason.sort(key=lambda x: x.get('info', {}).get('score', 0), reverse=True)
        top_mtr = rejected_with_reason[:1]

    # 技术面信号: 全量推送 (按评分排序)
    direct_picks.sort(key=lambda x: x.get('info', {}).get('score', 0), reverse=True)

    logger.info("\n" + "="*50)
    logger.info(f"📨 阶段 4/4: 结果推送 (MTR Top 3 + 结构战法 全量)")
    logger.info("="*50)

    # ==============================================================
    # 4. 推送: MTR 新信号 | 3K 全量 | 观察中 | 状态变更
    # ==============================================================
    msg_lines = ["🚀 **Brooks-AI 猎手报告**\n"]
    from tools.notifier import format_mtr_alert, format_3k_alert
    
    # Block 1: 🆕 MTR 新信号 (Top 3)
    if top_mtr:
        msg_lines.append("【🎯 MTR 新信号 Top 3】")
        for p in top_mtr:
            code, name = p['code'], (p.get('name_cn') or fetch_stock_name(p['code']))
            info = p.get('info', {})
            status_icon = "✅" if p['ai_verdict'] else "❌"
            header = f"{status_icon} {name} ({code})"
            msg_lines.append(header)
            msg_lines.append(format_mtr_alert(code, name, info.get('entry', info.get('price',0)), info.get('sl',0), info.get('tp1',0), info.get('tp2',0)))
            if not p['ai_verdict']:
                clean_reason = p.get('ai_reject_reason', '').replace("日线拒绝: ", "").strip()
                short_reason = clean_reason[:30] + ("..." if len(clean_reason) > 30 else "")
                msg_lines.append(f"> AI 警告: {short_reason}")
            msg_lines.append("")
    
    # Block 2: ⚡ 技术面直通信号 (3K / Structural Gap)
    if direct_picks:
        from tools.notifier import format_structural_alert
        msg_lines.append(f"【⚡ 技术面确认信号 ({len(direct_picks)} 只)】")
        
        # Separate structural gaps for EV grouping
        sg_best = []
        sg_good = []
        sg_warn = []
        others = []
        
        for p in direct_picks:
            strat_type = p.get('type', '').upper()
            if 'STRUCTURAL' in strat_type:
                ev_rating = p.get('info', {}).get('ev_rating', '')
                if '🌟' in ev_rating: sg_best.append(p)
                elif '👍' in ev_rating: sg_good.append(p)
                elif '⚠️' in ev_rating: sg_warn.append(p)
                else: others.append(p)
            else:
                others.append(p)
                
        # Helper to format Struct Gap
        def _add_sg_lines(group_list, title):
            if group_list:
                msg_lines.append(f"  {title} ({len(group_list)}只):")
                for p in group_list:
                    code, name = p['code'], (p.get('name_cn') or fetch_stock_name(p['code']))
                    info = p.get('info', {})
                    msg_lines.append("  " + format_structural_alert(code, name, info.get('entry', info.get('price',0)), info.get('sl',0), info.get('tp1',0), ev_rating=info.get('ev_rating', 'N/A')))
        
        _add_sg_lines(sg_best, "🌟 高预期")
        _add_sg_lines(sg_good, "👍 常态")
        _add_sg_lines(sg_warn, "⚠️ 低预期")
        
        for p in others:
            code, name = p['code'], (p.get('name_cn') or fetch_stock_name(p['code']))
            info = p.get('info', {})
            strat_type = p.get('type', '').upper()
            
            if 'STRUCTURAL' in strat_type:
                msg_lines.append("  " + format_structural_alert(code, name, info.get('entry', info.get('price',0)), info.get('sl',0), info.get('tp1',0), ev_rating=info.get('ev_rating', 'N/A')))
            else:
                gt_entry = info.get('gap_test_entry', 0)
                gt_rr = info.get('gap_test_rr', 0)
                if gt_entry:
                    msg_lines.append(f"• {name} ({code}) | Buy Stop: {gt_entry:.2f} | R:R=1:{gt_rr:.1f}")
                else:
                    msg_lines.append(format_3k_alert(code, name, info.get('entry', info.get('price',0)), info.get('sl',0), info.get('tp1',0)))
        
        msg_lines.append("")
    
    if not top_mtr and not direct_picks:
        msg_lines.append("  (无新增信号)\n")

    # Block 2: 📌 观察中 (仍在 Watchlist 里的未触发股票)
    watching_now = watchlist.get_watching()
    watching_count = len(watching_now) if watching_now else 0
    
    if watching_count > 0:
        msg_lines.append("【📌 仍在观察中信号】")
        for watch_code, watch_data in watching_now.items():
            name = fetch_stock_name(watch_code)
            entry = watch_data.get('entry', 0)
            msg_lines.append(f"• 挂单有效 {name} ({watch_code}) | Buy Stop: {entry:.2f}")
        msg_lines.append("")

    # Block 3: 🚫 状态变更 (Triggered 或 Invalidated)
    if status_changes:
        msg_lines.append("【🔔 信号状态变更】")
        for st_code, st_status, st_data in status_changes:
            name = fetch_stock_name(st_code)
            if st_status == 'TRIGGERED':
                icon = "🎯"
                action = "突破买点成交，恭喜入场！"
            else:
                icon = "💔"
                action = "结构破位或止损，建议撤单观察。"
            msg_lines.append(f"• {icon} {name} ({st_code}) -> {st_status}: {action}")
        msg_lines.append("")

    msg_lines.append("-------------------")
    
    # 底部精简看板 (猎手看板)
    rejected_count = len(rejected_list) if rejected_list else 0
    msg_lines.append(f"📊 猎手看板: 伏击圈内在观望 {watching_count} 只 | AI 今日挡下 {rejected_count} 只次优形态")

    summary_text = "\n".join(msg_lines)
    send_discord_message(summary_text)

    # 5. 图表推送 (全量 - 5张一拼长图)
    from tools.notifier import generate_chart_bytes
    
    # 合并所有需要推送图表的信号
    all_chart_candidates = list(top_mtr) + list(direct_picks)
    
    # 对缺少图表的信号尝试主线程重绘
    chart_pool = []
    for p in all_chart_candidates:
        if 'chart_buf' not in p or not p['chart_buf']:
            try:
                code, name = p['code'], (p.get('name_cn') or fetch_stock_name(p['code']))
                info = p.get('info', {})
                if 'df' in p:
                    fallback_reason = p.get('ai_reject_reason', p.get('final_reason', '')).replace("日线拒绝: ", "")[:30]
                    new_buf = generate_chart_bytes(
                        code, name, p.get('type', 'MTR'), info.get('sl', 0),
                        tp1=info.get('tp1', 0), tp2=info.get('tp2', 0),
                        reason=f"[√] {fallback_reason}" if p.get('ai_verdict') else f"[×] {fallback_reason}",
                        df_override=p['df'],
                        ev_rating=info.get('ev_rating', None),
                        sig_quality=info.get('sig_quality', 0),
                        bears=info.get('pb_consec_bear', 0)
                    )
                    if new_buf:
                        p['chart_buf'] = new_buf
            except Exception as e:
                logger.error(f"❌ 重绘失败 {p['code']}: {e}")

        if 'chart_buf' in p and p['chart_buf']:
            chart_pool.append(p['chart_buf'])

    if chart_pool:
        BATCH_SIZE = getattr(settings, 'MAX_IMAGES_PER_BATCH', 5)
        logger.info(f"🎨 全量图表推送: {len(chart_pool)} 张 ({BATCH_SIZE} 张/批)")
        for batch_start in range(0, len(chart_pool), BATCH_SIZE):
            batch = chart_pool[batch_start:batch_start + BATCH_SIZE]
            merged = stitch_images(batch)
            if merged:
                send_discord_image(merged, filename=f"hunter_batch_{batch_start}.png")

    return new_signals


def _check_data_freshness():
    """检查日线/周线数据新鲜度, 返回提示信息 (无副作用, 仅读 DB)"""
    msgs = []
    try:
        from core.database import get_db_connection
        with get_db_connection() as conn:
            r = conn.execute('SELECT MAX(trade_date) FROM daily_bars').fetchone()
            if r and r[0]:
                from datetime import datetime
                last = r[0]
                today = datetime.now().strftime('%Y-%m-%d')
                if last < today:
                    # 计算滞后天数 (粗略, 不考虑节假日)
                    d1 = datetime.strptime(last, '%Y-%m-%d')
                    d2 = datetime.strptime(today, '%Y-%m-%d')
                    lag = (d2 - d1).days
                    msgs.append(f"⚠️ 日线数据停留在 {last} (滞后 {lag} 天, 建议先选 4 同步)")
                else:
                    msgs.append(f"✅ 日线数据已是最新 ({last})")
    except Exception:
        msgs.append("⚠️ 日线数据库读取失败")
    
    try:
        from core.database import get_db_connection as _gdc
        with _gdc() as conn:
            wr = conn.execute('SELECT MAX(trade_date) FROM weekly_bars').fetchone()
            if wr and wr[0]:
                msgs.append(f"  周线数据最新: {wr[0]}")
    except Exception:
        pass
    
    return msgs


def _run_data_sync():
    """数据同步子菜单 (选项 4)"""
    print("\n  选择同步周期:")
    print("  1. 📈 日线 (约5-10分钟)")
    print("  2. 📊 周线 (约3-5分钟)")
    print("  3. 🔄 全部同步 (日线+周线)")
    try:
        sync_choice = input("  请选择 (默认 3): ").strip() or '3'
    except (EOFError, KeyboardInterrupt):
        sync_choice = '3'
    
    import time
    
    if sync_choice in ('1', '3'):
        print("\n🔄 开始日线数据同步...")
        t0 = time.time()
        try:
            from core.data_provider import update_daily_data_batch
            update_daily_data_batch()
            elapsed = time.time() - t0
            print(f"✅ 日线同步完成 (耗时 {elapsed:.0f}秒)")
        except Exception as e:
            print(f"❌ 日线同步失败: {e}")
    
    if sync_choice in ('2', '3'):
        print("\n🔄 开始周线数据同步...")
        t0 = time.time()
        try:
            from core.data_provider import update_weekly_data_batch
            update_weekly_data_batch()
            elapsed = time.time() - t0
            print(f"✅ 周线同步完成 (耗时 {elapsed:.0f}秒)")
        except Exception as e:
            print(f"❌ 周线同步失败: {e}")
    
    # 同步后显示最新状态
    for msg in _check_data_freshness():
        print(f"  {msg}")


def main():
    parser = argparse.ArgumentParser(description="Brooks-AI Hunter (V9.1 - Daily & Weekly)")
    parser.add_argument('--strategy', type=str, default=None, help='Select Trading Strategy')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of stocks for testing')
    parser.add_argument('--timeframe', type=str, default=None, choices=['daily', 'weekly'], help='时间周期: daily 或 weekly')
    parser.add_argument('--weeks', type=int, default=4, help='(周线模式) 检查最近N周的信号')
    parser.add_argument('--track', action='store_true', help='追踪已归档信号的最新状态')
    parser.add_argument('--report', action='store_true', help='输出信号追踪统计报表')
    args = parser.parse_args()

    init_journal_db()
    
    # ============================================================
    # 追踪模式: python hunter.py --track [--report]
    # ============================================================
    if args.track or args.report:
        from core.signal_tracker import init_signal_archive, track_signals, generate_report, format_tracker_discord_msg
        init_signal_archive()
        if args.track:
            track_signals(timeframe=args.timeframe)
        report = generate_report(timeframe=args.timeframe)
        if report and report.get('total_resolved', 0) > 0:
            from tools.notifier import send_discord_message
            send_discord_message(format_tracker_discord_msg(report))
        return
    
    # ============================================================
    # Step 0: 主菜单 (交互式 / CLI 直通)
    # ============================================================
    # CLI 直通: 指定 --timeframe 则跳过交互菜单
    if args.timeframe:
        if args.timeframe == 'weekly':
            print(f"\n🌙 周线模式启动 (检查最近 {args.weeks} 周)")
            from tools.scanner_weekly_gap import scan_weekly_gap, _format_and_push_results
            all_codes = data_provider.get_stock_list()
            if not all_codes: print("❌ 获取股票列表失败"); return
            if args.limit > 0: all_codes = all_codes[:args.limit]
            results = scan_weekly_gap(all_codes, recent_weeks=args.weeks)
            _format_and_push_results(results)
            return
        # daily: 继续往下进入策略选择
    else:
        # 交互式主菜单
        print("\n" + "═"*40)
        print("  Brooks-AI 猎手 V9.1")
        print("═"*40)
        print("  1. 🔭 扫描新机会 (周末埋伏)")
        print("  2. 📊 信号追踪 (日常管理)")
        print("  3. 🛡️ 持仓管家 (Guardian)")
        print("  4. 🔄 数据同步")
        print("═"*40)
        try:
            mode_choice = input("请选择 (默认 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            mode_choice = '1'
        
        # 路径 2: 信号追踪仪表盘
        if mode_choice == '2':
            from core.signal_tracker import run_tracker_dashboard
            run_tracker_dashboard()
            return
        
        # 路径 3: 持仓管家
        if mode_choice == '3':
            from tools.for_hold import load_holdings_with_cost, analyze_single_stock_micro
            holdings = load_holdings_with_cost()
            if not holdings:
                print("⚠️ 持仓列表为空 (请检查 hold_list.txt)"); return
            print(f"🛡️ 持仓管家启动, {len(holdings)} 只股票...")
            for item in holdings:
                try: analyze_single_stock_micro(item)
                except Exception as e: print(f"❌ {item['code']} 分析失败: {e}")
            return
        
        # 路径 4: 数据同步
        if mode_choice == '4':
            _run_data_sync()
            return
        
        # 路径 1: 扫描 → 选时间周期
        print("\n  选择扫描周期:")
        print("  1. 日线 (Daily)")
        print("  2. 周线 (Weekly)")
        try:
            tf_choice = input("  请选择 (默认 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            tf_choice = '1'
        
        if tf_choice == '2':
            print(f"\n🌙 周线模式启动 (检查最近 {args.weeks} 周)")
            from tools.scanner_weekly_gap import scan_weekly_gap, _format_and_push_results
            all_codes = data_provider.get_stock_list()
            if not all_codes: print("❌ 获取股票列表失败"); return
            if args.limit > 0: all_codes = all_codes[:args.limit]
            results = scan_weekly_gap(all_codes, recent_weeks=args.weeks)
            _format_and_push_results(results)
            return

    # ============================================================
    # 日线路径: 原有流程 (策略选择 → run_pipeline_once)
    # ============================================================
    from core.strategy_registry import StrategyRegistry
    all_available = StrategyRegistry.list_strategies()
    
    active_strategies = []
    
    if args.strategy:
        if args.strategy.upper() == 'ALL':
            active_strategies = all_available
        else:
            active_strategies = [s.strip().upper() for s in args.strategy.split(',')]
    else:
        print("\n" + "="*40)
        print("🔍 Brooks-AI 猎手策略选择")
        print("="*40)
        for i, s in enumerate(all_available):
            print(f"  {i+1}. {s}")
        print(f"  {len(all_available)+1}. ALL (全量扫描)")
        print("="*40)
        
        try:
            choice = input(f"请输入选择序号 (默认 1 - {all_available[0]}): ").strip()
            if not choice:
                active_strategies = [all_available[0]]
            elif choice.isdigit():
                idx = int(choice)
                if idx == len(all_available) + 1:
                    active_strategies = all_available
                elif 1 <= idx <= len(all_available):
                    active_strategies = [all_available[idx-1]]
                else:
                    active_strategies = [all_available[0]]
            else:
                if choice.upper() in all_available:
                    active_strategies = [choice.upper()]
                else:
                    active_strategies = [all_available[0]]
        except (EOFError, KeyboardInterrupt):
            active_strategies = [all_available[0]]
            
    print(f"\n🚀 已激活策略: {', '.join(active_strategies)}")

    try:
        all_codes = data_provider.get_stock_list()
        if not all_codes:
            return
        if args.limit > 0:
            all_codes = all_codes[:args.limit]
        run_pipeline_once(all_codes, strategies=active_strategies)
    except KeyboardInterrupt:
        logger.info("🛑 程序已终止")
    finally:
        gc.collect()

if __name__ == "__main__":
    main()