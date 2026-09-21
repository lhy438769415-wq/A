# archive/tools/backtest_gap_h2_new_exit.py
"""
[回测] GAP H2 新出场方案 (分批止盈 + 跟踪止损) vs 原出场方案 — 2x2 对照

目的 (对应 2026-09-20 锁定的出场设计):
  - 任务1: C0+重置(纯) 信号 + 新出场  → EV 几何?
  - 任务2: C0 原版 信号 + 新出场     → 是否比 C0 原版 + 旧出场 EV 更好?

新出场状态机 (仅动出场/持仓管理层, 信号生成沿用现有策略类):
  设 E=入场(信号K高点 Buy Stop 次日), SL0=缺口地板, R=E-SL0, T2=E+2R, SH=突破腿最高点
  阶段A 等待2R : 任意 post-entry 根 high >= T2 → 平半仓(限价 T2)
  阶段B 剩半仓跟踪: high 从未 > SH → SL 维持 SL0; 首次 high > SH → SL 一次性移到「信号K最低价」(固定)
  阶段C 新高后反转出场(阶段B后生效): 以「最近一次新高」为基准计数 LL→HH→LL:
       新高(>running_peak) → 重置计数; 跌破「前次回踩低点」(rev_trigger_low) → 反转确认离场
       优先级: SL(信号K低点) 硬底线优先; 低2 反转是底线之上主动离场

前过滤 (与旧出场一致, 保证公平): 缺口回填撤单 / 测量目标先达作废 / 30bar 超时 / 入场触发。
  注: 测量目标(tp_gap_h2)虽不再作为出场线, 仍作为「入场前先达即作废」的 setup 有效性过滤保留,
      以隔离"仅出场机械"的差异。

复用: evaluate_trade (旧出场) / LIFECYCLE_TIMEOUT_BARS 直接 import 自 backtest_gap_h2.py。
"""
import os
import sys
import time
import logging
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
from core.data_provider import get_stock_data, get_stock_list
from core.calculator import add_indicators
from config import settings
from core.log_config import get_logger
from core.strategies.gap_h2_strategy import GapH2Strategy
from core.strategies.gap_h2_enhanced_strategy import GapH2EnhancedStrategy

logger = get_logger(__name__)


