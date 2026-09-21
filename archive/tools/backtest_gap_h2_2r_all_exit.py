# archive/tools/backtest_gap_h2_2r_all_exit.py
"""
[回测] 新出场「简化版: 2R 全仓止盈」vs 现有阶段式新出场 vs 旧出场 — 对照 (A/B/D 基准)

目的 (对应 2026-09-20 讨论):
  用户想把新出场简化成「2R 全部止盈」, 即:
    - 入场(信号K高点 Buy Stop 次日) → 整仓持有
    - 任意 post-entry 根 high >= E+2R  → 全仓止盈离场 (limit T2, total_r≈+2R)
    - 任意 post-entry 根 low  <= 地板  → 全仓止损离场 (total_r≈−1R)
    - 去掉阶段式新出场的「过 SH 移止损到信号K低点」「LL→HH→LL 反转出场」等全部机械
  即一个固定的 2:1 盈亏比目标单。对照看它相对 A(C0+旧出场) 的 EV 几何。

出场引擎:
  - old    : evaluate_trade (全仓 TP=测量目标 / SL=地板)         [A]
  - staged : evaluate_trade_new_exit (2R半仓+跟踪+反转)          [B/D 现有]
  - 2r     : evaluate_trade_2r_all (2R全止盈 / 地板全止损)        [B'/D' 本次新增]

前过滤 (三者一致): 缺口回填撤单 / 测量目标先达作废 / 30bar 超时 / 入场触发。
信号生成: 复用 GapH2Strategy(C0 原版) 与 GapH2EnhancedStrategy(C0+重置, 真缺口/新高重置/地板/40-2)。
"""
import os
import sys
import time
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

warnings.filterwarnings('ignore')


def _find_project_root(start: str) -> str:
    p = os.path.abspath(start)
    while p and p != os.path.dirname(p):
        if os.path.isdir(os.path.join(p, 'core')) and os.path.isfile(os.path.join(p, 'core', 'paths.py')):
            return p
        p = os.path.dirname(p)
    return p


project_root = _find_project_root(__file__)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from core.paths import ensure_importable
ensure_importable()

from backtest_gap_h2 import evaluate_trade, LIFECYCLE_TIMEOUT_BARS
from backtest_gap_h2_new_exit import evaluate_trade_new_exit
from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from config import settings
from core.log_config import get_logger

logger = get_logger(__name__)


