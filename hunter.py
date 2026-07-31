# -*- coding: utf-8 -*-
import sys
import os

# 确保控制台支持UTF-8，防止Windows GBK下打印Emoji报错
if hasattr(sys.stdout, 'reconfigure') and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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

from core.log_config import get_logger

# 配置日志
logger = get_logger(__name__)

# 防止绘图卡死
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# 路径设置
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
from core.paths import ensure_importable
ensure_importable()

from config import settings
from core.api_client import query_deepseek
from core.formatter import format_for_ai, parse_response
from core.calculator import add_indicators

# 引用 data_manager 里的标准函数名
import core.data_provider as data_provider  # Runtime lookup

from core.scanner import run_scanner, run_scanner_all
from tools.notifier import fetch_stock_name, generate_chart_bytes, stitch_images, send_discord_message, send_discord_image
from tools.journal import init_journal_db, log_hunter_decision

DEBUG_MODE = False


# ===== RATING_PLAN Phase 0: 统一评级读取辅助 =====
def _extract_rating(info: Dict) -> Dict:
    """从 info['rating'] 取统一评级契约 (score/letter/factors/...); 回退 info['score'] (legacy)."""
    rating = info.get('rating') or {}
    if not rating:
        # 兼容 Phase 0 之前仅 score 可用的情况 (防御性, 正常 compute_rating 必注入 rating)
        return {'score': float(info.get('score', 0) or 0), 'letter': 'C',
                'factors': [], 'raw_score': 0, 'toxic': False, 'calibrated': False}
    return rating


def _letter_to_ev_text(letter: str) -> str:
    """字母评级 -> 兼容中文 ev_rating 文本 (与 tools/scanner_weekly_gap 保持一致)."""
    return {
        'A+': '🌟🌟 极品 (A+)',
        'A': '🌟 高预期 (A)',
        'B': '👍 常态 (B)',
        'C': '⚠️ 低预期 (C)',
        'D': '💀 毒性 (D)',
    }.get(letter, '👍 常态 (B)')


def process_ai_daily(scanner_result: Dict[str, any]) -> Tuple[bool, str]:
    """[DeepSeek] 策略深度审计 (辅助决策)
    
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
    """准备结果图表 (渲染 PA 标注与交易参数)
    
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
            reason=display_reason,
            ev_rating=None,  # 去字母化: 图表不显示假评级字母
            rating=info.get('rating'),  # 因子证据面板由此驱动(仅显示命中因子)
            entry=info.get('entry', 0),
            sig_quality=info.get('sig_quality', 0),
            bears=info.get('pb_consec_bear', 0)
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


def _scan_market(all_codes, strategies, seen_signals):
    """
    [Phase2 重构] 阶段 1: 全市场扫描 + Signal Tracker 归档
    
    Returns:
        tuple: (all_hits, new_signals)
    """
    new_signals = set()
    
    logger.info("\n" + "="*50)
    logger.info(f"🔭 全市场技术面扫描 (Scanning {len(all_codes)} 标的)")
    logger.info("="*50)

    scan_count = 0
    hit_count = 0
    all_hits = []
    
    with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as executor:
        # 🟢 [修复 ALL 短路] 用 run_scanner_all 返回该股命中的全部策略 (不再首个即返回)
        futures = {executor.submit(run_scanner_all, code, strategies): code for code in all_codes}
        
        for i, future in enumerate(as_completed(futures)):
            scan_count += 1
            if scan_count % 200 == 0:
                print(f"   ⏳ 扫描: {scan_count}/{len(all_codes)} | 命中: {hit_count}", end='\r')
            try:
                res_list = future.result() or []
                for res in res_list:
                    if not res or not res.get('code'):
                        continue
                    res['strategy_name'] = res['type']
                    sig_key = f"{res['code']}_{res['type']}"
                    
                    if sig_key in seen_signals:
                        continue
                        
                    hit_count += 1
                    all_hits.append(res)
                    new_signals.add(sig_key)
            except KeyboardInterrupt:
                logger.info("🛑 扫描被中断，正在退出...")
                break
            except Exception:
                continue
                
    print(f"\n✅ 扫描结束. 初步命中: {hit_count}")
    return all_hits, new_signals


