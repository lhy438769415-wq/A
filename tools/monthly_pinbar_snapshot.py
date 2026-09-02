"""
tools/monthly_pinbar_snapshot.py
======================================
[极简小工具] 月底收盘时扫描"本月最新月K"是否出现区间破位 Pinbar (Spring) 标的。

设计理念（用户原话）：
  - 非常低频操作（每月一次），不与日线/周线的功能耦合
  - 只关心：本月最新那根月K有没有 pinbar 形态
  - 可配合 WorkBuddy 每月定时任务使用

与现有 scan_engine 的关系：
  - 复用 MonthlyRangeBreakStrategy.calculate_signals（策略逻辑不变）
  - 复用 data_provider.get_monthly_bars（数据源不变）
  - 不调用 scan_engine / signal_tracker / notifier（不归档、不推送、不追踪）
  - 不依赖 GUI 或周线/日线任何编排

用法：
  cd 项目根目录
  PYTHONPATH="项目根目录" .venv/Scripts/python.exe tools/monthly_pinbar_snapshot.py [--limit N]

产出：
  strategy_lab/monthly_pinbar_snapshot.md   （人读版：本月 pinbar 清单）
  data/monthly_pinbar_snapshot.json          （机读版）

注意：纯只读分析，不动库、不归档、不推 Discord。
"""

import json
import os
import sys
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List

import pandas as pd
import numpy as np

# ---- 项目路径 ----
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from core.data_provider import get_monthly_bars, get_stock_data, get_stock_list, get_stock_name
from core.strategy_registry import StrategyRegistry


# =====================================================================
# 单只股票：检查最新一根月K是否为 Spring Pinbar
# =====================================================================

def check_latest_month(code: str) -> Optional[Dict[str, Any]]:
    """
    检查一只股票的最新月K是否触发信号。

    返回: 命中 dict (含 entry/sl/tp/rr/评级) 或 None (无信号)。
    """
    try:
        df = get_monthly_bars(code, limit=300)
        if df is None or len(df) < 25:   # LLV(20) 需要至少 21+ 根月线
            return None

        strat = StrategyRegistry.get_strategy('STRATEGY_MONTHLY_RANGE_BREAK')
        df = strat.calculate_signals(df)

        # 只看最后一根月K
        last = df.iloc[-1]
        if not last.get('signal_mrb', False):
            return None

        # 有信号 → 提取订单位 + 评级
        entry = float(last.get('entry_mrb', np.nan))
        sl = float(last.get('sl_mrb', np.nan))
        tp = float(last.get('tp_mrb', np.nan))

        if np.isnan(entry) or np.isnan(sl):
            return None

        risk = entry - sl
        reward = (tp - entry) if not np.isnan(tp) else 0
        rr = round(reward / risk, 1) if risk > 0 else 0

        # 四因子评级
        try:
            rating = strat.compute_rating(df, timeframe='monthly')
            ev_score = rating.raw_score if rating else 0
            rating_dict = rating.to_dict() if rating else None
        except Exception:
            ev_score = 0
            rating_dict = None

        # 最新日线收盘价（纯信息参考，不参与判定）
        latest_close = None
        try:
            ddf = get_stock_data(code, limit=3)
            if ddf is not None and not ddf.empty:
                latest_close = float(ddf.iloc[-1]['close'])
        except Exception:
            pass

        name = get_stock_name(code)

        return {
            'code': code,
            'name': name,
            'signal_month': str(last.get('ym', '')),
            'signal_date': str(last.get('trade_date', '')),
            'entry': round(entry, 2),
            'sl': round(sl, 2),
            'tp': round(tp, 2) if not np.isnan(tp) else None,
            'rr': rr,
            'ev_score': ev_score,
            'rating': rating_dict,
            'latest_close': round(latest_close, 2) if latest_close else None,
            # PA 签名细节
            'lower_shadow_ratio': float(last.get('lower_shadow_ratio_mrb', 0)) if pd.notna(last.get('lower_shadow_ratio_mrb')) else None,
            'break_depth_pct': float(last.get('break_depth_pct_mrb', 0)) if pd.notna(last.get('break_depth_pct_mrb')) else None,
            'shadow_pct': float(last.get('shadow_pct_mrb', 0)) if pd.notna(last.get('shadow_pct_mrb')) else None,
        }
    except Exception as e:
        logger.debug(f"检查 {code} 异常: {e}")
        return None


# =====================================================================
# 全市场扫描
# =====================================================================