# =====================================================================
# 简化出场引擎: 2R 全仓止盈 + 地板全仓止损 (无跟踪/反转)
# =====================================================================
def evaluate_trade_2r_all(df, signal_idx, timeout=LIFECYCLE_TIMEOUT_BARS):
    """简化新出场: 整仓, 到 2R 全止盈, 破地板全止损; 去掉阶段式新出场的一切附加机械。"""
    try:
        sig_row = df.iloc[signal_idx]
        E = sig_row['entry_gap_h2']
        SL0 = sig_row['sl_gap_h2']
        gap_floor = sig_row['gap_h2_floor_exact']
        bsg = sig_row['bars_since_breakout_h2']
        tp_measured = sig_row['tp_gap_h2']       # 仅用于"入场前先达即作废"过滤

        # 动态挂单: 策略给出的收口成交K(全局位置索引); 非空时回测须在该K定点成交
        _eb = sig_row.get('entry_bar_gap_h2', np.nan)
        _i_entry = None
        if not pd.isna(_eb):
            try:
                _i_entry = int(round(float(_eb))) - (signal_idx + 1)
            except (TypeError, ValueError):
                _i_entry = None

        try:
            E = float(E); SL0 = float(SL0); gap_floor = float(gap_floor)
        except (TypeError, ValueError):
            return {'status': 'ERROR', 'reason': 'NaN'}
        if pd.isna(E) or pd.isna(SL0) or pd.isna(gap_floor):
            return {'status': 'ERROR', 'reason': 'NaN'}
        if pd.isna(tp_measured):
            tp_measured = np.inf

        pb_df = df[(df['bars_since_breakout_h2'] == bsg) & (df.index < df.index[signal_idx])]
        pb_bars = len(pb_df)
        sig_q = float(sig_row.get('sig_bar_quality_h2', 0))
        base = {'sig_quality': sig_q, 'pb_bars': pb_bars}

        post = df.iloc[signal_idx + 1:]
        if post.empty:
            return {**base, 'status': 'PENDING', 'reason': 'EOF'}

        status = 'WAITING'
        bars_waited = 0
        entry_date = actual_entry = None
        R = T2 = None
        n = len(post)
        for i in range(n):
            row = post.iloc[i]
            row_date = row['date'] if 'date' in row else post.index[i]
            high = float(row['high']); low = float(row['low']); op = float(row['open'])

            if status == 'WAITING':
                bars_waited += 1
                if low < gap_floor - 1e-3:
                    return {**base, 'status': 'INVALIDATED', 'reason': 'gap_filled'}
                if high >= tp_measured:
                    return {**base, 'status': 'VOIDED', 'reason': 'tp_before_entry'}
                if bars_waited > timeout:
                    return {**base, 'status': 'TIMEOUT', 'reason': 'timeout'}
                # 动态挂单: 有 entry_bar 时仅在收口成交K(i==_i_entry)成交,
                # 等待期其余K不提前触发(否则会用最终下移价在阴跌K提前进场);
                # 无 entry_bar(兼容旧数据/其他策略)回退"首根 high>=E"规则
                _enter = False
                if _i_entry is not None:
                    if i == _i_entry:
                        _enter = True
                else:
                    if high >= E:
                        _enter = True
                if _enter:
                    actual_entry = max(E, op)
                    entry_date = row_date
                    status = 'IN_TRADE'
                    r0 = actual_entry - SL0
                    if r0 <= 0:
                        return {**base, 'status': 'ERROR', 'reason': 'R<=0'}
                    t2 = actual_entry + 2.0 * r0
                    # 同日: 先止损(硬底线) 后止盈
                    if low <= SL0:
                        exit_p = min(SL0, op)
                        total_r = (exit_p - actual_entry) / r0
                        return {**base, 'status': 'WIN' if total_r > 1e-4 else 'LOSS',
                                'entry_date': entry_date, 'exit_date': row_date,
                                'entry_price': actual_entry, 'exit_price': exit_p,
                                'half_exit_price': np.nan, 'total_r': total_r, 'reason': 'stop_loss'}
                    if high >= t2:
                        hp = max(t2, op)
                        total_r = (hp - actual_entry) / r0
                        return {**base, 'status': 'WIN' if total_r > 1e-4 else 'LOSS',
                                'entry_date': entry_date, 'exit_date': row_date,
                                'entry_price': actual_entry, 'exit_price': hp,
                                'half_exit_price': np.nan, 'total_r': total_r, 'reason': 'tp_2r'}
                    continue

            # ---- IN_TRADE: 整仓, 固定地板止损 + 固定 2R 止盈 ----
            if status == 'IN_TRADE':
                R = actual_entry - SL0
                T2 = actual_entry + 2.0 * R
                if low <= SL0:
                    exit_p = min(SL0, op)
                    total_r = (exit_p - actual_entry) / R
                    return {**base, 'status': 'WIN' if total_r > 1e-4 else 'LOSS',
                            'entry_date': entry_date, 'exit_date': row_date,
                            'entry_price': actual_entry, 'exit_price': exit_p,
                            'half_exit_price': np.nan, 'total_r': total_r, 'reason': 'stop_loss'}

                if high >= T2:
                    hp = max(T2, op)
                    total_r = (hp - actual_entry) / R
                    return {**base, 'status': 'WIN' if total_r > 1e-4 else 'LOSS',
                            'entry_date': entry_date, 'exit_date': row_date,
                            'entry_price': actual_entry, 'exit_price': hp,
                            'half_exit_price': np.nan, 'total_r': total_r, 'reason': 'tp_2r'}

        if status == 'IN_TRADE':
            return {**base, 'status': 'HOLDING', 'entry_date': entry_date,
                    'entry_price': actual_entry, 'half_exit_price': np.nan,
                    'total_r': 0.0, 'reason': 'eof'}
        return {**base, 'status': 'PENDING', 'reason': 'not_triggered'}
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}


