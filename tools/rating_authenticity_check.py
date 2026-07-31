# -*- coding: utf-8 -*-
"""
tools/rating_authenticity_check.py — 评级真实性验证 (A+ 是否真的优于 D)

核心问题: 生产 compute_rating 给出的字母评级 (A+/A/B/C/D) 是否与实际交易盈亏一致?
  - 若 A+ 的 EV/胜率显著高于 D, 且跨档单调 → 评级有区分度, 可作为推送优先级。
  - 若存在大量"评级倒置"(低档EV高于高档) 或 档间差异不显著 → 评级是噪声,
    应采纳交易员提议: 去字母化 / 不分级全推送 + 精简信息。

数据来源: calibration_signals.jsonl (每行一笔信号, 含生产 letter + 实际 net_R)。
  -> 不需要重跑模拟, 直接复用校准数据集, 反映生产评级的真实表现。

指标口径 (与 rating_calibration_backtest.analyze 一致):
  - 仅 status in (WIN, LOSS) 计入分母 (HOLDING/INVALIDATED/VOIDED/TIMEOUT 撤单类不计入)。
  - 净胜率 = net_win>0 占比; EV = mean(net_R) (R倍数, 成本敏感, 滑点=0上限)。
  - Wilson 95% 置信区间用于判断档间差异是否显著。

输出: 控制台摘要 + 文件 rating_authenticity_report.txt
"""
import os
import sys
import json
import argparse
import math
from collections import defaultdict

import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)
from core.paths import ensure_importable
ensure_importable()


def wilson_ci(k, n, z=1.96):
    """Wilson 95% 置信区间, 返回 (lo, hi) 百分比。n=0 返回 (None,None)。"""
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (center - half) * 100, (center + half) * 100


GRADE_RANK = {'A+': 4, 'A': 3, 'B': 2, 'C': 1, 'D': 0}
GRADE_ORDER = ['A+', 'A', 'B', 'C', 'D']


def load_signals(path):
    recs = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def band_stats(records):
    """对单组 records (已过滤到 WIN/LOSS) 按 letter 统计。返回 {letter: {...}}。"""
    by_letter = defaultdict(list)
    for r in records:
        by_letter[r.get('letter')].append(r)
    out = {}
    for lv in GRADE_ORDER:
        sub = by_letter.get(lv, [])
        n = len(sub)
        if n == 0:
            out[lv] = {'n': 0, 'win_rate': None, 'ci_lo': None, 'ci_hi': None, 'ev': None}
            continue
        k = sum(1 for r in sub if r.get('net_win', 0) > 0)
        wr = k / n * 100
        lo, hi = wilson_ci(k, n)
        ev = float(np.mean([r.get('net_R', 0.0) for r in sub]))
        out[lv] = {'n': n, 'win_rate': round(wr, 2), 'ci_lo': lo, 'ci_hi': hi,
                   'ev': round(ev, 3)}
    return out


def monotonicity(stats):
    """检验 EV 是否随评级升高而单调升高。返回 (inversions, monotone_ok, ev_spread)。"""
    present = [(lv, stats[lv]['ev']) for lv in GRADE_ORDER
               if stats[lv]['n'] > 0 and stats[lv]['ev'] is not None]
    # 按 rank 降序 (A+ -> D)
    present.sort(key=lambda x: -GRADE_RANK[x[0]])
    inversions = 0
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            hi_lv, hi_ev = present[i]
            lo_lv, lo_ev = present[j]
            # 高档应 > 低档; 若高档 <= 低档 计为一次倒置
            if hi_ev <= lo_ev:
                inversions += 1
    ev_spread = None
    if present:
        top = present[0][1]
        bottom = present[-1][1]
        ev_spread = round(top - bottom, 3)
    # 完全单调: 没有倒置且至少两档
    monotone_ok = (inversions == 0 and len(present) >= 2)
    return inversions, monotone_ok, ev_spread


def evaluate_strategy(key, records):
    """单策略评级真实性评估。records 为该策略全部信号 (含撤单类)。"""
    traded = [r for r in records if r.get('status') in ('WIN', 'LOSS')]
    stats = band_stats(traded)
    inv, mono, spread = monotonicity(stats)
    n_traded = len(traded)
    n_total = len(records)
    # 整体基线 (无评级)
    if n_traded:
        k = sum(1 for r in traded if r.get('net_win', 0) > 0)
        base_wr = round(k / n_traded * 100, 2)
        base_ev = round(float(np.mean([r.get('net_R', 0.0) for r in traded])), 3)
    else:
        base_wr, base_ev = None, None
    # A+ 是否显著优于整体基线 (CI 不重叠)
    aplus = stats['A+']
    aplus_beats = None
    if aplus['n'] and aplus['ci_lo'] is not None and base_wr is not None:
        aplus_beats = aplus['ci_lo'] > base_wr  # A+下限高于基线
    return {
        'key': key, 'n_total': n_total, 'n_traded': n_traded,
        'base_wr': base_wr, 'base_ev': base_ev,
        'stats': stats, 'inversions': inv, 'monotone': mono, 'ev_spread': spread,
        'aplus_beats_base': aplus_beats,
    }