# =====================================================================
# 新出场引擎: 分批止盈 + 跟踪止损 + 反转出场
# =====================================================================
def evaluate_trade_new_exit(df, signal_idx, timeout=LIFECYCLE_TIMEOUT_BARS):
    """新出场方案的单笔交易模拟 (仅出场/持仓管理层与旧版不同)。"""
    try:
        sig_row = df.iloc[signal_idx]
        E = sig_row['entry_gap_h2']
        SL0 = sig_row['sl_gap_h2']
        sig_low = sig_row['low']                 # 信号K(第二次回调 LHLL)最低价 = 规则2 止损位
        gap_floor = sig_row['gap_h2_floor_exact']
        bsg = sig_row['bars_since_breakout_h2']
        tp_measured = sig_row['tp_gap_h2']       # 仅用于"入场前先达作废"过滤

        # 动态挂单: 策略已给出真正的收口成交K(entry_bar_gap_h2);
        # 有则仅在指定K成交, 否则回退旧"首根 high>=E"规则(兼容无该列的策略)。
        _eb = sig_row.get('entry_bar_gap_h2', np.nan)
        entry_bar = (_eb if (isinstance(_eb, (int, float, np.integer, np.floating))
                             and not pd.isna(_eb)) else None)
        i_entry = (int(entry_bar) - (signal_idx + 1)) if entry_bar is not None else None

        if pd.isna(E) or pd.isna(SL0) or pd.isna(sig_low) or pd.isna(gap_floor):
            return {'status': 'ERROR', 'reason': 'NaN'}
        if pd.isna(tp_measured):
            tp_measured = np.inf

        # 突破腿最高点 SH (信号出现前结构顶点): 突破组内 index<=signal 的最高 high
        pos_arr = np.arange(len(df))
        grp_mask = (df['bars_since_breakout_h2'].values == bsg)
        sh = float(df['high'].values[grp_mask & (pos_arr <= signal_idx)].max())

        # 回调特征 (复用口径)
        pb_df = df[(df['bars_since_breakout_h2'] == bsg) & (df.index < df.index[signal_idx])]
        pb_bars = len(pb_df)
        sig_q = float(sig_row.get('sig_bar_quality_h2', 0))
        base = {'sig_quality': sig_q, 'pb_bars': pb_bars}

        post = df.iloc[signal_idx + 1:]
        if post.empty:
            return {**base, 'status': 'PENDING', 'reason': 'EOF'}

        # 状态变量
        status = 'WAITING'
        bars_waited = 0
        entry_date = actual_entry = None
        realized_r = 0.0          # 已实现 R (来自半仓止盈)
        remaining_frac = 1.0       # 剩余仓位比例
        half_done = False          # 阶段A 是否半仓止盈
        sh_crossed = False         # 阶段B 是否触发
        current_sl = SL0           # 当前硬底线 (floor -> sig_low)
        running_peak = sh          # 阶段C 运行峰值 (新高基准)
        prior_pullback_low = None  # 最近新高之后的回踩最低 low
        rev_trigger_low = None     # 反转触发线 (前次回踩低点)
        half_exit_date = None      # 阶段A 2R 半仓止盈日 (成册绘图用)
        sh_cross_date = None       # 阶段B 首次过 SH 新高移损日 (成册绘图用)

        n = len(post)
        for i in range(n):
            row = post.iloc[i]
            row_date = row['date'] if 'date' in row else post.index[i]
            high = float(row['high']); low = float(row['low']); op = float(row['open'])

            if status == 'WAITING':
                # 动态挂单: 指定成交K直接成交; 等待期内其余K不成交(挂单价已下移)
                if entry_bar is not None:
                    if i == i_entry:
                        actual_entry = max(E, op)
                        entry_date = row_date
                        status = 'IN_TRADE'
                        R = actual_entry - SL0
                        if R <= 0:
                            return {**base, 'status': 'ERROR', 'reason': 'R<=0'}
                        T2 = actual_entry + 2 * R
                        if low <= current_sl:
                            exit_p = min(current_sl, op)
                            total_r = remaining_frac * (exit_p - actual_entry) / R
                            return {**base, 'status': 'LOSS', 'entry_date': entry_date, 'exit_date': row_date,
                                    'entry_price': actual_entry, 'exit_price': exit_p,
                                    'half_exit_price': np.nan, 'total_r': total_r,
                                    'half_exit_date': None, 'sh_cross_date': None, 'reason': 'same_day_stop'}
                        if high >= T2:
                            hp = max(T2, op)
                            realized_r += remaining_frac * 0.5 * (hp - actual_entry) / R
                            remaining_frac *= 0.5
                            half_done = True
                            half_exit_date = row_date
                    continue
                # 回退旧逻辑 (无 entry_bar 的策略)
                bars_waited += 1
                if low < gap_floor - 1e-3:
                    return {**base, 'status': 'INVALIDATED', 'reason': 'gap_filled'}
                if high >= tp_measured:
                    return {**base, 'status': 'VOIDED', 'reason': 'tp_before_entry'}
                if bars_waited > timeout:
                    return {**base, 'status': 'TIMEOUT', 'reason': 'timeout'}
                if high >= E:
                    actual_entry = max(E, op)
                    entry_date = row_date
                    status = 'IN_TRADE'
                    R = actual_entry - SL0
                    if R <= 0:
                        return {**base, 'status': 'ERROR', 'reason': 'R<=0'}
                    T2 = actual_entry + 2 * R
                    # same-day 止损 (理论上不发生, 但保留)
                    if low <= current_sl:
                        exit_p = min(current_sl, op)
                        total_r = remaining_frac * (exit_p - actual_entry) / R
                        return {**base, 'status': 'LOSS', 'entry_date': entry_date, 'exit_date': row_date,
                                'entry_price': actual_entry, 'exit_price': exit_p,
                                'half_exit_price': np.nan, 'total_r': total_r,
                                'half_exit_date': None, 'sh_cross_date': None, 'reason': 'same_day_stop'}
                    # same-day 2R 半仓
                    if high >= T2:
                        hp = max(T2, op)
                        realized_r += remaining_frac * 0.5 * (hp - actual_entry) / R
                        remaining_frac *= 0.5
                        half_done = True
                        half_exit_date = row_date
                    continue

            # ---- IN_TRADE ----
            R = actual_entry - current_sl  # 动态风险 (移动止损后变化)
            # 1) 硬底线优先 (SL)
            if low <= current_sl:
                exit_p = min(current_sl, op)
                total_r = realized_r + remaining_frac * (exit_p - actual_entry) / (actual_entry - SL0)
                win = total_r > 1e-4
                return {**base, 'status': 'WIN' if win else 'LOSS', 'entry_date': entry_date, 'exit_date': row_date,
                        'entry_price': actual_entry, 'exit_price': exit_p,
                        'half_exit_price': (T2 if half_done else np.nan), 'total_r': total_r,
                        'half_exit_date': half_exit_date, 'sh_cross_date': sh_cross_date,
                        'reason': 'stop_loss'}

            # 2) 阶段A: 2R 半仓止盈 (未触发过)
            if (not half_done) and high >= T2:
                hp = max(T2, op)
                realized_r += remaining_frac * 0.5 * (hp - actual_entry) / (actual_entry - SL0)
                remaining_frac *= 0.5
                half_done = True
                half_exit_date = row_date

            # 3) 阶段B: 首次过 SH → 移动止损到信号K低点(固定)
            if (not sh_crossed) and high > sh + 1e-9:
                sh_crossed = True
                current_sl = sig_low
                running_peak = sh
                prior_pullback_low = low
                rev_trigger_low = None
                sh_cross_date = row_date

            # 4) 阶段C: 新高后的反转出场 (LL->HH->LL)
            if sh_crossed:
                if high > running_peak + 1e-9:
                    running_peak = high
                    rev_trigger_low = prior_pullback_low   # 前次回踩低点成为反转触发线
                    prior_pullback_low = low               # 开启新回踩基线
                else:
                    if prior_pullback_low is None:
                        prior_pullback_low = low
                    else:
                        prior_pullback_low = min(prior_pullback_low, low)
                    if rev_trigger_low is not None and low < rev_trigger_low - 1e-9:
                        exit_p = min(rev_trigger_low, op)
                        total_r = realized_r + remaining_frac * (exit_p - actual_entry) / (actual_entry - SL0)
                        win = total_r > 1e-4
                        return {**base, 'status': 'WIN' if win else 'LOSS', 'entry_date': entry_date,
                                'exit_date': row_date, 'entry_price': actual_entry, 'exit_price': exit_p,
                                'half_exit_price': (T2 if half_done else np.nan), 'total_r': total_r,
                                'half_exit_date': half_exit_date, 'sh_cross_date': sh_cross_date,
                                'reason': 'reversal_exit'}

        if status == 'IN_TRADE':
            return {**base, 'status': 'HOLDING', 'entry_date': entry_date, 'entry_price': actual_entry,
                    'half_exit_price': (T2 if half_done else np.nan), 'total_r': realized_r,
                    'half_exit_date': half_exit_date, 'sh_cross_date': sh_cross_date,
                    'reason': 'eof'}
        return {**base, 'status': 'PENDING', 'reason': 'not_triggered'}
    except Exception as e:
        return {'status': 'ERROR', 'reason': str(e)}