# =====================================================================
# 配置表 (A 旧 / B 阶段式 / B' 2R全止盈 / D 阶段式 / D' 2R全止盈)
# =====================================================================
ENH_PARAMS = dict(gap_mode='true', reset_on_newhigh=True, sl_mode='floor', max_pb=40, min_pb=2)

CONFIGS = [
    {'label': 'A C0原版+旧出场',        'kind': 'orig', 'exit': 'old',    'params': {}},
    {'label': 'B C0原版+阶段式新出场',  'kind': 'orig', 'exit': 'staged', 'params': {}},
    {'label': "B' C0原版+2R全止盈",     'kind': 'orig', 'exit': '2r',     'params': {}},
    {'label': 'D C0+重置+阶段式新出场', 'kind': 'enh',  'exit': 'staged', 'params': ENH_PARAMS},
    {'label': "D' C0+重置+2R全止盈",    'kind': 'enh',  'exit': '2r',     'params': ENH_PARAMS},
]


def _make(cfg):
    if cfg['kind'] == 'orig':
        from core.strategies.gap_h2_strategy import GapH2Strategy
        return GapH2Strategy()
    from core.strategies.gap_h2_enhanced_strategy import GapH2EnhancedStrategy
    return GapH2EnhancedStrategy(**cfg['params'])


def _worker(code, configs, limit):
    try:
        _lim = None if limit <= 0 else limit
        df = get_stock_data(code, limit=_lim)
        if df is None or len(df) < 100:
            return []
        if 'date' not in df.columns and 'trade_date' in df.columns:
            df['date'] = df['trade_date']
        df = add_indicators(df)
        out = []
        for cfg in configs:
            strat = _make(cfg)
            sdf = strat.calculate_signals(df.copy())
            sig_col = 'signal_gap_h2'
            indices = [i for i, v in enumerate(sdf[sig_col].fillna(False).values) if v]
            for idx in indices:
                if cfg['exit'] == 'old':
                    t = evaluate_trade(sdf, idx)
                    if t['status'] in ('WIN', 'LOSS'):
                        ep = t.get('exit_price', np.nan); en = t.get('entry_price', np.nan)
                        sl = sdf.iloc[idx]['sl_gap_h2']
                        risk = (en - sl) if (pd.notna(en) and pd.notna(sl)) else np.nan
                        t['total_r'] = (ep - en) / risk if (pd.notna(risk) and risk > 0) else 0.0
                    t['half_exit_price'] = np.nan
                elif cfg['exit'] == 'staged':
                    t = evaluate_trade_new_exit(sdf, idx)
                else:
                    t = evaluate_trade_2r_all(sdf, idx)
                t['code'] = code
                t['config'] = cfg['label']
                sd = sdf.iloc[idx]['date'] if 'date' in sdf.columns else str(sdf.index[idx])
                t['signal_date'] = str(sd)[:10]
                out.append(t)
        return out
    except Exception as e:
        logger.error(f"worker {code} error: {e}")
        return []


def run(limit=0, stock_limit=0):
    codes = get_stock_list()
    if not codes:
        print("[!] 无法获取股票列表")
        return []
    if stock_limit > 0:
        codes = codes[:stock_limit]
    n = len(codes)
    print(f"[*] 扫描 {n} 只标的 (全历史日线)")
    print(f"[*] 配置: {', '.join(c['label'] for c in CONFIGS)}")
    t0 = time.time()
    all_trades = []
    with ProcessPoolExecutor(max_workers=getattr(settings, 'MAX_WORKERS', 4)) as exe:
        futs = {exe.submit(_worker, c, CONFIGS, limit): c for c in codes}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  [{done}/{n}]", flush=True)
            all_trades.extend(f.result())
    print(f"[*] 回测完成, 耗时 {time.time()-t0:.1f}s, 总记录 {len(all_trades)} 条")
    return all_trades