def _classify_signals(all_hits, analysis_queue, result_queue, stop_event, ai_threads, use_ai: bool = True):
    """
    [Phase2 重构] 阶段 2: Watchlist 生命周期 + 策略分流 + AI 审计
    
    Returns:
        tuple: (direct_picks, final_picks, rejected_list, watchlist, status_changes)
    """
    # 🟢 阶段 2.5: Watchlist 生命周期管理 (同步更新)
    from tools.watchlist import WatchlistManager
    from core.strategy_registry import StrategyRegistry
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

    # 2. 从 Scanner 结果过滤出"新"信号 (🆕 NEW)
    new_hits = []
    for res in all_hits:
        code = res['code']
        info = res['info']
        sb_idx = info.get('signal_bar_idx', -1)
        entry = info.get('entry', info.get('price', 0))
        sl = info.get('sl', 0)
        score = _extract_rating(info).get('score', 0)
        date_val = str(res['df']['date'].iloc[-1].strftime('%Y-%m-%d')) if hasattr(res['df']['date'].iloc[-1], 'strftime') else str(res['df']['date'].iloc[-1]) if 'date' in res['df'] else ''
        
        strat_type = res.get('type', '').upper()
        is_new_strategy = 'GAP_PINBAR' in strat_type or 'GAP_H2' in strat_type
        
        if is_new_strategy:
            # 新策略暂时不启用 Watchlist 去重拦截功能，始终强制作为 new_hits 允许推送，但仍做入库记录供生命周期追踪
            if code not in watchlist.data:
                watchlist.add_signal(code, entry, sl, score, sb_idx, date_val)
            else:
                watchlist.update_signal_bar(code, sb_idx, entry)
            new_hits.append(res)
        else:
            # 经典策略保持原有的去重拦截机制
            if code in watchlist.data:
                sig_data = watchlist.data[code]
                if sig_data['status'] in ['TRIGGERED', 'INVALIDATED', 'EXPIRED']:
                    if sb_idx != -1 and sb_idx != sig_data['signal_bar_idx']:
                        watchlist.add_signal(code, entry, sl, score, sb_idx, date_val)
                        new_hits.append(res)
                else:
                    if sb_idx != -1 and sb_idx != sig_data['signal_bar_idx']:
                        watchlist.update_signal_bar(code, sb_idx, entry)
                        new_hits.append(res)
            else:
                watchlist.add_signal(code, entry, sl, score, sb_idx, date_val)
                new_hits.append(res)
            
    logger.info(f"📌 Watchlist 过滤后，新增/更新信号: {len(new_hits)}")

    # [Fix] 去字母化后推送全部扫描命中, 不再用 watchlist 去重拦截推送可见性。
    # watchlist 去重仅用于上面的生命周期写库 (add_signal/update_signal_bar 已按 new_hits 执行),
    # 不应屏蔽用户对本次扫描结果的可见性 —— 否则已跟踪的稳定信号 (如 MTR 同信号K线索引恒不变)
    # 会被全部吞掉, 导致"扫描出 N 个但推送 0"。AI 审计候补已在下方按 ai_candidates_raw[:10] 限流,
    # 不会因全量而爆 token; 无 AI 模式下全部走 direct_picks 直通推送。
    # 注: 此改动是 P0-2.4 (正确解析 signal_bar_idx) 暴露的副作用修复 —— 修复前该字段恒为 -1,
    #      去重误判"已变"而每次都推; 修复后返回真实值, 稳定信号被正确判定为"未变"却被错误吞掉。

    # 🟢 [V9.5] 策略分流: MTR/3K/Struct Gap 均不再强制走 AI
    ai_candidates_raw = []
    direct_picks = []

    for res in all_hits:
        strat_type = res.get('type', '')
        # 🟢 [P1⑤] 元数据驱动 AI 跳过逻辑: 各策略在 get_metadata 声明 ai_audit,
        # 取代硬编码策略名子串匹配 (消除新增策略忘改 hunter + 子串误匹配风险)
        try:
            _ai_audit = StrategyRegistry.get_metadata(strat_type).get('ai_audit', True)
        except Exception:
            _ai_audit = True
        if not _ai_audit:
            # 🟢 [RATING_PLAN] 统一从 info['rating'] 取评级; [P0-5] 不再把经回测证明为噪声的字母
            #         渲染/写入(图表reason/info), 改用命中因子作证据。
            _info = res.get('info', {})
            from tools.notifier import factor_evidence_text
            try:
                _dn = StrategyRegistry.get_metadata(strat_type).get('display_name') or strat_type.replace('STRATEGY_', '')
            except Exception:
                _dn = strat_type.replace('STRATEGY_', '')
            _ev = factor_evidence_text(_info.get('rating'))
            reason_txt = f"{_dn} 结构确认 {_ev}" if _ev else f"{_dn} 结构确认"

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

    if not use_ai:
        # 如果不启用 AI，所有本来要走 AI 的信号，全部变成技术面直通
        for res in ai_candidates_raw:
            reason_txt = f"[{res.get('type')}] 技术面信号 (AI 审计已关闭)"
            res_item, chart_buf, _ = prepare_daily_chart(res, passed=True, reason=reason_txt)
            if chart_buf:
                res_item['chart_buf'] = chart_buf
            res_item['ai_verdict'] = True
            res_item['ai_parsed'] = {'verdict': 'PASS', 'reason': f'{reason_txt}'}
            direct_picks.append(res_item)
        ai_candidates_raw = []

    ai_candidates = ai_candidates_raw[:10]
    skipped_candidates = ai_candidates_raw[10:]
    
    if ai_candidates_raw:
        logger.info(f"🧠 AI 审计候补: {len(ai_candidates_raw)} 个信号 (非 MTR/3K/SG 策略)")
    
    for res in ai_candidates:
        try:
            analysis_queue.put(res, timeout=1.0)
        except queue.Full:
            break

    # 等待 AI 队列处理完毕
    mtr_count = len(ai_candidates)
    if mtr_count > 0:
        logger.info(f"🧠 AI 候补队列审计: {mtr_count} 项待处理...")
        analysis_queue.join()
    
    stop_event.set()
    for t in ai_threads: t.join()
    
    final_picks = []
    rejected_list = []
    
    for res in skipped_candidates:
        res['ai_parsed'] = {'verdict': 'SKIP', 'reason': '分数较低，已节省 Token 跳过审计'}
        res['ai_daily_view'] = "SKIP"
        res_item, chart_buf, _ = prepare_daily_chart(res, passed=True, reason="[跳过审计] 技术面评分第10名后")
        if chart_buf: res_item['chart_buf'] = chart_buf
        final_picks.append(res_item)
    
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
    
    # [P1-3 修复] 信号归档必须发生在 AI 判定之后: 仅归档通过 AI / 结构确认的信号,
    # AI 拒绝(rejected_list)的信号一律不进 signal_archive, 避免其永远停在 PENDING 污染追踪器。
    _archive_passed_signals(direct_picks + final_picks)

    return direct_picks, final_picks, rejected_list, watchlist, status_changes


