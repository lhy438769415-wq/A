# -*- coding: utf-8 -*-
"""
周线 3K 策略独立扫描器 (scanner_weekly_3k.py)

独立于日线系统，直接调用 Baostock frequency='w' 获取周线数据。
不修改 data_provider / database / scanner 等现有核心模块。

# 用法:
#   1. 在周末手动运行网络同步：python tools/update_weekly_data.py
#   2. 运行纯离线本地扫描：python tools/scanner_weekly_3k.py [--limit N] [--weeks D]
#
# 会自动在 strategy_lab 目录下生成每周埋伏计划报告 (weekly_ambush_plan.md)
# 并在 data 目录下生成监控文件 (weekly_watchlist.json)
"""
import sys, os, io, argparse, logging, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import baostock as bs

from core.calculator import add_indicators
from core.strategies.three_k_strategy import ThreeKStrategy
import core.data_provider as dp
from tools.notifier import generate_chart_bytes, stitch_images, send_discord_image, send_discord_message, send_discord_images

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# =====================================================
# 从本地周线数据库极速提取数据
# =====================================================
def fetch_weekly_data(full_code: str, weeks: int = 200) -> pd.DataFrame:
    """
    代理函数：将原有的 Baostock 联调逻辑更换为读取本地 db。
    """
    return dp.get_stock_data_weekly(full_code, limit=weeks)


# =====================================================
# 主扫描逻辑
# =====================================================
def scan_weekly_3k(all_codes: list, recent_weeks: int = 4) -> dict:
    """
    扫描全市场周线 3K 信号
    
    Returns:
        {'signals_3k': [...], 'signals_gap_test': [...]}
    """
    strategy = ThreeKStrategy()
    results_3k = []
    results_gt = []
    
    for i, code in enumerate(all_codes):
        if (i + 1) % 200 == 0:
            print(f"  进度: {i+1}/{len(all_codes)}...")
        
        try:
            df = fetch_weekly_data(code, weeks=200)
            if df is None or len(df) < 60:
                continue
            
            df = add_indicators(df)
            df = strategy.calculate_signals(df)
            
            # 检查最近 N 周是否有信号
            recent = df.tail(recent_weeks)
            
            # 3K 信号
            sig_rows = recent[recent['signal_3k'] == True]
            for _, row in sig_rows.iterrows():
                results_3k.append({
                    'code': code,
                    'name': dp.get_stock_name(code),
                    'date': row['trade_date'] if 'trade_date' in row else (row['date'] if 'date' in row else str(row.name)),
                    'close': row['close'],
                    'sl': row.get('sl_3k', np.nan),
                })
            
            # 缺口测试确认
            gt_rows = recent[recent.get('signal_3k_gap_test', pd.Series(dtype=bool)) == True]
            for _, row in gt_rows.iterrows():
                entry = row.get('entry_3k_gap_test', np.nan)
                sl = row.get('sl_3k_gap_test', np.nan)
                tp = row.get('tp_3k_gap_test', np.nan)
                risk = entry - sl if not np.isnan(entry) and not np.isnan(sl) else 0
                reward = tp - entry if not np.isnan(tp) and not np.isnan(entry) else 0
                rr = round(reward / risk, 1) if risk > 0 else 0
                
                results_gt.append({
                    'code': code,
                    'name': dp.get_stock_name(code),
                    'date': row['trade_date'] if 'trade_date' in row else (row['date'] if 'date' in row else str(row.name)),
                    'entry': entry,
                    'sl': sl,
                    'tp': tp,
                    'rr': rr,
                })
                
        except Exception as e:
            continue
    
    return {'signals_3k': results_3k, 'signals_gap_test': results_gt}


