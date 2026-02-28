# -*- coding: utf-8 -*-
"""
周线 结构性测量缺口策略独立扫描器 (scanner_weekly_gap.py)

独立于日线系统，直接调用 Baostock frequency='w' 获取周线数据。
不修改 data_provider / database / scanner 等现有核心模块。

# 用法:
#   1. 在周末手动运行网络同步：python tools/update_weekly_db.py
#   2. 运行纯离线本地扫描：python tools/scanner_weekly_gap.py [--limit N] [--weeks D]
#
# 会自动在 strategy_lab 目录下生成每周埋伏计划报告 (weekly_struct_gap_plan.md)
# 并在 data 目录下生成监控文件 (weekly_gap_watchlist.json)
"""
import sys, os, io, argparse, logging, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import baostock as bs

from core.calculator import add_indicators
from core.strategies.structural_gap_strategy import StructuralGapStrategy
import core.data_provider as dp
from tools.notifier import generate_chart_bytes, stitch_images, send_discord_image, send_discord_message

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =====================================================
# 从本地周线数据库极速提取数据
# =====================================================
def fetch_weekly_data(full_code: str, weeks: int = 200) -> pd.DataFrame:
    return dp.get_stock_data_weekly(full_code, limit=weeks)


# =====================================================
# 主扫描逻辑
# =====================================================
def scan_weekly_gap(all_codes: list, recent_weeks: int = 4) -> dict:
    """
    扫描全市场周线 Structural Gap 信号
    """
    strategy = StructuralGapStrategy()
    results_gap = []
    
    for i, code in enumerate(all_codes):
        if (i + 1) % 50 == 0:
            sys.stdout.write(f"\r  ⏳ 扫描进度: {i+1}/{len(all_codes)}... 累计命中: {len(results_gap)}")
            sys.stdout.flush()
        
        try:
            df = fetch_weekly_data(code, weeks=300) # Increased to 300 to ensure we have enough history for 60-week lookup
            if df is None or len(df) < 100:
                continue
            
            df = add_indicators(df)
            df = strategy.calculate_signals(df)
            
            # 检查最近 N 周是否有确认信号
            recent = df.tail(recent_weeks)
            
            # 第一优先级：缺口确认买点 (优先检查这个)
            gt_rows = recent[recent.get('signal_struct_gap_confirm', pd.Series(dtype=bool)) == True]
            
            # 第二优先级：如果近期没有确认买点，但存在处于悬空期的强力突破 (is_breakout)，且缺口依然存活
            if gt_rows.empty:
                # 寻找最近的一个 breakout
                recent_breakouts = df[df.get('is_breakout', pd.Series(dtype=bool)) == True].tail(1)
                if not recent_breakouts.empty:
                    bo_date = recent_breakouts.index[-1]
                    # 检查 breakout 之后的这段时间是否还活着
                    idx_bo = df.index.get_loc(bo_date)
                    recent_slice = df.iloc[idx_bo:]
                    if recent_slice['struct_gap_open'].all() and len(recent_slice) <= strategy.MAX_PULLBACK_WINDOW:
                        # 这是一个正在孕育的潜伏缺口，我们为其制造一个人工追踪点供观察
                        # 注意此时还没确认，没法算确定的 Entry 和 TP
                        bo_row = recent_breakouts.iloc[-1]
                        
                        # Gap Floor calculation based on rolling 60 days before the breakout var
                        past_highs = df['high'].iloc[max(0, idx_bo - 60):max(0, idx_bo - 1)]
                        temp_sl = past_highs.max() if not past_highs.empty else bo_row['low']
                        
                        # TP calculation based on measuring
                        past_lows = df['low'].iloc[max(0, idx_bo - 60):max(0, idx_bo - 1)]
                        prior_sl = past_lows.min() if not past_lows.empty else bo_row['low']
                        
                        current_min_low = recent_slice['low'].min()
                        temp_mid = (current_min_low + temp_sl) / 2
                        temp_tp = 2 * temp_mid - prior_sl
                        
                        q = bo_row.get('sig_bar_quality', 0)
                        
                        name = dp.get_stock_name(code)
                        results_gap.append({
                            'code': code,
                            'name': name,
                            'date': bo_row['date'] if 'date' in bo_row else str(bo_row.name),
                            'entry': df['high'].iloc[-1] + 0.01, # Buy stop above this week's high
                            'sl': temp_sl,
                            'tp': temp_tp,
                            'rr': round((temp_tp - df['high'].iloc[-1]) / (df['high'].iloc[-1] - temp_sl), 1) if df['high'].iloc[-1] > temp_sl else 0,
                            'sig_quality': q,
                            'bears': sum(recent_slice['close'] < recent_slice['open']),
                            'ev_rating': '🔎 潜在缺口追踪 (尚未翻转)',
                            'is_pending': True
                        })
                        print(f"\n  👀 发现正在孕育的潜在缺口: {code} {name}")
                        continue
            for sig_date, row in gt_rows.iterrows():
                entry = row.get('entry_struct_gap', np.nan)
                sl = row.get('sl_struct_gap', np.nan)
                tp = row.get('tp_struct_gap', np.nan)
                
                # 🛡️ 核心修复：检查此信号之后到今天为止，缺口是否已被填补 (价格跌穿 SL)
                if not np.isnan(sl):
                    # 获取该信号次日至今的最低价 (包含今天)
                    idx = df.index.get_loc(sig_date)
                    if idx < len(df) - 1:
                        post_signal_min_low = df['low'].iloc[idx+1:].min()
                        if post_signal_min_low <= sl:
                            # 缺口前提已被破坏，直接抛弃该遗留信号
                            continue
                
                risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
                rr = round(reward / risk, 1) if risk > 0 else 0
                
                
                # 🟢 [Structural Gap V8.5] 提取特征评分 (Weekly EV)
                q = row.get('sig_bar_quality', 0)
                
                # 计算回调连阴数 (Weekly)
                pb_consec_bear = 0
                if 'bars_since_breakout' in df.columns and not pd.isna(row.get('bars_since_breakout')):
                    pb_bars = int(row['bars_since_breakout'])
                    idx = df.index.get_loc(sig_date)
                    if pb_bars > 0 and idx >= pb_bars:
                        pb_df = df.iloc[idx - pb_bars : idx]
                        is_bear = pb_df['close'] < pb_df['open']
                        shifts = is_bear != is_bear.shift()
                        groups = shifts.cumsum()
                        bear_groups = is_bear.groupby(groups).sum()
                        pb_consec_bear = int(bear_groups.max()) if not bear_groups.empty else 0
                        
                # 动态评级 (Dynamic EV Rating - Weekly)
                if q > 0.8 and pb_consec_bear < 2:
                    ev_rating = '🌟 高预期'
                elif q <= 0.5 and pb_consec_bear >= 2:
                    ev_rating = '⚠️ 低预期'
                else:
                    ev_rating = '👍 常态'
                
                name = dp.get_stock_name(code)
                results_gap.append({
                    'code': code,
                    'name': name,
                    'date': row['date'] if 'date' in row else str(row.name),
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'rr': rr,
                    'sig_quality': q,
                    'bears': pb_consec_bear,
                    'ev_rating': ev_rating,
                    'is_pending': False
                })
                print(f"\n  ✨ 命中: {code} {name} | [{ev_rating}]")
                
        except Exception as e:
            continue
            
    return {'signals_gap': results_gap}