def _archive_passed_signals(items):
    """[P1-3] 仅归档通过 AI / 结构确认的信号 (AI 拒绝的不归档, 杜绝 PENDING 污染)。

    items 为 direct_picks + final_picks (均为原始 res dict, 含 code/type/info)。
    """
    from core.signal_tracker import archive_signal
    from tools.notifier import factor_evidence_text
    for res in items:
        try:
            info = res.get('info', {})
            archive_signal(
                code=res['code'], strategy=res['type'], timeframe='daily',
                entry=info.get('entry', info.get('price', 0)),
                sl=info.get('sl', 0), tp=info.get('tp1', 0),
                ev_rating='',  # [P0-5] 不再写经回测证明为噪声的假字母
                evidence=factor_evidence_text(info.get('rating')),
                signal_date=info.get('signal_date', ''),
                signal_bar_idx=info.get('signal_bar_idx', -1),
                rr=info.get('rr', 0), name=res.get('name_cn', '')
            )
        except Exception:
            pass  # 归档失败不影响主流程


def _compose_report(direct_picks, final_picks, rejected_list, watchlist, status_changes, total_stocks=0, strategy_names=None):
    """
    [V9.16] 阶段 3: 统一推送格式 (与周线完全对齐)
    
    推送结构: 标题区 → 统计区(按策略优先级) → 每条一行精简(全推送, 因子证据) → 图表预告
    """
    from tools.notifier import format_signal_line
    
    # ===== 数据准备 =====
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
        # 🟢 空结果消息包含周期+策略名 (统一用 display_name)
        try:
            from core.strategy_registry import StrategyRegistry
            strat_label = ' / '.join(StrategyRegistry.get_metadata(s).get('display_name') or s for s in strategy_names) if strategy_names else '全策略'
        except Exception:
            strat_label = ' / '.join(s.replace('STRATEGY_', '').replace('_MASTER', '') for s in strategy_names) if strategy_names else '全策略'
        logger.info("💤 本轮无新信号 (Scanner 0 命中)")
        send_discord_message(f"💤 【日线/{strat_label}】，本次未发现信号")
        return

    # 合并所有通过的信号 (direct + AI passed)
    all_passed = list(direct_picks) + list(passed_candidates)
    all_passed.sort(key=lambda x: _extract_rating(x.get('info', {})).get('score', 0), reverse=True)

    logger.info("\n" + "="*50)
    logger.info(f"📨 阶段 3/3: 信号归档与结果推送 (Dispatch)")
    logger.info("="*50)

    # ===== 按策略分组 (去字母化: 因子证据 + 策略优先级, 非字母分级) =====
    # 背景: A+/A/B/C/D 字母评级经历史回测验证为统计噪声(9/9策略全噪声),
    #       详见 rating_authenticity_report.txt。改以因子命中作证据、按策略历史EV排优先级、全量推送。
    from collections import OrderedDict
    from core.strategy_registry import StrategyRegistry
    from tools.notifier import (format_signal_one_line, strategy_priority,
                                 signal_chart_key)

    def _sn(p):
        st = p.get('type', 'MTR')
        try:
            return StrategyRegistry.get_metadata(st).get('display_name') or st
        except Exception:
            return st

    groups = OrderedDict()
    for p in all_passed:
        groups.setdefault(_sn(p), []).append(p)

    total_hits = len(all_passed)

    # 组间按策略优先级降序
    ranked = sorted(groups.items(),
                    key=lambda kv: strategy_priority(kv[1][0].get('type', ''), 'daily'),
                    reverse=True)

    # ===== ① 标题区 =====
    msg_lines = []
    msg_lines.append("🔔 **【日线 Brooks-AI 猎手 雷达扫描完成】**")
    msg_lines.append(f"时间: {datetime.now().strftime('%Y-%m-%d')}")
    if total_stocks > 0:
        msg_lines.append(f"池子: 全市场 {total_stocks} 只个股")
    msg_lines.append("----------------------")

    # ===== ② 统计区 (按策略优先级, 列出全部激活策略含 0 命中) =====
    msg_lines.append(f"🎯 **命中结果**: 共 {total_hits} 只")
    if strategy_names:
        # 🟢 用激活策略名单补齐 0 命中项, 让交易员知道"扫了但今日无符合"
        count_by_sn = {sn: len(ps) for sn, ps in groups.items()}
        ordered_keys = sorted(set(strategy_names),
                              key=lambda s: strategy_priority(s, 'daily'),
                              reverse=True)
        for skey in ordered_keys:
            try:
                dname = StrategyRegistry.get_metadata(skey).get('display_name') or skey
            except Exception:
                dname = skey.replace('STRATEGY_', '').replace('_MASTER', '')
            cnt = count_by_sn.get(dname, 0)
            note = "" if cnt > 0 else "  (今日无符合)"
            msg_lines.append(f"   • {dname}: {cnt} 只{note}")
    else:
        for sn, ps in ranked:
            msg_lines.append(f"   • {sn}: {len(ps)} 只")
    msg_lines.append("")

    # ===== ③ 每条信号一行精简 (全推送, 不按字母筛) =====
    for sn, ps in ranked:
        msg_lines.append(f"📌 **{sn} ({len(ps)}只)**:")
        ps_sorted = sorted(ps, key=lambda x: (x.get('info', {}).get('rating') or {}).get('score', 0), reverse=True)
        for p in ps_sorted:
            code = p['code']
            name = p.get('name_cn') or fetch_stock_name(code)
            info = p.get('info', {})
            msg_lines.append(format_signal_one_line(code, name, p.get('type', 'MTR'), info, timeframe='daily'))
        msg_lines.append("")

    if total_hits == 0:
        msg_lines.append("  (无新增信号)")
        msg_lines.append("")

    # ===== ④ 图表预告 (全量按策略优先级+因子证据排序, 去掉仅A+门禁) =====
    chart_candidates = sorted(all_passed, key=lambda x: signal_chart_key(x, 'daily'), reverse=True)

    if chart_candidates:
        msg_lines.append(f"📊 信号 K线图即将推送 ({len(chart_candidates)} 张, 按策略优先级)...")
    else:
        msg_lines.append(f"📋 本轮无信号, 不推送图表。")

    summary_text = "\n".join(msg_lines)
    send_discord_message(summary_text)

    # 🟢 返回图表推送候选列表供 _dispatch_charts 使用
    return chart_candidates


