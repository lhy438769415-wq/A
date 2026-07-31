# -*- coding: utf-8 -*-
"""
tools/rating_factor_edge_check.py — 因子级 edge 检验 (回应"因子本身靠不靠谱")

与 rating_authenticity_check 的区别:
  - 前者测"字母档位(A+/D)"是否有区分度 -> 结论: 噪声。
  - 本脚本测"因子本身"是否有 edge: 每个因子 hit=True vs hit=False 的实际 EV 差,
    以及连续 score 对 net_R 的排序力 (Spearman)。这公正回答"用户回测出来的因子是否真有效"。

口径: 仅 status in (WIN, LOSS); EV = mean(net_R)。
  factor_delta = EV(hit=True) - EV(hit=False)。>0 表示该因子指向"赚钱方向"。
  spearman(score, net_R): 连续分值(即使硬编码权重)整体排序力, 范围[-1,1]。
"""
import os
import sys
import json
import argparse
from collections import defaultdict

import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)
from core.paths import ensure_importable
ensure_importable()


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


def spearman(x, y):
    """手写 Spearman (避免依赖 scipy): 秩变换后 Pearson。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 3:
        return None
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    mx, my = rx.mean(), ry.mean()
    num = np.sum((rx - mx) * (ry - my))
    den = np.sqrt(np.sum((rx - mx) ** 2) * np.sum((ry - my) ** 2))
    return float(num / den) if den > 0 else None


def evaluate(key, records):
    traded = [r for r in records if r.get('status') in ('WIN', 'LOSS')]
    if not traded:
        return None
    net = np.array([r.get('net_R', 0.0) for r in traded])
    scores = np.array([r.get('score', 0.0) for r in traded])
    rho = spearman(scores, net)

    # 因子级 edge
    factor_names = set()
    for r in traded:
        fh = r.get('factor_hits', {})
        factor_names.update(fh.keys())
    factor_stats = {}
    for fn in factor_names:
        hit_evs, miss_evs = [], []
        for r in traded:
            fh = r.get('factor_hits', {})
            if fn not in fh:
                continue
            if fh[fn]:
                hit_evs.append(r.get('net_R', 0.0))
            else:
                miss_evs.append(r.get('net_R', 0.0))
        if not hit_evs or not miss_evs:
            continue
        ev_hit = float(np.mean(hit_evs))
        ev_miss = float(np.mean(miss_evs))
        factor_stats[fn] = {
            'n_hit': len(hit_evs), 'n_miss': len(miss_evs),
            'ev_hit': round(ev_hit, 3), 'ev_miss': round(ev_miss, 3),
            'delta': round(ev_hit - ev_miss, 3),
        }
    n_factors = len(factor_stats)
    n_correct = sum(1 for s in factor_stats.values() if s['delta'] > 0)
    return {
        'key': key, 'n_traded': len(traded),
        'spearman': round(rho, 3) if rho is not None else None,
        'n_factors': n_factors, 'n_correct_dir': n_correct,
        'correct_ratio': round(n_correct / n_factors, 2) if n_factors else None,
        'factor_stats': factor_stats,
    }


def render(report, path):
    L = []
    L.append("🦅 因子级 edge 检验 (因子本身 vs 字母档位)")
    L.append("=" * 78)
    L.append("判定: 因子有效 ⟺ 该因子 hit 比 miss 实际 EV 高 (delta>0); 且连续 score 排序力 rho>0。")
    L.append("注意: 即使因子各自有效, 加权求和+切档成 A+/D 仍可能丢失信号 (见上一报告)。")
    L.append("=" * 78)
    L.append(f"{'策略':<24}{'n':>7}{'rho':>8}{'因子数':>7}{'正向%':>7}")
    L.append("-" * 78)
    for key, res in report['strategies'].items():
        cr = (str(round(res['correct_ratio'] * 100)) + '%') if res['correct_ratio'] is not None else '—'
        L.append(f"{key:<24}{res['n_traded']:>7}{str(res['spearman']):>8}"
                 f"{res['n_factors']:>7}{cr:>7}")
    L.append("=" * 78)
    for key, res in report['strategies'].items():
        L.append(f"\n### {key}  (n={res['n_traded']}, rho={res['spearman']}, "
                 f"正向因子 {res['n_correct_dir']}/{res['n_factors']})")
        L.append(f"   {'因子':<12}{'n_hit':>7}{'n_miss':>7}{'EV(hit)':>9}{'EV(miss)':>9}{'delta':>8}  方向")
        for fn, s in sorted(res['factor_stats'].items(), key=lambda kv: -kv[1]['delta']):
            arrow = '▲赚钱' if s['delta'] > 0 else ('▼亏钱' if s['delta'] < 0 else '—')
            L.append(f"   {fn:<12}{s['n_hit']:>7}{s['n_miss']:>7}{s['ev_hit']:>9}"
                     f"{s['ev_miss']:>9}{s['delta']:>8}  {arrow}")
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    print('\n'.join(L))


def main():
    ap = argparse.ArgumentParser(description='因子级 edge 检验')
    ap.add_argument('--signals', type=str,
                    default=os.path.join(ROOT, 'calibration_signals.jsonl'))
    ap.add_argument('--report', type=str,
                    default=os.path.join(ROOT, 'rating_factor_edge_report.txt'))
    args = ap.parse_args()
    recs = load_signals(args.signals)
    print(f"加载 {len(recs)} 笔信号")
    by_key = defaultdict(list)
    for r in recs:
        by_key[r.get('_key', 'unknown')].append(r)
    report = {'strategies': {}}
    for key in sorted(by_key.keys()):
        res = evaluate(key, by_key[key])
        if res:
            report['strategies'][key] = res
    render(report, args.report)
    print(f"\n报告: {args.report}")


if __name__ == '__main__':
    main()