def run_snapshot(limit: int = 0, progress_callback=None, cancel_event=None) -> List[Dict]:
    """
    扫描全市场，返回本月出现 Spring Pinbar 的标的列表。
    """
    def _safe_progress(done, total, info=None):
        if progress_callback:
            try:
                progress_callback(done, total, info)
            except Exception:
                pass

    all_codes = get_stock_list()
    if not all_codes:
        print("❌ 获取股票列表失败")
        return []

    if limit > 0:
        all_codes = all_codes[:limit]

    total = len(all_codes)
    hits = []
    completed = 0
    MAX_WORKERS = 4

    print(f"\n🌕 月线 Pinbar 月底快照")
    print(f"   范围: {total} 只 | 线程: {MAX_WORKERS}")
    print(f"   检查: 每只取最新一根月K，看是否 signal_mrb=True\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_latest_month, code): code for code in all_codes}

        for future in as_completed(futures):
            if cancel_event and cancel_event.is_set():
                print("\n🛑 用户终止扫描")
                break

            completed += 1
            if completed % 100 == 0:
                print(f"  ⏳ 进度: {completed}/{total}... 已发现 {len(hits)} 只")

            _safe_progress(completed, total, len(hits))

            code = futures[future]
            try:
                result = future.result()
                if result:
                    hits.append(result)
                    pct = ''
                    if result.get('latest_close') and result.get('entry'):
                        p = (result['latest_close'] - result['entry']) / result['entry'] * 100
                        pct = f" (现价距买点 {p:+.1f}%)"
                    print(f"  ✨ {result['code']} {result['name']} — {result['signal_month']}{pct}")
            except Exception as e:
                logger.debug(f"{code} 结果获取失败: {e}")

    print(f"\n✅ 扫描完成! {total} 只中本月有 {len(hits)} 只出现月线 Spring Pinbar")
    return hits


# =====================================================================
# 报告生成
# =====================================================================

def generate_reports(hits: List[Dict], total_scanned: int = 0):
    """生成 Markdown 报告 + JSON 数据文件。"""
    project_root = PROJECT_ROOT
    now = pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')

    # ---- Markdown ----
    lab_dir = os.path.join(project_root, 'strategy_lab')
    os.makedirs(lab_dir, exist_ok=True)
    md_path = os.path.join(lab_dir, 'monthly_pinbar_snapshot.md')

    md = f"# 🌕 月线 Pinbar 月底快照\n\n"
    md += f"> **快照时间**: {now}\n"
    md += f"> **扫描范围**: {total_scanned} 只 A 股\n"
    md += f"> **命中数量**: {len(hits)} 只\n"
    md += f"> **检查内容**: 每只股票最新一根月 K 线是否满足「区间底部破位 Spring Pinbar」5 条件\n\n"

    if not hits:
        md += "## 结果\n\n**本月未发现月线 Spring Pinbar 标的。**\n\n"
        md += "> 下月底再来看。\n"
    else:
        md += "## 🎯 本月命中的 Spring Pinbar\n\n"
        md += "| 代码 | 名称 | 信号月 | 买点 (Buy Stop) | 止损 (SL) | 目标 (TP) | 盈亏比 | 现价 | 距买点 | 下影比 | 刺破深 |\n"
        md += "|:---:|:---|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n"

        for h in hits:
            tp_str = f"{h['tp']:.2f}" if h.get('tp') else "N/A"
            rr_str = f"1:{h['rr']:.1f}" if h['rr'] > 0 else "N/A"
            close_str = f"{h['latest_close']:.2f}" if h.get('latest_close') else "—"
            if h.get('latest_close') and h.get('entry'):
                dist = (h['latest_close'] - h['entry']) / h['entry'] * 100
                dist_str = f"{dist:+.1f}%"
            else:
                dist_str = "—"
            shadow = f"{h['lower_shadow_ratio']:.1f}" if h.get('lower_shadow_ratio') is not None else "—"
            depth = f"{h['break_depth_pct']:.1f}%" if h.get('break_depth_pct') is not None else "—"

            md += (f"| `{h['code']}` | **{h['name']}** | {h['signal_month']} "
                   f"| >={h['entry']:.2f} | *{h['sl']:.2f}* | {tp_str} "
                   f"| {rr_str} | {close_str} | {dist_str} "
                   f"| {shadow} | {depth} |\n")

        # PA 因子明细
        md += "\n---\n\n## 🔬 PA 因子详情\n\n"
        for h in hits:
            md += f"### `{h['code']}` {h['name']}\n\n"
            r = h.get('rating')
            if r and r.get('factors'):
                md += "| 因子 | 值 | 通过 | 权重 | 说明 |\n|:---|:---:|:---:|:---:|:---|\n"
                for f in r['factors']:
                    hit_mark = "✅" if f.get('hit') else "❌"
                    md += f"| {f['name']} | {f['value']} | {hit_mark} | {f.get('weight', 0)} | {f.get('note', '')} |\n"
                md += f"\n**原始分**: {r.get('raw_score')} | **评分**: {r.get('score')} | **EV**: {'正' if (r.get('raw_score') or 0) > 0 else '负/平'}\n\n"
            else:
                md += "(评级计算异常)\n\n"
            md += "---\n\n"

    md += "\n> ⚠️ **免责**: 本快照为纯 PA 结构识别工具输出，不构成交易建议。仅基于 OHLC 机械规则，未考虑基本面/流动性/市场情绪。\n"
    md += "\n> **方法论**: Al Brooks Price Action — 区间下沿假跌破(Spring)+长下影收回+阳线实体。详见 `core/strategies/monthly_range_break_strategy.py`。\n"

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    # ---- JSON ----
    data_dir = os.path.join(project_root, 'data')
    json_path = os.path.join(data_dir, 'monthly_pinbar_snapshot.json')

    output = {
        'snapshot_time': now,
        'total_scanned': total_scanned,
        'hits_count': len(hits),
        'check_description': '每只股票最新一根月K是否为区间底部破位Spring Pinbar',
        'hits': hits,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=4, ensure_ascii=False, default=str)

    print(f"📄 报告: {md_path}")
    print(f"📊 数据: {json_path}")
    return md_path, json_path


# =====================================================================
# 入口
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description='月线 Pinbar 月底快照 — 检查本月最新月K是否有 Spring Pinbar')
    parser.add_argument('--limit', type=int, default=0, help='限制扫描数量（0=全市场）')
    args = parser.parse_args()

    hits = run_snapshot(limit=args.limit)
    generate_reports(hits, total_scanned=len(get_stock_list()) if args.limit == 0 else args.limit)


if __name__ == '__main__':
    main()
