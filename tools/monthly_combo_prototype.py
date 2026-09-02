"""
tools/monthly_combo_prototype.py
=====================================================
[B 方案原型] 月线候选池 + 周线/日线确认组合分析器

核心思路（大白话）：
  月线弹簧线信号太稀疏（6个月30只），而且出来时可能已错过最佳入场。
  本脚本把月线信号当"观察池"（粗筛），再用周线/日线找精确入场时机（精筛）。

两层漏斗：
  L1 月线 → 候选池（已有：monthly_range_break_watchlist.json 的 11 只）
  L2 周线   → 结构完整性检查（SL没穿？TP没摸？价格在什么位置？）
  L3 日线   → 入场时机检查（价格是否逼近买点？短期动能如何？）

输出四档：
  🟢 READY    = 周线结构完好 + 日线逼近买点 → 可重点盯、设预警
  🟡 WATCHING = 结构还在但离买点远          → 放自选、每周看一眼
  ⚪ INVALID  = 已破止损或已摸目标           → 复盘归档
  ⚠️  STALE    = 信号太老(>12月)或数据异常     → 降优先级

用法：
  cd 项目根目录
  PYTHONPATH="项目根目录" .venv/Scripts/python.exe tools/monthly_combo_prototype.py

产出：
  strategy_lab/monthly_combo_report.md   （人读版）
  data/monthly_combo_results.json        （机读版）

注意：纯只读分析，不动库、不归档、不推送 Discord。
"""

import json
import os
import sys
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, Tuple, List

import pandas as pd
import numpy as np

# ---- 项目路径 ----
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from core.data_provider import get_stock_data, get_stock_data_weekly, get_monthly_bars


# =====================================================================
# 配置参数（可调）
# =====================================================================

# 周线确认窗口：信号后查最近 N 根周线
WEEKLY_LOOKBACK = 12

# 日线确认窗口：最近 N 个交易日
DAILY_LOOKBACK = 60

# "逼近买点"的定义：最新收盘价距月线 entry 在此百分比内视为"接近"
ENTRY_PROXIMITY_PCT = 5.0

# 信号最大年龄（月），超过视为 STALE
MAX_SIGNAL_AGE_MONTHS = 12


# =====================================================================
# 单只股票的多周期确认分析
# =====================================================================