def _dispatch_charts(direct_picks, final_picks, top_picks=None):
    """
    [去字母化] 阶段 4: 为全量信号生成图表并推送, 按策略优先级 + 因子证据排序 (去掉仅 A+/A 出图门禁)
    """
    from tools.notifier import (generate_chart_bytes, send_discord_images, send_discord_message,
                                factor_evidence_list, factor_evidence_text, signal_chart_key)
    
    # 去字母化: top_picks 已是 _compose_report 排出的全量(按策略优先级+因子证据排序)
    if top_picks is None:
        all_chart_candidates = sorted(list(final_picks) + list(direct_picks),
                                      key=lambda x: signal_chart_key(x, 'daily'), reverse=True)
    else:
        all_chart_candidates = list(top_picks)
    
    if not all_chart_candidates:
        logger.info("📭 无任何标的，跳过图表推送")
        return
    
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
                        ev_rating=None,
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
        # 🟢 [P1⑧] 信号洪流保护: 超出上限的图表候选聚合为文字摘要, 不刷屏
        MAX_CHARTS = settings.MAX_CHARTS_PER_RUN
        overflow_candidates = []
        if len(chart_pool) > MAX_CHARTS:
            overflow_candidates = all_chart_candidates[MAX_CHARTS:]
            chart_pool = chart_pool[:MAX_CHARTS]
            logger.warning(
                f"⚠️ 信号洪流保护: 本次 {len(all_chart_candidates)} 个图表候选, "
                f"仅推送 Top {len(chart_pool)} 张, 其余 {len(overflow_candidates)} 个汇总为文字"
            )

        BATCH_SIZE = 10
        logger.info(f"🎨 Discord 图表推送: {len(chart_pool)} 张信号图 ({BATCH_SIZE} 张/批, 按策略优先级)")

        # 去字母化: 不再区分 A+/A 级, 统一按策略优先级标注
        label = "📊 **信号 K线图 (按策略优先级)**"

        for batch_start in range(0, len(chart_pool), BATCH_SIZE):
            batch = chart_pool[batch_start:batch_start + BATCH_SIZE]
            send_discord_images(
                batch,
                content=f"{label} ({len(chart_pool)} 张)"
            )

        # 超量信号聚合为一条文字摘要 (不丢信号, 不刷图)
        if overflow_candidates:
            lines = [f"📝 **其余 {len(overflow_candidates)} 个信号 (图表已折叠, 按策略优先级)**"]
            for p in overflow_candidates:
                info = p.get('info', {})
                name = p.get('name_cn') or fetch_stock_name(p['code'])
                ev = factor_evidence_text(info.get('rating'))
                _stype = p.get('type', 'MTR')
                try:
                    from core.strategy_registry import StrategyRegistry
                    stype = StrategyRegistry.get_metadata(_stype).get('display_name') or _stype.replace('STRATEGY_', '')
                except Exception:
                    stype = _stype.replace('STRATEGY_', '')
                lines.append(f"• `{p['code']}` {name} | {stype} | {ev}")
            send_discord_message("\n".join(lines))