# =====================================================================
# 配置表 (2x2: 信号基底 x 出场方案)
# =====================================================================
CONFIGS = [
    {'label': 'A C0原版+旧出场', 'kind': 'orig', 'exit': 'old',
     'params': {}, 'desc': '真缺口/无重置/地板止损/40-2 + 原全仓TP(测量目标)/SL'},
    {'label': 'B C0原版+新出场', 'kind': 'orig', 'exit': 'new',
     'params': {}, 'desc': 'C0 信号 + 新出场(2R半仓+跟踪+反转)'},
    {'label': 'C C0+重置+旧出场', 'kind': 'enh', 'exit': 'old',
     'params': dict(gap_mode='true', reset_on_newhigh=True, sl_mode='floor', max_pb=40, min_pb=2),
     'desc': '真缺口/有重置/地板止损/40-2 + 原全仓TP/SL'},
    {'label': 'D C0+重置+新出场', 'kind': 'enh', 'exit': 'new',
     'params': dict(gap_mode='true', reset_on_newhigh=True, sl_mode='floor', max_pb=40, min_pb=2),
     'desc': 'C0+重置 信号 + 新出场(2R半仓+跟踪+反转) [任务1]'},
]


def _make_strategy(cfg):
    if cfg['kind'] == 'orig':
        return GapH2Strategy()
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
            strat = _make_strategy(cfg)
            sdf = strat.calculate_signals(df.copy())
            sig_col = 'signal_gap_h2'
            indices = [i for i, v in enumerate(sdf[sig_col].fillna(False).values) if v]
            for idx in indices:
                if cfg['exit'] == 'old':
                    t = evaluate_trade(sdf, idx)
                    # 旧出场: rr 由 backtest_single 口径计算 (evaluate_trade 本身不产出 rr)
                    if t['status'] in ('WIN', 'LOSS'):
                        ep = t.get('exit_price', np.nan)
                        en = t.get('entry_price', np.nan)
                        sl = sdf.iloc[idx]['sl_gap_h2']
                        risk = (en - sl) if (pd.notna(en) and pd.notna(sl)) else np.nan
                        t['total_r'] = (ep - en) / risk if (pd.notna(risk) and risk > 0) else 0.0
                    t['half_exit_price'] = np.nan
                else:
                    t = evaluate_trade_new_exit(sdf, idx)
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
    if limit > 0:
        codes = codes[:limit]
    if stock_limit > 0:
        codes = codes[:stock_limit]
    n = len(codes)
    print(f"[*] 扫描 {n} 只标的 (全历史日线)")
    print(f"[*] 新出场引擎: evaluate_trade_new_exit (2R半仓+跟踪+反转)")
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
            r = f.result()
            if r:
                all_trades.extend(r)
    print(f"[*] 回测完成, 耗时 {time.time()-t0:.1f}s, 总交易记录 {len(all_trades)} 条")
    return all_trades