def main():
    parser = argparse.ArgumentParser(description='周线 3K 策略扫描器')
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
        
        print(f"\n🚀 周线 3K 扫描: {len(all_codes)} 只股票, 检查最近 {args.weeks} 周")
        print("=" * 80)
        
        results = scan_weekly_3k(all_codes, recent_weeks=args.weeks)
        
        # === 控制台输出 ===
        print("\n" + "=" * 80)
        print(f"  周线 3K 信号汇总")
        print("=" * 80)
        
        sig_3k = results['signals_3k']
        sig_gt = results['signals_gap_test']
        
        print(f"\n📌 重点观察区 - 周线 3K 形态刚确认 (共 {len(sig_3k)} 个):")
        print("-" * 60)
        for s in sig_3k:
            print(f"{s['code']:>12s} {s['name']:<6s} 周线日期:{s['date']}  最新收盘:{s['close']:.2f}  破位参考(SL):{s['sl']:.2f}")
        
        print(f"\n📌 下周埋伏区 - 周线缺口测试已确认，待触发 Buy Stop (共 {len(sig_gt)} 个):")
        print("-" * 60)
        for s in sig_gt:
            tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
            rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
            print(f"{s['code']:>12s} {s['name']:<6s} 周线日期:{s['date']}  下周买入(Buy Stop):>={s['entry']:.2f}  防守(SL):{s['sl']:.2f}  目标:{tp_str}  R:R={rr_str}")
        
        print("\n" + "=" * 80)
        
        # === 导出报告与数据 ===
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 1. 导出 JSON 数据 (用于盘中监控)
        data_dir = os.path.join(project_root, 'data')
        os.makedirs(data_dir, exist_ok=True)
        json_path = os.path.join(data_dir, 'weekly_watchlist.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
        print(f"✅ 生成监控名单: {json_path}")
        
        # 2. 导出 Markdown 报告 (供人工审阅)
        lab_dir = os.path.join(project_root, 'strategy_lab')
        os.makedirs(lab_dir, exist_ok=True)
        md_path = os.path.join(lab_dir, 'weekly_ambush_plan.md')
        
        report_md = f"# 下周交易埋伏计划 (基于周线 3K V2.3)\n\n"
        report_md += f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report_md += f"**扫描范围**: 全市场 {len(all_codes)} 只个股\n\n"
        
        report_md += f"## 🎯 下周重点埋伏区 (待挂单)\n\n"
        report_md += f"> [!IMPORTANT]\n> 以下标的在最近 {args.weeks} 周内已走出 `缺口测试确认(Gap Test) 阳线`。**一旦下周冲破测缺K线高点，即可按规则挂单追涨或平开现价买入。**\n\n"
        if not sig_gt:
            report_md += "本周无符合【缺口测试确认】的埋伏标的。\n\n"
        else:
            report_md += "| 代码 | 名称 | 周线信号日期 | 下周买点 (Buy Stop) | 止损 (Gap Floor) | 目标价 (TP) | 盈亏比预估 |\n"
            report_md += "|:---:|:---|:---|:---|:---|:---|:---|\n"
            for s in sig_gt:
                tp_str = f"{s['tp']:.2f}" if not np.isnan(s['tp']) else "N/A"
                rr_str = f"1:{s['rr']:.1f}" if s['rr'] > 0 else "N/A"
                report_md += f"| `{s['code']}` | **{s['name']}** | {s['date']} | **>={s['entry']:.2f}** | *{s['sl']:.2f}* | {tp_str} | {rr_str} |\n"
        
        report_md += f"\n## 🔭 下周观察池 (刚出 3K 雏形)\n\n"
        report_md += f"> [!NOTE]\n> 以下标的已出现强劲的周线 3K 突破形态。尚未进行缺口测试，不建议盲目追高。**下周可重点观察它们的周线回撤（是否能守住下方参考位并在下周或下下周收出企稳阳线）。**\n\n"
        if not sig_3k:
            report_md += "本周无新出的【3K 突破】观察标的。\n\n"
        else:
            report_md += "| 代码 | 名称 | 周线 3K 日期 | 最新收盘 | 回撤防守底线 (不宜跌破) |\n"
            report_md += "|:---:|:---|:---|:---|:---|\n"
            for s in sig_3k:
                report_md += f"| `{s['code']}` | **{s['name']}** | {s['date']} | {s['close']:.2f} | *{s['sl']:.2f}* |\n"
                
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(report_md)
        print(f"✅ 生成本周末复盘报告: {md_path}")
        
        # === 微信图文推送 ===
        print("\n🚀 正在生成微信图文推送...")
        chart_buffers = []
        # 优先推送 Gap Test (最多 3 个)
        for s in sig_gt[:3]:
            try:
                df = fetch_weekly_data(s['code'], weeks=200)
                if df is not None:
                    df = add_indicators(df)
                    df = ThreeKStrategy().calculate_signals(df)
                    buf = generate_chart_bytes(
                        code=s['code'], stock_name=s['name'], strategy_type="3K WEEKLY (Gap Test)",
                        sl_price=s['sl'], tp1=s['tp'] if not np.isnan(s['tp']) else 0,
                        reason="周线缺口测试确认，待Buy Stop", df_override=df
                    )
                    if buf: chart_buffers.append(buf)
            except Exception as e:
                logger.warning(f"绘图失败 {s['code']}: {e}")
                
        # 补充推送新 3K (补齐到最多 5 个)
        remain = 5 - len(chart_buffers)
        if remain > 0:
            for s in sig_3k[:remain]:
                try:
                    df = fetch_weekly_data(s['code'], weeks=200)
                    if df is not None:
                        df = add_indicators(df)
                        df = ThreeKStrategy().calculate_signals(df)
                        buf = generate_chart_bytes(
                            code=s['code'], stock_name=s['name'], strategy_type="3K WEEKLY (New Forming)",
                            sl_price=s['sl'], reason="周线刚出3K雏形，重点观察回抽", df_override=df
                        )
                        if buf: chart_buffers.append(buf)
                except Exception as e:
                    logger.warning(f"绘图失败 {s['code']}: {e}")
                    
        # 推送到 Discord
        if chart_buffers:
            msg = "🔔 【周线 3K 雷达扫描完成】\n"
            msg += f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d')}\n"
            msg += f"池子: 全市场 {len(all_codes)} 只个股\n"
            msg += f"----------------------\n"
            msg += f"🎯 缺口确认 (待设Buy Stop): {len(sig_gt)} 只\n"
            msg += f"🔭 新出3K雏形 (重点观察): {len(sig_3k)} 只\n"
            
            filenames = [f"weekly_3k_{i}.png" for i in range(len(chart_buffers))]
            send_discord_images(chart_buffers, filenames, content=msg)
            print("✅ Discord 图文推送成功！")
        else:
            if not sig_gt and not sig_3k:
                send_discord_message("🔔 **【周线 3K 雷达】**\n本周无任何符合条件的标的。好好休息！")
                print("✅ Discord 空结果推送成功！")
            else:
                print("⚠️ 没有成功生成任何图表。")
        
    except Exception as e:
        logger.error(f"严重异常: {e}")


if __name__ == '__main__':
    main()