# =====================================================================
# 指标 & 报告
# =====================================================================
def _metrics(trades):
    total = len(trades)
    wins = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    done = len(wins) + len(losses)
    m = {'total': total, 'win': len(wins), 'loss': len(losses),
         'hold': len([t for t in trades if t['status'] in ('HOLDING', 'PENDING')]),
         'invalid': len([t for t in trades if t['status'] == 'INVALIDATED']),
         'void': len([t for t in trades if t['status'] == 'VOIDED']),
         'timeout': len([t for t in trades if t['status'] == 'TIMEOUT']),
         'err': len([t for t in trades if t['status'] == 'ERROR'])}
    if done > 0:
        wr = len(wins) / done
        avg_w = float(np.mean([t['total_r'] for t in wins])) if wins else 0.0
        avg_l = float(np.mean([t['total_r'] for t in losses])) if losses else 0.0
        ev = wr * avg_w + (1 - wr) * avg_l
        sum_w = sum(t['total_r'] for t in wins)
        sum_l = sum(t['total_r'] for t in losses)
        pf = (sum_w / abs(sum_l)) if sum_l != 0 else float('inf')
        m.update({'wr': wr, 'avg_w': avg_w, 'avg_l': avg_l, 'ev': ev, 'pf': pf,
                   'total_r': sum_w + sum_l})
    else:
        m.update({'wr': 0, 'avg_w': 0, 'avg_l': 0, 'ev': 0, 'pf': 0, 'total_r': 0})
    return m