def run_pipeline_once(all_codes, strategies: List[str] = None, seen_signals: set = None, use_ai: bool = True) -> set:
    """
    [Phase2 重构] 主流水线协调器 (原 412 行 → 精简为 ~40 行控制流)
    
    Pipeline: scan → classify → report → dispatch
    """
    if strategies is None:
        from core.strategy_registry import StrategyRegistry
        strategies = StrategyRegistry.list_strategies()
    
    if seen_signals is None: seen_signals = set()
    
    logger.info("\n" + "="*50)
    logger.info("🚀 阶段 1/3: 全行情快照与市场分析 (Snapshot)")
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
    
    # AI Worker 线程池 (供 _classify_signals 使用)
    analysis_queue = queue.Queue(maxsize=5000)
    result_queue = queue.Queue()
    stop_event = threading.Event()
    
    num_ai_workers = 6
    ai_threads = []
    for i in range(num_ai_workers):
        t = threading.Thread(target=ai_worker, args=(i, analysis_queue, result_queue, stop_event))
        t.start()
        ai_threads.append(t)
    logger.info(f"🤖 已启动核心扫描进程 (技术面直通专线已就绪)")

    try:
        # 阶段 1: 扫描
        all_hits, new_signals = _scan_market(all_codes, strategies, seen_signals)
        
        # 阶段 2: 分类 + AI 审计
        direct_picks, final_picks, rejected_list, watchlist, status_changes = _classify_signals(
            all_hits, analysis_queue, result_queue, stop_event, ai_threads, use_ai=use_ai
        )
        
        # 阶段 3: 报告 (V9.16: 统一推送格式, 传入池子总量+策略名)
        top_picks = _compose_report(direct_picks, final_picks, rejected_list, watchlist, status_changes,
                                     total_stocks=len(all_codes), strategy_names=strategies)
        
        # 阶段 4: 图表 (去字母化: 全量按策略优先级出图, 不再限 A+/A)
        _dispatch_charts(direct_picks, final_picks, top_picks=top_picks)
    finally:
        # 🛡️ 确保退出时 AI Worker 线程被正确停止
        stop_event.set()
        for t in ai_threads:
            t.join(timeout=3)
    
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


