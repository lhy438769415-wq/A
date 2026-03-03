import sys
import os
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# Setup path
sys.path.append(os.getcwd())

from core.strategies.mtr_strategy import MTRStrategy
import core.data_provider as data_provider
from core.calculator import add_indicators

def scan_candidate(code):
    try:
        # Get enough data for Swing calculation
        df = data_provider.get_stock_data(code, limit=200)
        if df is None or len(df) < 100:
            return None
        
        # Add basic indicators required by strategy
        df = add_indicators(df)
        strat = MTRStrategy()
        df = strat.calculate_signals(df)
        
        last_row = df.iloc[-1]
        stage = last_row.get('mtr_stage', 'NONE')
        
        # [V26 Integrity Check] Data Freshness
        # 很多标的数据停留在几年前，导致产生"僵尸信号"
        last_date_str = str(last_row['date'])
        from datetime import datetime
        try:
            last_dt = datetime.strptime(last_date_str, "%Y-%m-%d")
            days_diff = (datetime.now() - last_dt).days
            if days_diff > 10:
                # print(f"Skipping stale: {code} ({last_date_str})")
                return None 
        except Exception:
            pass # Date parse error, assume OK or Skip? Skip safer.
            
        if stage != "NONE":
            return {
                'code': code,
                'name': code, # Placeholder, maybe fetch name if available
                'date': str(last_row['date']),
                'stage': stage,
                'close': last_row['close'],
                'score': last_row.get('mtr_score', 0)
            }
            
    except Exception as e:
        # print(f"Error scanning {code}: {e}")
        pass
    return None

def run_radar_scan(all_codes):
    """
    可以直接被 Hunter 调用的雷达扫描函数
    """
    print(f"📡 Radar Scanning: MTR Potential Analysis ({len(all_codes)} stocks)...")
    
    results = {
        "SETUP_READY": [],   # 结构就绪（原文四要素中①②③已确认）
        "SETUP_FORMING": [],  # 结构形成中
    }
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(scan_candidate, code): code for code in all_codes}
        completed = 0
        for future in as_completed(futures):
            res = future.result()
            if res:
                stage = res['stage']
                if stage in results:
                    results[stage].append(res)
            
            completed += 1
            if completed % 1000 == 0:
                print(f"Progress: {completed}/{len(all_codes)}...")

    # Sorting
    results["SETUP_READY"].sort(key=lambda x: x.get('score', 0), reverse=True)
    results["SETUP_FORMING"].sort(key=lambda x: x.get('score', 0), reverse=True)
    
    try:
        from tools.notifier import fetch_stock_name
    except ImportError:
        def fetch_stock_name(c): return c

    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")

    lines = [f"🦅 **MTR 潜力猎手雷达** ({today_str})", "-" * 30]
    
    # 只有在有结果时才返回报告，否则返回 None 防止发送空微信头
    if not any(results.values()):
        return None

    lines = [f"🦅 **MTR 潜力猎手雷达** ({today_str})", "-" * 30]
    
    # 1. SETUP_READY
    if results["SETUP_READY"]:
        lines.append(f"\n🔴 **【结构就绪】Setup Ready** ({len(results['SETUP_READY'])}只)")
        for r in results["SETUP_READY"][:5]:
            name = fetch_stock_name(r['code'])
            lines.append(f"> **{name}** ({r['code']}) | 确认启动 | 分数:{r.get('score',0):.1f}")
    
    # 2. SETUP_FORMING
    if results["SETUP_FORMING"]:
        lines.append(f"\n🟠 **【结构形成中】Setup Forming** ({len(results['SETUP_FORMING'])}只)")
        for r in results["SETUP_FORMING"][:10]:
            name = fetch_stock_name(r['code'])
            lines.append(f"• **{name}** ({r['code']}) | 正在测试低点")

    report_text = "\n".join(lines)
    return report_text

def main():
    all_codes = data_provider.get_stock_list()
    if not all_codes:
        print("No stock list found.")
        return
    
    # Optional: Limiting for debug if list is huge
    all_codes = all_codes[:200] 
    
    run_radar_scan(all_codes)

if __name__ == "__main__":
    main()