def analyze(trades):
    rows = {c['label']: [t for t in trades if t['config'] == c['label']] for c in CONFIGS}
    metrics = {label: _metrics(ts) for label, ts in rows.items()}

    def tag(lbl):
        return lbl.split(' ', 1)[0]  # A / B / B' / D / D'

    lines = []
    lines.append("# GAP H2 出场方案对比：2R全止盈(简化) vs 阶段式新出场 vs 旧出场\n")
    lines.append("> 数据：本地 `baostock.db` 日线全历史。信号生成：复用 `GapH2Strategy`（C0 原版）与 `GapH2EnhancedStrategy`（C0+重置，真缺口/新高重置/地板/40-2）。  ")
    lines.append("> 旧出场 `evaluate_trade`：全仓 TP=测量目标(2×地板−前摆低) / SL=地板。  ")
    lines.append("> 阶段式新出场 `evaluate_trade_new_exit`：阶段A 2R半仓止盈 → 阶段B 过突破高点 SH 移止损到信号K低点 → 阶段C LL→HH→LL 反转出场。  ")
    lines.append("> **简化新出场 `evaluate_trade_2r_all`**：整仓，到 2R 全止盈、破地板全止损，无跟踪/反转。固定 2:1 盈亏比。  ")
    lines.append("> 三者前过滤一致（缺口回填/测量先达/30bar超时）。\n")

    lines.append("## 一、核心指标对比\n")
    header = ("| 配置 | 总信号 | 已结案 | 胜率 | 平均盈R | 平均亏R | 盈亏比 | "
              "单笔EV | 累计净R | HOLDING |")
    sep = "|------|------|------|------|------|------|------|------|------|------|"
    lines.append(header); lines.append(sep)
    for c in CONFIGS:
        m = metrics[c['label']]
        pf = m['pf']
        pf_s = f"{pf:.2f}" if pf != float('inf') else "∞"
        lines.append(
            f"| {c['label']} | {m['total']} | {m['win']+m['loss']} | {m['wr']*100:.1f}% | "
            f"+{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s} | {m['ev']:+.4f} | "
            f"{m['total_r']:+.1f} | {m['hold']} |"
        )
    lines.append("")

    lines.append("## 二、与 A(C0+旧出场) 的 EV / 胜率差异\n")
    base = metrics['A C0原版+旧出场']
    lines.append(f"- **基准 A**：EV = {base['ev']:+.4f} R/单，胜率 {base['wr']*100:.1f}%\n")
    for c in CONFIGS[1:]:
        m = metrics[c['label']]
        d_ev = m['ev'] - base['ev']
        d_wr = (m['wr'] - base['wr']) * 100
        verdict = '提升' if d_ev > 0 else '下降'
        lines.append(
            f"- **{tag(c['label'])}** 相对 A：EV {verdict} Δ{d_ev:+.4f} "
            f"（{m['ev']:+.4f} vs {base['ev']:+.4f}）；胜率 Δ{d_wr:+.1f}pt "
            f"（{m['wr']*100:.1f}% vs {base['wr']*100:.1f}%）；累计净R {m['total_r']:+.1f} vs {base['total_r']:+.1f}。"
        )
    lines.append("")

    lines.append("## 三、结论\n")
    b2 = metrics["B' C0原版+2R全止盈"]
    d2 = metrics["D' C0+重置+2R全止盈"]
    b_st = metrics['B C0原版+阶段式新出场']
    d_st = metrics['D C0+重置+阶段式新出场']
    lines.append(f"- **B'(2R全止盈) vs B(阶段式)**：同为 C0 信号，简化后 EV {'提升' if b2['ev']>b_st['ev'] else '下降'} "
                 f"（{b2['ev']:+.4f} vs {b_st['ev']:+.4f}）；胜率 {b2['wr']*100:.1f}% vs {b_st['wr']*100:.1f}%；"
                 f"平均盈R {b2['avg_w']:.3f}(封顶≈2R) vs {b_st['avg_w']:.3f}。")
    lines.append(f"- **D'(2R全止盈) vs D(阶段式)**：同为 C0+重置 信号，简化后 EV {'提升' if d2['ev']>d_st['ev'] else '下降'} "
                 f"（{d2['ev']:+.4f} vs {d_st['ev']:+.4f}）；胜率 {d2['wr']*100:.1f}% vs {d_st['wr']*100:.1f}%。")
    lines.append(f"- **对比 A(旧出场)**：B' EV = {b2['ev']:+.4f}（{'优于' if b2['ev']>base['ev'] else '劣于'} A 的 {base['ev']:+.4f}）；"
                 f"D' EV = {d2['ev']:+.4f}（{'优于' if d2['ev']>base['ev'] else '劣于'} A）。")
    lines.append("- 含义：2R全止盈把每笔盈利封顶在≈2R，亏损固定≈1R（地板），结构上是纯 2:1 目标单；"
                 "与阶段式新出场比，少了「让利润奔跑」(跟踪/反转) 的部分，也少了「移损到信号K低点减亏」的部分。")
    lines.append("")
    lines.append("## 四、口径说明（动态入场改造后）")
    lines.append("")
    lines.append("> 本表为 **H2 动态入场改造后** 的回测：入场不再死钉信号K高点，而是由策略 `_apply_dynamic_entry` "
                 "跟踪扫描——从信号K后若跟一根 LHLL 阴跌K 则挂单下移，直到出现 HH（最低价未扫到缺口地板）才收口成交；"
                 "破地板 / 测量目标先达 / 超时(30bar) 则信号作废（`signal_gap_h2=False`）。")
    lines.append("> 改造效应：约 31% 的「回调后不再创新高」失败信号被取消，故信号基数由原 3629(A/B/B') / 2344(D/D') "
                 "降至 2484(A/B/B') / 1628(D/D')。取消的多为失败信号，故 B'/D' 的 EV 较改造前抬升。")
    lines.append("> 入场定点：`evaluate_trade_2r_all` 读 `entry_bar_gap_h2` 仅在收口成交K成交，等待期不提前触发"
                 "（避免用最终下移价在阴跌K误进场）；无该列时回退「首根 high>=E」旧规则（兼容其他策略）。")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}。EV 为单笔数学期望（以初始整仓 R 计）。")

    report = "\n".join(lines)
    out_path = os.path.join(project_root, 'docs', 'gap_h2_2r_all_exit_compare.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"[*] 报告已写入: {out_path}")

    # 控制台摘要
    print("\n" + "=" * 86)
    print("  GAP H2 出场对照 (日线全历史)")
    print("=" * 86)
    print(header.replace('|', ' | '))
    for c in CONFIGS:
        m = metrics[c['label']]
        pf_s = f"{m['pf']:.2f}" if m['pf'] != float('inf') else "inf"
        print(f"  {c['label']:20s} | {m['total']:>5d} | {m['win']+m['loss']:>4d} | "
              f"{m['wr']*100:5.1f}% | +{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s:>4s} | "
              f"{m['ev']:+.4f} | {m['total_r']:+.1f} | {m['hold']:>4d} |")
    print("=" * 86)
    return metrics


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--limit', type=int, default=0, help='每只标的取最近 N 根 (0=全历史)')
    p.add_argument('--stocks', type=int, default=0, help='仅取前 N 只标的 (0=全部)')
    args = p.parse_args()
    trades = run(limit=args.limit, stock_limit=args.stocks)
    if trades:
        analyze(trades)