def check_database_health():
    """[P0-4] 启动数据库自检: 防空库/错位库静默运行 (由 07-26 评审事故暴露)。

    返回 (fatal, warn):
      - fatal: 库不存在/过小/读不到 → 禁止扫描, 但保留恢复入口 (不退出程序)
      - warn:  数据滞后等可自愈项 → 仅提醒, 不拦截

    设计: 严重问题绝不调用 sys.exit 封死程序, 否则用户无法进入主菜单[4]数据同步
          自助修复 (评审 P1-5)。滞后 N 天 (如长假/周末未同步) 归为 warn, 不阻断。
    """
    import os
    from config import settings
    from datetime import datetime
    MIN_SIZE = 100 * 1024 * 1024          # 100MB
    MIN_ROWS = 1_000_000                  # 100万行
    MAX_LAG_DAYS = 7
    db_path = settings.DB_PATH
    fatal, warn = [], []

    # ① 文件存在 + ② 大小
    if not os.path.exists(db_path):
        fatal.append(f"数据库文件不存在: {db_path}")
    else:
        size = os.path.getsize(db_path)
        if size < MIN_SIZE:
            fatal.append(f"数据库过小 ({size/1024/1024:.1f}MB < 100MB), 疑似空库/错位")
        # ③ 行数 + ④ 新鲜度
        try:
            from core.database import get_db_connection
            with get_db_connection() as conn:
                cnt = conn.execute('SELECT COUNT(*) FROM daily_bars').fetchone()[0]
                if cnt < MIN_ROWS:
                    fatal.append(f"daily_bars 仅 {cnt:,} 行 (< 100万), 数据不完整")
                r = conn.execute('SELECT MAX(trade_date) FROM daily_bars').fetchone()
                if r and r[0]:
                    last = datetime.strptime(r[0], '%Y-%m-%d')
                    lag = (datetime.now() - last).days
                    if lag > MAX_LAG_DAYS:
                        warn.append(f"日线数据滞后 {lag} 天 (> {MAX_LAG_DAYS}), 建议先同步")
        except Exception as e:
            fatal.append(f"数据库读取失败: {e}")

    if fatal:
        msg = "🛑 **数据库严重异常, 扫描已禁用**\n" + "\n".join(f"• {p}" for p in fatal)
        logger.error(msg)
        try:
            send_discord_message(msg)
        except Exception:
            pass
    if warn:
        logger.warning("; ".join(warn))
    return fatal, warn


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
            result = update_daily_data_batch()
            elapsed = time.time() - t0
            if result:
                downloaded, total = result
                if downloaded == 0 and total > 0:
                    print(f"✅ 日线数据已全量最新 (耗时 {elapsed:.0f}秒, 活跃股票均已最新, {total}只退市/无新数据已跳过)")
                elif downloaded < total:
                    print(f"✅ 日线同步完成 (耗时 {elapsed:.0f}秒, 成功更新 {downloaded}/{total} 只, 其余为退市/无新数据)")
                else:
                    print(f"✅ 日线同步完成 (耗时 {elapsed:.0f}秒, {downloaded}/{total})")
            else:
                print(f"✅ 日线同步完成 (耗时 {elapsed:.0f}秒)")
        except Exception as e:
            print(f"❌ 日线同步失败: {e}")
    
    if sync_choice in ('2', '3'):
        print("\n🔄 开始周线数据同步...")
        t0 = time.time()
        try:
            from core.data_provider import update_weekly_data_batch
            result = update_weekly_data_batch()
            elapsed = time.time() - t0
            if result:
                downloaded, total = result
                if downloaded == 0 and total > 0:
                    print(f"✅ 周线数据已全量最新 (耗时 {elapsed:.0f}秒, 活跃股票均已最新, {total}只退市/无新数据已跳过)")
                elif downloaded < total:
                    print(f"✅ 周线同步完成 (耗时 {elapsed:.0f}秒, 成功更新 {downloaded}/{total} 只, 其余为退市/无新数据)")
                else:
                    print(f"✅ 周线同步完成 (耗时 {elapsed:.0f}秒, {downloaded}/{total})")
            else:
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
    parser.add_argument('--no-ai', action='store_true', help='旁路(Bypass) DeepSeek AI 审计，全量技术面直通')
    args = parser.parse_args()

    # [P1-6] 重置本次运行摘要, 心跳据此写出真实状态(避免假成功)
    RUN_SUMMARY.clear()
    RUN_SUMMARY.update(
        mode=None, job_ran=False, signals=0,
        discord_configured=bool(os.environ.get('DISCORD_BOT_TOKEN')),
        error=None,
    )

    init_journal_db()

    # [P0-4] 启动数据库自检: 防空库/错位库静默运行。
    # 维护模式 (track/report) 只读归档库, 不依赖实时行情, 跳过以免误伤。
    # [P1-5] 严重问题仅禁用扫描、保留恢复入口(不 sys.exit 封死); 滞后归为提醒。
    db_fatal, db_warn = [], []
    if not (args.track or args.report):
        db_fatal, db_warn = check_database_health()
    if db_warn:
        print("\n⚠️  " + "\n⚠️  ".join(db_warn))
    if db_fatal:
        print("\n" + "=" * 50)
        print("🛑 数据库严重异常, 扫描功能已禁用")
        for p in db_fatal:
            print(f"  • {p}")
        print("👉 请选择主菜单 [4] 数据同步 修复后重试 (恢复入口保持开放)")
        print("=" * 50)

    # ============================================================
    # 追踪模式: python hunter.py --track [--report]
    # ============================================================
    if args.track or args.report:
        from core.signal_tracker import init_signal_archive, track_signals, generate_report, format_tracker_discord_msg, run_tracker_dashboard
        init_signal_archive()
        RUN_SUMMARY['mode'] = 'track' if args.track else 'report'
        RUN_SUMMARY['job_ran'] = True
        if args.track:
            # 🟢 V9.3: 统一走仪表盘路径 (内含追踪 + 按状态分组推送 + 报表)
            run_tracker_dashboard()
        if args.report:
            # 仅生成统计报表 (不重复追踪)
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
        if db_fatal:
            print("🛑 数据库严重异常, 已禁止扫描。请先运行交互模式选 [4] 数据同步修复, 或检查数据库文件。")
            return
        if args.timeframe == 'weekly':
            print(f"\n🌙 周线模式启动 (检查最近 {args.weeks} 周)")
            from core.strategy_registry import StrategyRegistry
            from core.scan_engine import run_weekly_scan
            all_codes = data_provider.get_stock_list()
            if not all_codes: print("❌ 获取股票列表失败"); return
            if args.limit > 0: all_codes = all_codes[:args.limit]

            # 🟢 [P1] 使用 StrategyRegistry 动态获取支持周线的策略列表
            weekly_supported = StrategyRegistry.get_strategies_by_timeframe('weekly')
            active_strategies = None
            if args.strategy:
                if args.strategy.upper() == 'ALL':
                    active_strategies = weekly_supported
                else:
                    active_strategies = [s.strip().upper() for s in args.strategy.split(',')]
            else:
                active_strategies = [weekly_supported[0]] if weekly_supported else []

            run_weekly_scan(active_strategies, weeks=args.weeks, limit=args.limit, all_codes=all_codes)
            RUN_SUMMARY['mode'] = 'weekly'; RUN_SUMMARY['job_ran'] = True
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
        print("  5. 📝 复盘录入 (Review Bridge)")
        print("═"*40)
        try:
            mode_choice = input("请选择 (默认 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            mode_choice = '1'
        
        # 路径 2: 信号追踪仪表盘
        if mode_choice == '2':
            from core.signal_tracker import run_tracker_dashboard
            RUN_SUMMARY['mode'] = 'track'; RUN_SUMMARY['job_ran'] = True
            run_tracker_dashboard()
            return
        
        # 路径 3: 持仓管家
        if mode_choice == '3':
            from tools.for_hold import load_holdings_with_cost, analyze_single_stock_micro
            holdings = load_holdings_with_cost()
            if not holdings:
                print("⚠️ 持仓列表为空 (请检查 hold_list.txt)"); return
            print(f"🛡️ 持仓管家启动, {len(holdings)} 只股票...")
            RUN_SUMMARY['mode'] = 'guardian'; RUN_SUMMARY['job_ran'] = True
            for item in holdings:
                try: analyze_single_stock_micro(item)
                except Exception as e: print(f"❌ {item['code']} 分析失败: {e}")
            return
        
        # 路径 4: 数据同步
        if mode_choice == '4':
            RUN_SUMMARY['mode'] = 'sync'; RUN_SUMMARY['job_ran'] = True
            _run_data_sync()
            return
        
        # 路径 5: 复盘录入
        if mode_choice == '5':
            from core.review_bridge import run_review_cli
            RUN_SUMMARY['mode'] = 'review'; RUN_SUMMARY['job_ran'] = True
            run_review_cli()
            return
        
        # 路径 1: 扫描 → 选时间周期 (致命库损坏时禁用, 保留[4]同步入口)
        if db_fatal:
            print("🛑 数据库严重异常, 扫描已禁用。请先选 [4] 数据同步 修复。")
            return
        print("\n  选择扫描周期:")
        print("  1. 日线 (Daily)")
        print("  2. 周线 (Weekly)")
        try:
            tf_choice = input("  请选择 (默认 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            tf_choice = '1'
        
        if tf_choice == '2':
            print(f"\n🌙 周线模式启动 (扫描有效缺口结构 / 3K 埋伏)")
            from core.strategy_registry import StrategyRegistry
            from core.scan_engine import run_weekly_scan
            all_codes = data_provider.get_stock_list()
            if not all_codes: print("❌ 获取股票列表失败"); return
            if args.limit > 0: all_codes = all_codes[:args.limit]

            # 🟢 [P1] 使用 StrategyRegistry 动态获取支持周线的策略列表
            weekly_supported = StrategyRegistry.get_strategies_by_timeframe('weekly')
            # 🟢 [Phase 3] 3K 也纳入周线菜单 (经 run_weekly_scan 路由, 不归档)
            menu_options = list(weekly_supported) + ['STRATEGY_3K']
            print("\n" + "="*40)
            print("🔍 周线扫描策略选择")
            print("="*40)
            for i, s in enumerate(menu_options):
                print(f"  {i+1}. {s}")
            print(f"  {len(menu_options)+1}. ALL (全量扫描, 仅缺口家族)")
            print("="*40)

            try:
                choice = input(f"请输入选择序号 (默认 1 - {weekly_supported[0]}): ").strip()
                if not choice:
                    active_strategies = [weekly_supported[0]]
                elif choice.isdigit():
                    idx = int(choice)
                    if idx == len(menu_options) + 1:
                        active_strategies = weekly_supported
                    elif 1 <= idx <= len(menu_options):
                        active_strategies = [menu_options[idx-1]]
                    else:
                        active_strategies = [weekly_supported[0]]
                else:
                    _up = choice.upper()
                    if _up in menu_options:
                        active_strategies = [_up]
                    elif _up in weekly_supported:
                        active_strategies = [_up]
                    else:
                        active_strategies = [weekly_supported[0]]
            except (EOFError, KeyboardInterrupt):
                active_strategies = [weekly_supported[0]]

            print(f"\n🚀 已激活周线策略: {', '.join(active_strategies)}")

            run_weekly_scan(active_strategies, weeks=args.weeks, limit=args.limit, all_codes=all_codes)
            RUN_SUMMARY['mode'] = 'weekly'; RUN_SUMMARY['job_ran'] = True
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

    # 询问是否启用 AI 二次审计
    use_ai = True
    if not args.no_ai:
        try:
            ai_choice = input("\n是否启用 AI 二次审计？\n  1. 是 [默认]\n  2. 否 (纯技术面直通，约10秒)\n请选择 (1/2): ").strip()
            if ai_choice == '2':
                use_ai = False
        except (EOFError, KeyboardInterrupt):
            use_ai = True
    else:
        use_ai = False

    try:
        all_codes = data_provider.get_stock_list()
        if not all_codes:
            return
        if args.limit > 0:
            all_codes = all_codes[:args.limit]
        new_signals = run_pipeline_once(all_codes, strategies=active_strategies, use_ai=use_ai)
        RUN_SUMMARY['mode'] = 'daily'
        RUN_SUMMARY['job_ran'] = True
        RUN_SUMMARY['signals'] = len(new_signals or [])
    except KeyboardInterrupt:
        logger.info("🛑 程序已终止")
    finally:
        # 🛡️ 优雅退出: WAL checkpoint + 连接池排空
        try:
            from core.database import close_all_connections
            close_all_connections()
        except Exception:
            pass
        gc.collect()

def _notify_crash(exc):
    """运行监控: 扫描崩溃时发 Discord 告警 + 写崩溃日志, 避免静默死 (紧贴高可用第一性)。

    仅作为最外层兜底 —— 任何 main() 未捕获的异常都会在此被捕获并告警,
    然后原样 re-raise 保持非零退出码 (供外部调度器感知失败)。
    """
    import os, json, traceback, datetime
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tb = traceback.format_exc()
    # 1) 崩溃日志 (兜底, 即使 Discord 失败也不丢信息)
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(root, 'data')
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'crash_log.txt'), 'a', encoding='utf-8') as f:
            f.write(f"\n[{ts}] CRASH\n{tb}\n{'='*60}\n")
    except Exception:
        pass
    # 2) Discord 告警 (best-effort, 失败不影响退出码)
    try:
        from tools.notifier import send_discord_message
        send_discord_message(
            f"🚨 **Brooks-AI 猎手运行崩溃**\n时间: {ts}\n```\n{tb[-1500:]}\n```"
        )
    except Exception:
        pass