# =====================================================================
# 专业量化指标汇总 (按 total_r 口径)
# =====================================================================
def _metrics(trades):
    total = len(trades)
    wins = [t for t in trades if t['status'] == 'WIN']
    losses = [t for t in trades if t['status'] == 'LOSS']
    inv = [t for t in trades if t['status'] == 'INVALIDATED']
    void = [t for t in trades if t['status'] == 'VOIDED']
    tout = [t for t in trades if t['status'] == 'TIMEOUT']
    hold = [t for t in trades if t['status'] in ('HOLDING', 'PENDING')]
    err = [t for t in trades if t['status'] == 'ERROR']
    done = len(wins) + len(losses)
    m = {'total': total, 'win': len(wins), 'loss': len(losses), 'invalid': len(inv),
         'void': len(void), 'timeout': len(tout), 'hold': len(hold), 'err': len(err), 'done': done}
    if done > 0:
        wr = len(wins) / done
        avg_w = float(np.mean([t['total_r'] for t in wins])) if wins else 0.0
        avg_l = float(np.mean([t['total_r'] for t in losses])) if losses else 0.0
        ev = wr * avg_w + (1 - wr) * avg_l
        sum_w = sum(t['total_r'] for t in wins)
        sum_l = sum(t['total_r'] for t in losses)
        pf = (sum_w / abs(sum_l)) if sum_l != 0 else float('inf')
        total_r = sum_w + sum_l
        closed = [t for t in trades if t['status'] in ('WIN', 'LOSS') and 'total_r' in t]
        closed.sort(key=lambda t: t.get('exit_date') or t.get('signal_date') or '')
        eq = 0.0; peak = 0.0; mdd = 0.0
        max_loss_streak = max_win_streak = cur_loss = cur_win = 0
        max_single_win = 0.0; max_single_loss = 0.0
        for t in closed:
            rr = t['total_r']
            eq += rr
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
            if rr >= 0:
                cur_win += 1; cur_loss = 0
                max_win_streak = max(max_win_streak, cur_win)
            else:
                cur_loss += 1; cur_win = 0
                max_loss_streak = max(max_loss_streak, cur_loss)
            max_single_win = max(max_single_win, rr)
            max_single_loss = min(max_single_loss, rr)
        m.update({'wr': wr, 'avg_w': avg_w, 'avg_l': avg_l, 'ev': ev, 'pf': pf,
                   'total_r': total_r, 'mdd': mdd, 'max_loss_streak': max_loss_streak,
                   'max_win_streak': max_win_streak, 'max_win_r': max_single_win,
                   'max_loss_r': max_single_loss})
    else:
        m.update({'wr': 0, 'avg_w': 0, 'avg_l': 0, 'ev': 0, 'pf': 0, 'total_r': 0,
                   'mdd': 0, 'max_loss_streak': 0, 'max_win_streak': 0,
                   'max_win_r': 0, 'max_loss_r': 0})
    return m