def main():
    parser = argparse.ArgumentParser(description='周线 Structural Gap 策略扫描器')
    parser.add_argument('--limit', type=int, default=0, help='限制扫描股票数量')
    parser.add_argument('--weeks', type=int, default=4, help='检查最近N周的信号')
    args = parser.parse_args()
    
    # ⚠️ 提示用户确认周线数据是否已更新
    try:
        from config.settings import DB_PATH
        import sqlite3
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='weekly_bars'")
            if c.fetchone()[0] == 0:
                print(f"\n❌ 周线数据库表 weekly_bars 不存在 ({DB_PATH})")
                print("👉 请先执行此命令同步数据: python tools/update_weekly_db.py\n")
                return
    except:
        pass
        
    try:
        # 获取股票列表 (从本地日线DB)
        all_codes = dp.get_stock_list()
        if not all_codes:
            print("❌ 获取股票列表失败")
            return
        
        if args.limit > 0:
            all_codes = all_codes[:args.limit]
        
        print(f"\n🚀 周线 Structural Gap 扫描: {len(all_codes)} 只股票, 检查最近 {args.weeks} 周")
        print("=" * 80)
        
        results = scan_weekly_gap(all_codes, recent_weeks=args.weeks)
        
        # === 控制台输出 ===
        print("\n" + "=" * 80)
        print(f"  周线 结构缺口 信号汇总")
        print("=" * 80)
        
        sig_gap = results['signals_gap']
        
        sg_best = [s for s in sig_gap if '🌟' in s.get('ev_rating', '') and not s.get('is_pending')]
        sg_good = [s for s in sig_gap if '👍' in s.get('ev_rating', '') and not s.get('is_pending')]
        sg_warn = [s for s in sig_gap if '⚠️' in s.get('ev_rating', '') and not s.get('is_pending')]
        sg_pend = [s for s in sig_gap if s.get('is_pending')]
        
        # 确保按评级重新排序
        sig_gap = sg_best + sg_good + sg_warn + sg_pend
        results['signals_gap'] = sig_gap
        
        print(f"\n📌 重点埋伏区 - 周线结构跨越+神级洗盘已确认 (共 {len(sig_gap)} 个):")
        print("-" * 60)
        
        def _print_sg_console(group, title):
            if group:
                print(f"\n[{title}] ({len(group)}只):")
                for s in group:
                    tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
                    rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
                    print(f"  {s['code']:>12s} {s['name']:<6s} | 买入:>={s['entry']:.2f} | 止损:{s['sl']:.2f} | 止盈:{tp_str} | R:R={rr_str}")
                    
        _print_sg_console(sg_best, "🌟 高预期")
        _print_sg_console(sg_good, "👍 常态")
        _print_sg_console(sg_warn, "⚠️ 低预期")
        _print_sg_console(sg_pend, "🔎 潜在追踪缺口 (尚未出现日历翻转信号)")
        
        print("\n" + "=" * 80)
        
        # === 导出报告与数据 ===
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 1. 导出 JSON 数据
        data_dir = os.path.join(project_root, 'data')
        os.makedirs(data_dir, exist_ok=True)
        json_path = os.path.join(data_dir, 'weekly_gap_watchlist.json')
        # Handle datetime serialization
        def default_serializer(obj):
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            return str(obj)
            
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False, default=default_serializer)
        print(f"✅ 生成监控名单: {json_path}")
        
        # 2. 导出 Markdown 报告
        lab_dir = os.path.join(project_root, 'strategy_lab')
        os.makedirs(lab_dir, exist_ok=True)
        md_path = os.path.join(lab_dir, 'weekly_struct_gap_plan.md')
        
        report_md = f"# 下周交易埋伏计划 (基于周线 Structural Gap V3.0)\n\n"
        report_md += f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report_md += f"**扫描范围**: 全市场 {len(all_codes)} 只个股\n\n"
        
        report_md += f"## 🎯 神级波段埋伏区 (待挂单)\n\n"
        report_md += f"> [!IMPORTANT]\n> 以下标的在最近 {args.weeks} 周内已走出 `突破并回调探底成功(H1 Confirmation)`。\n> **核心铁律：只要回调不破前高支撑线，下周一旦冲破本周高位，即放手做多，用极致止损博弈宏大波段！**\n\n"
        if not sig_gap:
            report_md += "本周无符合条件的突破标的。\n\n"
        else:
            report_md += "| 代码 | 名称 | 信号特征 | 下周买点 (Buy Stop) | 绝对止损 (Gap Floor) | 测距翻倍 (TP) | 盈亏比预估 |\n"
            report_md += "|:---:|:---|:---|:---|:---|:---|:---|\n"
            for s in sig_gap:
                tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
                rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
                date_str = s['date'].strftime('%Y-%m-%d') if hasattr(s['date'], 'strftime') else str(s['date'])
                report_md += f"| `{s['code']}` | **{s['name']}** | {s['ev_rating']} | **>={s['entry']:.2f}** | *{s['sl']:.2f}* | {tp_str} | {rr_str} |\n"
                
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(report_md)
        print(f"✅ 生成本周末复盘报告: {md_path}")
        
        # === Discord 图文推送 ===
        print("\n🚀 正在生成 Discord 图文全量推送...")
        
        # Discord 正文摘要先独立发送
        msg = "🔔 **【周线 结构性缺口(Structural Gap) 雷达扫描完成】**\n"
        msg += f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d')}\n"
        msg += f"池子: 全市场 {len(all_codes)} 只个股\n"
        msg += f"----------------------\n"
        msg += f"🎯 **命中结果**: 共 {len(sig_gap)} 只\n"
        
        if sg_best: msg += f"   🌟 **高预期**: {len(sg_best)} 只\n"
        if sg_good: msg += f"   👍 **常态**: {len(sg_good)} 只\n"
        if sg_warn: msg += f"   ⚠️ **低预期**: {len(sg_warn)} 只\n"
        if sg_pend: msg += f"   🔎 **潜在孕育期追踪**: {len(sg_pend)} 只\n"
        
        if not sig_gap:
            msg += "\n本周无任何符合条件的缺口买点出现。耐心等待战机！"
        else:
            msg += f"\n详细标的名单及交易策略，请在电脑端查看本地分析报告。\n正在按评级最高优先进行全量图形推送..."
        send_discord_message(msg)
        
        if not sig_gap:
            print("✅ Discord 空结果推送成功！")
        else:
            # 采用拆包逻辑全量推送：每组最大 10 个图形拼接
            BATCH_SIZE = 10
            total_batches = (len(sig_gap) + BATCH_SIZE - 1) // BATCH_SIZE
            
            for batch_idx in range(total_batches):
                batch_sigs = sig_gap[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
                chart_buffers = []
                print(f"  正在生成并推送第 {batch_idx+1}/{total_batches} 批图表 (本批包含 {len(batch_sigs)} 只标的)...")
                
                for s in batch_sigs:
                    try:
                        df = fetch_weekly_data(s['code'], weeks=300)
                        if df is not None:
                            df = add_indicators(df)
                            df = StructuralGapStrategy().calculate_signals(df)
                            buf = generate_chart_bytes(
                                code=s['code'], stock_name=s['name'], 
                                strategy_type="STRUCTURAL_GAP",
                                sl_price=s['sl'], tp1=s['tp'] if not np.isnan(s['tp']) else 0,
                                reason=f"周线大底确认 | {s['ev_rating']}", df_override=df,
                                ev_rating=s['ev_rating'], sig_quality=s['sig_quality'], bears=s['bears']
                            )
                            if buf: chart_buffers.append(buf)
                    except Exception as e:
                        logger.warning(f"绘图失败 {s['code']}: {e}")
                        
                if chart_buffers:
                    stitched = stitch_images(chart_buffers)
                    if stitched:
                        send_discord_image(stitched, filename=f"batch_{batch_idx+1}.png")
                        print(f"✅ 第 {batch_idx+1}/{total_batches} 批 ({len(chart_buffers)} 张长图) 推送成功！")
                    else:
                        print(f"⚠️ 第 {batch_idx+1}/{total_batches} 批 图片拼接失败。")
                else:
                    print(f"⚠️ 第 {batch_idx+1}/{total_batches} 批 没有成功生成任何图表。")
        
    except Exception as e:
        logger.error(f"严重异常: {e}")


if __name__ == '__main__':
    main()