# 运行会话摘要: main() 在真实任务执行点填充, _write_heartbeat 据此写出真实心跳。
RUN_SUMMARY = {
    'mode': None,            # 本次运行的真实模式 (daily/weekly/sync/track/guardian/review)
    'job_ran': False,        # 是否真的执行了扫描/同步等任务 (菜单空跑/数据缺失早退=False)
    'signals': 0,            # 扫描命中信号数
    'discord_configured': False,  # Discord 令牌是否配置 (False=推送被静默跳过)
    'error': None,           # 捕获的异常信息 (非空则 status='error')
}


def _write_heartbeat(summary=None):
    """运行监控: 写出本次运行的真实心跳, 供 tools/check_heartbeat.py 判断健康。

    [P1-6 修复] 旧实现仅 main() 正常返回即写 status='ok', 不区分"真的扫了"还是
    "菜单空跑 / 数据缺失早退" -> 系统没干活也报正常 (假成功)。现依据 RUN_SUMMARY:
      - error 非空 -> status='error' (崩溃/异常)
      - job_ran=True -> status='ok'    (真实任务执行并跑完)
      - 其余        -> status='idle'   (仅打开菜单 / 早退, 不算一次有效运行)
    并附 mode / signals / discord_configured 真值, 便于排查"扫了但没推"等盲区。
    """
    import os, json, datetime
    s = summary or RUN_SUMMARY
    if s.get('error'):
        status = 'error'
    elif s.get('job_ran'):
        status = 'ok'
    else:
        status = 'idle'
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(root, 'data')
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, 'last_run.json'), 'w', encoding='utf-8') as f:
            json.dump({
                'last_run': datetime.datetime.now().isoformat(timespec='seconds'),
                'status': status,
                'mode': s.get('mode'),
                'signals': s.get('signals', 0),
                'discord_configured': s.get('discord_configured', False),
                'error': s.get('error'),
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        # [P1-6] 崩溃也写出真实心跳(status='error'), 不再让 check_heartbeat 只靠"过期"猜测
        RUN_SUMMARY['error'] = str(e)
        _write_heartbeat()
        _notify_crash(e)
        raise
    else:
        _write_heartbeat()