def analyze_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    对一个月线候选做周线+日线双重确认分析。

    返回完整分析结果 dict，含 classification / weekly_checks / daily_checks / scores。
    """
    code = candidate['code']
    name = candidate.get('name', '')
    entry = float(candidate['entry'])
    sl = float(candidate['sl'])
    tp = float(candidate['tp'])
    sig_date_str = candidate.get('date', '')

    # 解析信号日期算年龄
    try:
        sig_date = pd.Timestamp(sig_date_str)
        age_days = (pd.Timestamp.now() - sig_date).days
        age_months = age_days / 30.0
    except Exception:
        sig_date = pd.Timestamp.now()
        age_days = 0
        age_months = 0

    result = {
        'code': code,
        'name': name,
        'signal_date': sig_date_str,
        'entry': entry,
        'sl': sl,
        'tp': tp,
        'rr': candidate.get('rr', 0),
        'age_months': round(age_months, 1),
        'classification': 'UNKNOWN',
        'classification_reason': '',
        'weekly': {},
        'daily': {},
        'combo_score': 0,
    }

    # ---- 快速失效检查：信号太老 ----
    if age_months > MAX_SIGNAL_AGE_MONTHS:
        result['classification'] = 'STALE'
        result['classification_reason'] = f'信号已过{age_months:.1f}个月(>{MAX_SIGNAL_AGE_MONTHS}月)，价格可能已大幅漂移'
        return result

    # ---- 取周线数据 ----
    wdf = None
    try:
        wdf = get_stock_data_weekly(code, limit=WEEKLY_LOOKBACK + 4)  # 多取几根留余量
    except Exception as e:
        result['weekly']['error'] = str(e)

    # ---- 取日线数据 ----
    ddf = None
    try:
        ddf = get_stock_data(code, limit=DAILY_LOOKBACK + 5)
    except Exception as e:
        result['daily']['error'] = str(e)

    # 如果两个都没拿到数据，直接标异常
    if (wdf is None or wdf.empty) and (ddf is None or ddf.empty):
        result['classification'] = 'STALE'
        result['classification_reason'] = '无法获取周线或日线数据'
        return result

    # =====================================================================
    # 周线层确认（结构完整性）
    # =====================================================================
    w_checks = {
        'sl_intact': False,       # 周线最低价从未跌破月线 SL
        'tp_not_hit': True,       # 周线最高价未触及月线 TP
        'price_above_sl': False,  # 最新周线收在 SL 上方
        'price_below_tp': True,   # 最新周线收在 TP 下方（还有空间）
        'near_entry_weekly': False,  # 最近有周K低点接近 entry
        'bullish_weekly': False,  # 最近周线偏多（收盘>开盘的周占比）
    }

    if wdf is not None and not wdf.empty:
        wdf = wdf.copy()
        # 确保有日期列可排序
        if 'date' in wdf.columns:
            wdf = wdf.sort_values('date').reset_index(drop=True)
        elif 'trade_date' in wdf.columns:
            wdf = wdf.sort_values('trade_date').reset_index(drop=True)

        w_low_min = wdf['low'].min()
        w_high_max = wdf['high'].max()
        w_last_close = wdf.iloc[-1]['close']
        w_last_open = wdf.iloc[-1].open if 'open' in wdf.columns else w_last_close

        w_checks['sl_intact'] = w_low_min > sl * 0.98   # 允许 2% 滑点/四舍五入误差
        w_checks['tp_not_hit'] = w_high_max < tp * 1.02
        w_checks['price_above_sl'] = w_last_close > sl
        w_checks['price_below_tp'] = w_last_close < tp

        # 最近周K有没有低点接近 entry（±8%）
        recent_w = wdf.tail(min(4, len(wdf)))
        w_checks['near_entry_weekly'] = (recent_w['low'].min() <= entry * 1.08) and \
                                         (recent_w['high'].max() >= entry * 0.92)

        # 偏多周线比例（最近 8 周）
        look_w = wdf.tail(min(8, len(wdf)))
        if 'open' in look_w.columns:
            bull_weeks = (look_w['close'] > look_w['open']).sum()
            w_checks['bullish_weekly'] = bull_weeks >= len(look_w) // 2  # 过半数阳周

        result['weekly'] = {
            'checks': w_checks,
            'last_close': round(w_last_close, 2),
            'lowest_since': round(w_low_min, 2),
            'highest_since': round(w_high_max, 2),
            'weeks_analyzed': len(wdf),
            'pct_from_sl': round((w_last_close - sl) / sl * 100, 1) if sl > 0 else None,
            'pct_to_tp': round((tp - w_last_close) / tp * 100, 1) if tp > 0 else None,
            'pct_from_entry': round((w_last_close - entry) / entry * 100, 1) if entry > 0 else None,
        }

    # =====================================================================
    # 日线层确认（入场时机）
    # =====================================================================
    d_checks = {
        'sl_intact_daily': False,      # 日线从未跌破 SL
        'price_near_entry': False,     # 最新价在 entry ± ENTRY_PROXIMITY_PCT%
        'recent_approached_entry': False,  # 最近20天有到过 entry 附近
        'short_momentum_up': False,    # 短期均线多头排列（5日 > 20日）
        'last_bullish': False,         # 最新一根日线是阳线
        'volume_confirm': False,       # 最近阳线放量（比前5日均量高）
    }

    if ddf is not None and not ddf.empty:
        ddf = ddf.copy()
        date_col = 'date' if 'date' in ddf.columns else ('trade_date' if 'trade_date' in ddf.columns else None)
        if date_col:
            ddf = ddf.sort_values(date_col).reset_index(drop=True)

        d_low_min = ddf['low'].min()
        d_last_close = ddf.iloc[-1]['close']
        d_last_open = ddf.iloc[-1].get('open', d_last_close)
        d_last_vol = ddf.iloc[-1].get('volume', 0)

        d_checks['sl_intact_daily'] = d_low_min > sl * 0.98

        # 价格距 entry 百分比
        pct_from_entry = (d_last_close - entry) / entry * 100 if entry > 0 else 999
        d_checks['price_near_entry'] = abs(pct_from_entry) <= ENTRY_PROXIMITY_PCT

        # 最近 20 天有没有到过 entry 附近（±5%）
        recent_d = ddf.tail(min(20, len(ddf)))
        d_checks['recent_approached_entry'] = \
            (recent_d['low'].min() <= entry * 1.05) and (recent_d['high'].max() >= entry * 0.95)

        # 短期动能：5日收盘均值 vs 20日收盘均值
        if len(ddf) >= 20:
            ma5 = ddf['close'].tail(5).mean()
            ma20 = ddf['close'].tail(20).mean()
            d_checks['short_momentum_up'] = ma5 > ma20

        # 最新 K 线是阳线
        d_checks['last_bullish'] = d_last_close > d_last_open

        # 放量确认：最近阳线的成交量 > 前5天均量的 1.2 倍
        if d_checks['last_bullish'] and d_last_vol > 0 and len(ddf) >= 6:
            avg_vol_5 = ddf['volume'].iloc[-6:-1].mean()
            d_checks['volume_confirm'] = d_last_vol > avg_vol_5 * 1.2

        result['daily'] = {
            'checks': d_checks,
            'last_close': round(d_last_close, 2),
            'lowest_since': round(d_low_min, 2),
            'days_analyzed': len(ddf),
            'pct_from_sl': round((d_last_close - sl) / sl * 100, 1) if sl > 0 else None,
            'pct_to_tp': round((tp - d_last_close) / tp * 100, 1) if tp > 0 else None,
            'pct_from_entry': round(pct_from_entry, 1),
            'ma5_vs_ma20': '多头' if d_checks.get('short_momentum_up') else ('空头' if len(ddf) >= 20 else '数据不足'),
        }

    # =====================================================================
    # 综合分类决策
    # =====================================================================
    wc = result['weekly'].get('checks', {})
    dc = result['daily'].get('checks', {})

    # 第一优先：硬性失效
    if not wc.get('sl_intact', True):
        result['classification'] = 'INVALID'
        result['classification_reason'] = f"周线已跌破止损 SL={sl}（周线最低={result['weekly'].get('lowest_since')}）"
        return result

    if not dc.get('sl_intact_daily', True):
        result['classification'] = 'INVALID'
        result['classification_reason'] = f"日线已跌破止损 SL={sl}（日线最低={result['daily'].get('lowest_since')}）"
        return result

    if not wc.get('tp_not_hit', True):
        result['classification'] = 'INVALID'
        result['classification_reason'] = f"周线已触及目标 TP={tp}（周线最高={result['weekly'].get('highest_since')}）——好问题"
        return result

    # 第二优先：计算组合得分
    score = 0
    reasons = []

    # 周线结构分（最高 40 分）
    if wc.get('price_above_sl'):
        score += 15
    if wc.get('price_below_tp'):
        score += 10
    if wc.get('near_entry_weekly'):
        score += 10
    if wc.get('bullish_weekly'):
        score += 5

    # 日线时机分（最高 60 分）
    if dc.get('price_near_entry'):
        score += 25
        reasons.append('日线价在买点±5%内')
    elif dc.get('recent_approached_entry'):
        score += 10
        reasons.append('近期曾逼近买点')

    if dc.get('short_momentum_up'):
        score += 15
        reasons.append('短期均线多头')
    if dc.get('last_bullish'):
        score += 10
        reasons.append('最新日线收阳')
    if dc.get('volume_confirm'):
        score += 10
        reasons.append('阳线放量确认')

    result['combo_score'] = score

    # 分类阈值
    if score >= 50:
        result['classification'] = 'READY'
        result['classification_reason'] = f"综合评分 {score}/100 —— " + "; ".join(reasons) if reasons else "周线+日线多项确认通过"
    elif score >= 25:
        result['classification'] = 'WATCHING'
        result['classification_reason'] = f"综合评分 {score}/100 —— 结构完好但入场时机未到 (" + (", ".join(reasons) if reasons else "等待价格回归") + ")"
    else:
        result['classification'] = 'WATCHING'
        result['classification_reason'] = f"综合评分 {score}/100 —— 结构完好但偏离买点较远，持续观察"

    return result


# =====================================================================
# 主流程：批量分析 + 报告生成
# =====================================================================

def main():
    project_root = PROJECT_ROOT
    watchlist_path = os.path.join(project_root, 'data', 'monthly_range_break_watchlist.json')

    # ---- 1. 加载月线候选池 ----
    if not os.path.exists(watchlist_path):
        print(f"❌ 找不到月线观察池: {watchlist_path}")
        print("   请先运行月线扫描 (run_monthly_scan) 生成 watchlist")
        sys.exit(1)

    with open(watchlist_path, 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    candidates = watchlist.get('signals_mrb', [])
    total = len(candidates)
    print(f"\n🌕 月线组合原型分析")
    print(f"   候选池: {total} 只（来自 {os.path.basename(watchlist_path)}）")
    print(f"   分析维度: 周线结构({WEEKLY_LOOKBACK}周) + 日线时机({DAILY_LOOKBACK}日)")
    print(f"   逼近阈值: 距买点 ±{ENTRY_PROXIMITY_PCT}%\n")

    # ---- 2. 并行分析（4线程，与扫描一致）----
    results = []
    completed = 0
    MAX_WORKERS = 4

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_candidate, c): c for c in candidates}
        for future in as_completed(futures):
            completed += 1
            code = futures[future]
            try:
                r = future.result()
                results.append(r)
                cls_tag = r['classification']
                cls_emoji = {'READY': '🟢', 'WATCHING': '🟡', 'INVALID': '⚪', 'STALE': '⚠️'}.get(cls_tag, '❓')
                print(f"  [{completed}/{total}] {cls_emoji} {r['code']} {r['name']} → {cls_tag} (score={r.get('combo_score', '?')})")
            except Exception as e:
                print(f"  [{completed}/{total}] ❌ {code} 分析异常: {e}")

    # ---- 3. 排序：READY > WATCHING > INVALID > STALE; 同档按 score 降序 ----
    ORDER = {'READY': 0, 'WATCHING': 1, 'INVALID': 2, 'STALE': 3}
    results.sort(key=lambda x: (ORDER.get(x['classification'], 9), -(x.get('combo_score') or 0)))

    # ---- 4. 统计分布 ----
    dist = {}
    for r in results:
        c = r['classification']
        dist[c] = dist.get(c, 0) + 1

    print(f"\n{'='*60}")
    print(f"📊 分析完成! 分布: ", end="")
    for k in ['READY', 'WATCHING', 'INVALID', 'STALE']:
        if k in dist:
            emoji = {'READY': '🟢', 'WATCHING': '🟡', 'INVALID': '⚪', 'STALE': '⚠️'}[k]
            print(f"{emoji}{k}={dist[k]}  ", end="")
    print()

    # ---- 5. 写 Markdown 报告 ----
    lab_dir = os.path.join(project_root, 'strategy_lab')
    os.makedirs(lab_dir, exist_ok=True)
    md_path = os.path.join(lab_dir, 'monthly_combo_report.md')

    now = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')
    md = f"# 🌕 月线候选池 × 周线/日线组合确认报告\n\n"
    md += f"> **生成时间**: {now}\n"
    md += f"> **候选来源**: `monthly_range_break_watchlist.json` ({total} 只)\n"
    md += f"> **分析方法**: 月线=观察池 → 周线=结构检查 → 日线=入场时机\n\n"

    # 结果分布表
    md += "## 📊 结果分布\n\n"
    md += "| 档位 | 数量 | 含义 |\n|:---:|:---:|:---|\n"
    for k in ['READY', 'WATCHING', 'INVALID', 'STALE']:
        emoji = {'READY': '🟢 可入场', 'WATCHING': '🟡 观察中', 'INVALID': '⚪ 已失效', 'STALE': '⚠️ 过期'}[k]
        desc = {'READY': '周线结构完好+日线逼近买点，可设预警重点盯',
                 'WATCHING': '结构还在但离买点远，放自选每周看',
                 'INVALID': '已破止损或已摸目标，复盘归档',
                 'STALE': '信号太老或数据异常，降优先级'}[k]
        md += f"| {emoji} | {dist.get(k, 0)} | {desc} |\n"
    md += "\n"

    # ---- 各档明细 ----
    for tier in ['READY', 'WATCHING', 'INVALID', 'STALE']:
        tier_results = [r for r in results if r['classification'] == tier]
        if not tier_results:
            continue

        emoji = {'READY': '🟢', 'WATCHING': '🟡', 'INVALID': '⚪', 'STALE': '⚠️'}[tier]
        title = {'READY': '可入场（重点盯）', 'WATCHING': '观察中（持续跟踪）',
                 'INVALID': '已失效（复盘归档）', 'STALE': '过期（降优先级）'}[tier]

        md += f"\n## {emoji} {title} ({len(tier_results)} 只)\n\n"
        md += "| 代码 | 名称 | 信号月 | 买点 | 现价 | 距SL% | 距TP% | 距买点% | 得分 | 判定理由 |\n"
        md += "|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---|\n"

        for r in tier_results:
            w = r.get('weekly', {})
            d = r.get('daily', {})
            # 优先用日线现价（更精细），其次用周线
            last_close = d.get('last_close') or w.get('last_close', 0)
            pct_sl = d.get('pct_from_sl') or w.get('pct_from_sl', 0)
            pct_tp = d.get('pct_to_tp') or w.get('pct_to_tp', 0)
            pct_entry = d.get('pct_from_entry') or w.get('pct_from_entry', 0)

            md += (f"| `{r['code']}` | **{r['name']}** | {r['signal_date']} "
                   f"| {r['entry']:.2f} | {last_close:.2f} "
                   f"| {pct_sl:+.1f}% | {pct_tp:+.1f}% | {pct_entry:+.1f}% "
                   f"| {r.get('combo_score', 0)} | {r['classification_reason']} |\n")

    # ---- 详细诊断表（每只的周线+日线拆解）----
    md += "\n---\n\n## 🔬 详细诊断（逐只拆解）\n\n"

    for r in results:
        emoji = {'READY': '🟢', 'WATCHING': '🟡', 'INVALID': '⚪', 'STALE': '⚠️'}.get(r['classification'], '❓')
        md += f"### {emoji} `{r['code']}` {r['name']} — {r['classification']}\n\n"
        md += f"- **信号月**: {r['signal_date']} | **买点**: {r['entry']:.2f} | **SL**: {r['sl']:.2f} | **TP**: {r['tp']:.2f} | **RR**: 1:{r['rr']}\n"
        md += f"- **信号年龄**: {r['age_months']:.1f} 个月 | **组合得分**: {r.get('combo_score', 0)}/100\n\n"

        w = r.get('weekly', {})
        if w:
            wc = w.get('checks', {})
            md += "**周线检查**:\n"
            md += f"- 最新收盘: {w.get('last_close')} | 区间低: {w.get('lowest_since')} | 区间高: {w.get('highest_since')}\n"
            md += f"- SL 完好: ✅ 是" if wc.get('sl_intact') else "- SL 完好: ❌ 已破\n"
            md += f"- TP 未触: ✅ 是" if wc.get('tp_not_hit') else "- TP 未触: ❌ 已触及\n"
            md += f"- 收在 SL 上方: ✅ 是" if wc.get('price_above_sl') else "- 收在 SL 上方: ❌ 否\n"
            md += f"- 收在 TP 下方: ✅ 是" if wc.get('price_below_tp') else "- 收在 TP 下方: ❌ 已超\n"
            md += f"- 近期接近买点: ✅ 是" if wc.get('near_entry_weekly') else "- 近期接近买点: ❌ 否\n"
            md += f"- 周线偏多: ✅ 是" if wc.get('bullish_weekly') else "- 周线偏多: ❌ 否\n"
            md += f"- 距 SL: {w.get('pct_from_sl')}% | 距 TP: {w.get('pct_to_tp')}% | 距买点: {w.get('pct_from_entry')}%\n\n"

        d = r.get('daily', {})
        if d:
            dc = d.get('checks', {})
            md += "**日线检查**:\n"
            md += f"- 最新收盘: {d.get('last_close')} | 区间低: {d.get('lowest_since')}\n"
            md += f"- SL 完好(日): ✅ 是" if dc.get('sl_intact_daily') else "- SL 完好(日): ❌ 已破\n"
            md += f"- 价在买点±{ENTRY_PROXIMITY_PCT}%: ✅ 是" if dc.get('price_near_entry') else f"- 价在买点±{ENTRY_PROXIMITY_PCT}%: ❌ 否(距{d.get('pct_from_entry')}%)\n"
            md += f"- 近期曾逼近: ✅ 是" if dc.get('recent_approached_entry') else "- 近期曾逼近: ❌ 否\n"
            ma_stat = d.get('ma5_vs_ma20', 'N/A')
            md += f"- 短期均线: {ma_stat}\n"
            md += f"- 最新K阳线: ✅ 是" if dc.get('last_bullish') else "- 最新K阳线: ❌ 阴线\n"
            md += f"- 放量确认: ✅ 是" if dc.get('volume_confirm') else "- 放量确认: ❌ 否\n"
            md += f"- 距 SL: {d.get('pct_from_sl')}% | 距 TP: {d.get('pct_to_tp')}%\n\n"

        md += f"**判定**: {r['classification_reason']}\n\n"
        md += "---\n\n"

    # 方法论说明
    md += "## 📖 方法论说明\n\n"
    md += "| 维度 | 检查项 | 权重 | 说明 |\n|:---|:---|:---:|:---|\n"
    md += "| **周线结构** | SL 未被周线跌破 | 15 | 弹簧形态核心支撑不能丢 |\n"
    md += "| **周线结构** | TP 未被触及 | 10 | 还有上行空间才值得盯 |\n"
    md += "| **周线结构** | 近期周K接近买点 | 10 | 大周期在入场区附近晃悠 |\n"
    md += "| **周线结构** | 周线偏多(阳周>半) | 5 | 中期方向不逆势 |\n"
    md += "| **周线结构** | 收在 SL 上方 | 15 | 当前位置安全 |\n"
    md += "| **日线时机** | 价在买点±5%内 | 25 | 最关键：可以挂单了 |\n"
    md += "| **日线时机** | 近期曾逼近买点 | 10 | 说明这个价位有吸引力 |\n"
    md += "| **日线时机** | MA5 > MA20 | 15 | 短期动能向上 |\n"
    md += "| **日线时机** | 最新日线收阳 | 10 | 当根有买盘力量 |\n"
    md += "| **日线时机** | 阳线放量 | 10 | 量价配合更可信 |\n\n"
    md += "**阈值**: READY ≥ 50 分 / WATCHING ≥ 25 分 / 其余归 WATCHING 或 INVALID(STALE)\n\n"
    md += "> ⚠️ **免责**: 本报告为纯 PA 结构分析工具输出，不构成交易建议。所有结论基于历史 OHLC 数据的机械规则，未考虑基本面、市场情绪、流动性等因子。\n"

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f"\n✅ 报告已写入: {md_path}")

    # ---- 6. 写 JSON 机读版 ----
    data_dir = os.path.join(project_root, 'data')
    json_out_path = os.path.join(data_dir, 'monthly_combo_results.json')
    output = {
        'generated_at': now,
        'parameters': {
            'weekly_lookback': WEEKLY_LOOKBACK,
            'daily_lookback': DAILY_LOOKBACK,
            'entry_proximity_pct': ENTRY_PROXIMITY_PCT,
            'max_signal_age_months': MAX_SIGNAL_AGE_MONTHS,
        },
        'distribution': dist,
        'candidates': results,
    }
    with open(json_out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=4, ensure_ascii=False, default=str)
    print(f"✅ 数据已写入: {json_out_path}")

    return results, dist


if __name__ == '__main__':
    main()