def analyze(trades):
    rows = {c['label']: [t for t in trades if t['config'] == c['label']] for c in CONFIGS}
    metrics = {label: _metrics(ts) for label, ts in rows.items()}
    for label, ts in rows.items():
        dates = [t.get('signal_date', '') for t in ts if t.get('signal_date')]
        metrics[label]['date_min'] = min(dates) if dates else '-'
        metrics[label]['date_max'] = max(dates) if dates else '-'

    lines = []
    lines.append("# GAP H2 新出场方案（分批止盈+跟踪止损）回测报告\n")
    lines.append("> 数据：本地 `baostock.db` 日线全历史  ")
    lines.append("> 信号生成：复用 `GapH2Strategy`（C0 原版）与 `GapH2EnhancedStrategy`（C0+重置，真缺口/新高重置/地板止损/40-2）。  ")
    lines.append("> 旧出场：`evaluate_trade`（全仓 TP=测量目标 / SL=地板）。  ")
    lines.append("> 新出场：`evaluate_trade_new_exit`（阶段A 2R 半仓止盈 → 阶段B 过突破高点 SH 后移动止损到信号K低点 → 阶段C LL→HH→LL 反转出场，底线优先）。  ")
    lines.append("> 前过滤（新旧一致）：缺口回填撤单 / 测量目标先达作废 / 30bar 超时。\n")

    # 配置说明
    lines.append("## 一、配置说明（2x2：信号基底 × 出场方案）\n")
    lines.append("| 配置 | 信号基底 | 出场方案 | 说明 |")
    lines.append("|------|---------|---------|------|")
    lines.append("| A C0原版+旧出场 | C0 原版 | 旧 (全仓TP/SL) | 生产基线 |")
    lines.append("| B C0原版+新出场 | C0 原版 | 新 (2R半仓+跟踪+反转) | 任务2：C0 套新出场 |")
    lines.append("| C C0+重置+旧出场 | C0+重置 | 旧 (全仓TP/SL) | 仅加新高重置 |")
    lines.append("| D C0+重置+新出场 | C0+重置 | 新 (2R半仓+跟踪+反转) | **任务1：锁定设计** |\n")

    # 核心指标
    lines.append("## 二、核心量化指标对比\n")
    header = ("| 配置 | 总信号 | 已结案 | 胜率 | 平均盈R | 平均亏R | 盈亏比 | "
              "单笔EV | 累计净R | 最大回撤 | 最大连亏 | HOLDING | 信号区间 |")
    sep = "|------|------|------|------|------|------|------|------|------|------|------|------|------|"
    lines.append(header); lines.append(sep)
    for c in CONFIGS:
        m = metrics[c['label']]
        pf = m['pf']
        pf_s = f"{pf:.2f}" if pf != float('inf') else "∞"
        lines.append(
            f"| {c['label']} | {m['total']} | {m['done']} | {m['wr']*100:.1f}% | "
            f"+{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s} | {m['ev']:+.4f} | "
            f"{m['total_r']:+.1f} | {m['mdd']:.2f} | {m['max_loss_streak']} | "
            f"{m['hold']} | {m['date_min']}~{m['date_max']} |"
        )
    lines.append("")

    # 生命周期分布
    lines.append("## 三、信号生命周期 / 出场原因分布\n")
    lines.append("| 配置 | 总信号 | WIN | LOSS | 缺口回填 | 测量先达作废 | 超时 | HOLDING(未结) | ERROR |")
    lines.append("|------|------|------|------|------|------|------|------|------|")
    for c in CONFIGS:
        m = metrics[c['label']]
        lines.append(
            f"| {c['label']} | {m['total']} | {m['win']} | {m['loss']} | {m['invalid']} | "
            f"{m['void']} | {m['timeout']} | {m['hold']} | {m['err']} |"
        )
    lines.append("")

    # 新出场 WIN 的"半仓止盈后"细分（B/D 配置）
    lines.append("## 四、新出场 WIN 的获利结构（B/D：已结盈利单中「半仓2R已触发」占比）\n")
    lines.append("> 半仓2R是否触发，直接决定新出场是否锁定了 +1R 底仓利润。\n")
    for c in [CONFIGS[1], CONFIGS[3]]:
        ts = rows[c['label']]
        wins = [t for t in ts if t['status'] == 'WIN']
        if not wins:
            lines.append(f"- **{c['label']}**：无盈利单。")
            continue
        half_wins = [t for t in wins if (t.get('half_exit_price') is not None) and (not pd.isna(t.get('half_exit_price', np.nan)))]
        lines.append(f"- **{c['label']}**：盈利单 {len(wins)} 笔，其中半仓2R已触发 {len(half_wins)} 笔（{len(half_wins)/len(wins)*100:.1f}%）。")
    lines.append("")

    # 新出场离场原因分布（B/D）
    lines.append("## 四(续)、新出场离场原因分布（B/D：已结单按 reason 拆分）\n")
    lines.append("> 用于解释 EV 变化来源：多少单被「硬底线止损」吃掉，多少单被「反转出场」主动了结，多少单同日止损。\n")
    for c in [CONFIGS[1], CONFIGS[3]]:
        ts = rows[c['label']]
        closed = [t for t in ts if t['status'] in ('WIN', 'LOSS')]
        if not closed:
            lines.append(f"- **{c['label']}**：无已结单。")
            continue
        from collections import Counter
        cnt = Counter(t.get('reason', '?') for t in closed)
        tot = len(closed)
        parts = " / ".join(f"{k}={v}({v/tot*100:.1f}%)" for k, v in cnt.most_common())
        lines.append(f"- **{c['label']}**：已结 {tot} 笔 → {parts}")
    lines.append("")

    # 逐年分布（关键配置）
    lines.append("## 五、逐年胜率与期望值\n")
    for c in CONFIGS:
        ts = rows[c['label']]
        closed = [t for t in ts if t['status'] in ('WIN', 'LOSS') and t.get('signal_date')]
        if not closed:
            continue
        dfr = pd.DataFrame(closed)
        dfr['year'] = dfr['signal_date'].str[:4]
        dfr['is_win'] = dfr['status'] == 'WIN'
        lines.append(f"### {c['label']}")
        lines.append("| 年份 | 样本 | 胜率 | 平均R |")
        lines.append("|------|------|------|------|")
        for yr, g in dfr.groupby('year'):
            w = g['is_win'].mean() * 100
            r = g['total_r'].mean()
            lines.append(f"| {yr} | {len(g)} | {w:.1f}% | {r:+.3f} |")
        lines.append("")

    # 结论
    mA, mB, mC, mD = (metrics[c['label']] for c in CONFIGS)
    lines.append("## 六、结论\n")
    lines.append("### 任务1：C0+重置 信号 + 新出场（D）几何？")
    lines.append(f"- D（新出场）：EV = {mD['ev']:+.4f} R/单，胜率 {mD['wr']*100:.1f}%，已结案 {mD['done']} 笔，累计净R {mD['total_r']:+.1f}，最大回撤 {mD['mdd']:.2f}。")
    lines.append(f"- C（同基底+旧出场）：EV = {mC['ev']:+.4f} R/单，胜率 {mC['wr']*100:.1f}%，累计净R {mC['total_r']:+.1f}。")
    lines.append(f"- 结论：在 C0+重置 基底上，**新出场相对旧出场 EV {'提升' if mD['ev']>mC['ev'] else '下降'} "
                 f"（ΔEV = {mD['ev']-mC['ev']:+.4f}）；信号数 D={mD['total']} / C={mC['total']}。")
    lines.append("")
    lines.append("### 任务2：C0 原版信号 + 新出场（B）是否比 C0 原版+旧出场（A）EV 更好？")
    lines.append(f"- A（C0+旧出场，生产基线）：EV = {mA['ev']:+.4f} R/单，胜率 {mA['wr']*100:.1f}%，累计净R {mA['total_r']:+.1f}。")
    lines.append(f"- B（C0+新出场）：EV = {mB['ev']:+.4f} R/单，胜率 {mB['wr']*100:.1f}%，累计净R {mB['total_r']:+.1f}。")
    lines.append(f"- 结论：C0 原版信号套用新出场后，EV **{'提升' if mB['ev']>mA['ev'] else '下降'}** "
                 f"（ΔEV = {mB['ev']-mA['ev']:+.4f}）；"
                 f"胜率 A={mA['wr']*100:.1f}% → B={mB['wr']*100:.1f}%；"
                 f"结案样本 A={mA['done']} / B={mB['done']}（新出场 HOLDING 未结 {mB['hold']} 笔被排除）。")
    lines.append("")
    lines.append("### 注意事项 / 方法口径")
    lines.append("- **前过滤一致**：新旧出场均保留「缺口回填撤单 / 测量目标先达作废 / 30bar 超时」，故 A↔B、C↔D 的差异仅来自**出场机械**（全仓TP/SL vs 2R半仓+跟踪+反转）。")
    lines.append("- **测量目标**：新出场不再以 `2×地板−前摆低` 作为出场线（已废弃），但仍作为「入场前先达即作废」的 setup 过滤，避免把已跑完的行情误当作信号。")
    lines.append("- **HOLDING 处理**：EOF（数据末尾）仍未结的单子记为 HOLDING 并排除出 EV（与原框架一致）；新出场在「未过 SH 且未达 2R」时缺乏主动出场，HOLDING 占比偏高属设计特征，已单列披露。")
    lines.append("- **SH（突破腿最高点）**：取突破组内信号K之前的最高 high。")
    lines.append("- **阶段C 反转口径**：以「最近新高」为基准，跌破「前次回踩低点」即判 LL→HH→LL 反转确认（新高重置计数）；信号K低点止损为硬底线，优先成交。")
    lines.append("- **R 口径**：以 actual_entry（Buy Stop 实际成交=max(信号K高点, 开盘)）与 SL0（地板）之差为初始 R；移动止损后剩余仓位风险随之变化，total_r 统一折合为「初始整仓 R」。")
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}。EV 为单笔数学期望（以初始整仓 R 计）。")

    report = "\n".join(lines)
    out_path = os.path.join(project_root, 'docs', 'gap_h2_new_exit_backtest_report.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"[*] 报告已写入: {out_path}")

    # 控制台摘要
    print("\n" + "=" * 78)
    print("  GAP H2 新出场 vs 旧出场 (日线全历史)")
    print("=" * 78)
    print(header.replace('|', ' | '))
    for c in CONFIGS:
        m = metrics[c['label']]
        pf_s = f"{m['pf']:.2f}" if m['pf'] != float('inf') else "inf"
        print(f"  {c['label']:16s} | {m['total']:>5d} | {m['done']:>4d} | "
              f"{m['wr']*100:5.1f}% | +{m['avg_w']:.3f} | {m['avg_l']:.3f} | {pf_s:>4s} | "
              f"{m['ev']:+.4f} | {m['total_r']:+.1f} | {m['mdd']:.2f} | {m['max_loss_streak']:>2d} | {m['hold']:>4d} |")
    print("=" * 78)
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