def render(report, path):
    L = []
    L.append("🦅 评级真实性验证报告 (A+ vs D 历史实际盈亏对比)")
    L.append("=" * 78)
    L.append("判定逻辑:")
    L.append("  • 评级有意义 ⟺ 各档 EV 随评级升高单调升高(无倒置) 且 A+ 显著优于整体基线")
    L.append("  • 评级是噪声 ⟺ 存在大量评级倒置 或 档间差异不显著 (CI 重叠)")
    L.append("  • 据此决定是否: 去字母化 / 不分级全推送 + 精简信息")
    L.append("=" * 78)
    L.append(f"{'策略':<24}{'n':>7}{'基线WR%':>9}{'基线EV':>8}  | 倒置数 单调?  EV极差")
    L.append("-" * 78)
    verdicts = []
    for key, res in report['strategies'].items():
        flag = '✔可靠' if (res['monotone'] and res['aplus_beats_base']) else ('⚠弱' if res['monotone'] else '✘噪声')
        verdicts.append((key, flag, res))
        L.append(f"{key:<24}{res['n_traded']:>7}{str(res['base_wr']):>9}{str(res['base_ev']):>8}  | "
                 f"{res['inversions']:>5}  {'是' if res['monotone'] else '否':>4}  "
                 f"{str(res['ev_spread']):>7}  [{flag}]")
    L.append("=" * 78)
    # 逐策略明细表
    for key, flag, res in verdicts:
        L.append(f"\n### {key}  [{flag}]  总信号={res['n_total']} 已结案={res['n_traded']}")
        L.append(f"   {'档':<4}{'n':>7}{'净胜率%':>9}{'CI_lo':>8}{'CI_hi':>8}{'EV(R)':>8}")
        for lv in GRADE_ORDER:
            s = res['stats'][lv]
            if s['n'] == 0:
                L.append(f"   {lv:<4}{'—':>7}")
                continue
            L.append(f"   {lv:<4}{s['n']:>7}{str(s['win_rate']):>9}"
                     f"{str(round(s['ci_lo'],1)) if s['ci_lo'] is not None else '—':>8}"
                     f"{str(round(s['ci_hi'],1)) if s['ci_hi'] is not None else '—':>8}"
                     f"{str(s['ev']):>8}")
        L.append(f"   评级倒置数={res['inversions']} | 单调={res['monotone']} | "
                 f"EV极差(A+−D)={res['ev_spread']} | A+显著优于基线={res['aplus_beats_base']}")
    L.append("\n" + "=" * 78)
    # 汇总判定
    n_reliable = sum(1 for _, f, _ in verdicts if f == '✔可靠')
    n_weak = sum(1 for _, f, _ in verdicts if f == '⚠弱')
    n_noise = sum(1 for _, f, _ in verdicts if f == '✘噪声')
    L.append(f"汇总: 可靠 {n_reliable} / 弱 {n_weak} / 噪声 {n_noise} (共 {len(verdicts)} 策略批次)")
    if n_noise >= n_reliable:
        L.append("结论: 多数策略评级无区分度 → 评级字母不可作为推送优先级, 建议去字母化/不分级全推送。")
    elif n_reliable > n_noise:
        L.append("结论: 多数策略评级有区分度 → 可保留分级, 但弱项需单独处理或降级展示。")
    else:
        L.append("结论: 评级区分度参差 → 建议按策略分别决定是否分级。")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))


def main():
    ap = argparse.ArgumentParser(description='评级真实性验证')
    ap.add_argument('--signals', type=str,
                    default=os.path.join(ROOT, 'calibration_signals.jsonl'))
    ap.add_argument('--report', type=str,
                    default=os.path.join(ROOT, 'rating_authenticity_report.txt'))
    args = ap.parse_args()

    print(f"加载信号集: {args.signals}")
    recs = load_signals(args.signals)
    print(f"  总信号 {len(recs)} 笔")

    by_key = defaultdict(list)
    for r in recs:
        by_key[r.get('_key', 'unknown')].append(r)

    report = {'strategies': {}}
    for key in sorted(by_key.keys()):
        report['strategies'][key] = evaluate_strategy(key, by_key[key])

    render(report, args.report)
    print(f"\n报告已写入: {args.report}")


if __name__ == '__main__':
    main()
