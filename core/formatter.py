import os
import re
import pandas as pd
import numpy as np
import logging
from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

def load_sop_rules():
    """读取 Al Brooks 16步 SOP 规则文件"""
    sop_path = os.path.join(BASE_DIR, 'config', 'sop_rules.md')
    try:
        with open(sop_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return "警告：SOP 规则文件缺失。请严格基于 Price Action 进行分析。"

def parse_response(response_text):
    """
    [V8.5 通用解析器 V2]
    职责：从 AI 的回复中提取 XML 字段，支持思维链中间结果。
    """
    result = {
        "verdict": "ERROR",
        "reason": "解析失败",
        "raw": response_text
    }
    
    try:
        # 通用决策
        result["verdict"] = _extract_tag(response_text, "VERDICT") or "ERROR"
        result["reason"] = _extract_tag(response_text, "REASON") or _extract_tag(response_text, "PA_TAGS") or "无详细说明"
        result["analysis"] = _extract_tag(response_text, "ANALYSIS")
        result["pa_tags"] = _extract_tag(response_text, "PA_TAGS")
        result["tags"] = _extract_tag(response_text, "PA_TAGS")
        
        # 兼容原有遗留模板 WECHAT 及最新模板 DISCORD
        msg = _extract_tag(response_text, "DISCORD")
        if not msg:
            msg = _extract_tag(response_text, "WECHAT")
        result["discord_msg"] = msg

        # 思维链中间态 (Optional for debugging)
        if "<MARKET_STATE>" in response_text:
            result["market_state"] = _extract_tag(response_text, "MARKET_STATE")
        if "<ALWAYS_IN>" in response_text:
            result["always_in"] = _extract_tag(response_text, "ALWAYS_IN")
        if "<TRAP_CHECK>" in response_text:
            result["trap_check"] = _extract_tag(response_text, "TRAP_CHECK")
            
        # Hunter 专用
        if "<PROBABILITY>" in response_text:
            result["probability"] = _extract_tag(response_text, "PROBABILITY")
        if "<RR>" in response_text:
            result["rr"] = _extract_tag(response_text, "RR")
            
        # Guardian 专用
        if "<STATUS>" in response_text:
            result["status"] = _extract_tag(response_text, "STATUS")
        if "<WARNING>" in response_text:
            result["warning"] = _extract_tag(response_text, "WARNING")
        if "<NEW_STOP>" in response_text:
            result["new_stop"] = _extract_tag(response_text, "NEW_STOP")
        if "<POSITION_ADJUST>" in response_text:
             result["position_adjust"] = _extract_tag(response_text, "POSITION_ADJUST")
             
    except Exception as e:
        logger.error(f"⚠️ 解析器异常: {e}")
        
    return result

def _extract_tag(text, tag):
    """Helper to extract content between <TAG> and </TAG>"""
    try:
        pattern = f"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
             return match.group(1).strip()
        return None
    except:
        return None

def get_common_context(df, window_size=60):
    """提取 CSV 数据 (Reverted to 60 days, Raw Data Only)"""
    df = df.sort_values(by='date', ascending=True)
    
    # 微观数据表
    recent_df = df.tail(window_size).copy()
    # 仅保留基础列 + 核心 EMA 距离 (去除 V3 复杂指标)
    # 🟢 V1.9 Physics Engine: Inject Momentum Facts & Indicators
    cols = [
        'date', 'open', 'high', 'low', 'close', 'volume',
        'ema20', 'dist_ema20_atr', 'gap_down_count_20', 'relative_vol', 'linreg_res'
    ]
    final_cols = [c for c in cols if c in recent_df.columns]
    csv_str = recent_df[final_cols].to_string(index=False, float_format="%.2f")
    
    return {
        'csv_str': csv_str,
        'last_price': df.iloc[-1]['close']
    }

# Alias for compatibility with old internal calls
_get_common_context = get_common_context

def format_hunter_prompt(candidate_data):
    """
    [Legacy Wrapper]
    Redirects to the Default Strategy (Hunter V1).
    Kept for backward compatibility with tests/scripts.
    """
    from core.strategy_registry import StrategyRegistry
    strategy = StrategyRegistry.get_strategy("HUNTER_V1")
    
    # Adapt data structure
    ctx = get_common_context(candidate_data['df'], window_size=60)
    sop = load_sop_rules()
    
    context_data = {
        'code': candidate_data['code'],
        'df': candidate_data['df'],
        'ctx': ctx,
        'sop_content': sop,
        'candidate_data': candidate_data
    }
    return strategy.format_prompt(context_data)

def format_guardian_prompt(holding_data):
    """
    [Guardian Mode Prompt V2: Trade Thesis + PA Daily Diagnosis]
    核心理念: 不是让 AI 重新分析周线, 而是把交易论据作为事实传给 AI,
    让 AI 基于日线 PA 判断论据是否仍然成立。
    """
    code = holding_data['code']
    cost = holding_data.get('cost', 0)
    df = holding_data['df']
    
    # 交易论据上下文 (从 signal_archive 查询)
    thesis = holding_data.get('trade_thesis', {})
    entry_price = thesis.get('entry_price', cost)
    sl_price = thesis.get('sl_price', 0)
    tp_price = thesis.get('tp_price', 0)
    strategy = thesis.get('strategy', '未知')
    ev_rating = thesis.get('ev_rating', 'N/A')
    gap_status = thesis.get('gap_status', '未知')
    signal_date = thesis.get('signal_date', '未知')
    
    ctx = _get_common_context(df, window_size=60)
    sop_content = load_sop_rules()
    
    # 构建交易论据描述
    if 'STRUCTURAL_GAP' in strategy.upper():
        thesis_desc = (
            f"该持仓基于「结构性突破缺口」策略入场。"
            f"在 {signal_date} 发现价格突破了长期盘整的高点形成 Breakout Gap, "
            f"并在回调中确认缺口底部(Gap Floor)未被击穿。\n"
            f"  - 战略止损 (Gap Floor): {sl_price:.2f}\n"
            f"  - 对称测量目标 (MM): {tp_price:.2f}\n"
            f"  - 缺口状态: {gap_status}\n"
            f"  - 系统评级: {ev_rating}"
        )
    elif '3K' in strategy.upper():
        thesis_desc = (
            f"该持仓基于「3K 动能突破」策略入场。"
            f"在 {signal_date} 出现连续三根强势 K 线突破, 形成缺口测试确认。\n"
            f"  - 战略止损: {sl_price:.2f}\n"
            f"  - 目标: {tp_price:.2f}"
        )
    elif 'MTR' in strategy.upper():
        thesis_desc = (
            f"该持仓基于「MTR 主要趋势反转」策略入场。"
            f"在 {signal_date} 确认 Higher Low 反转信号。\n"
            f"  - 战略止损: {sl_price:.2f}\n"
            f"  - 目标: {tp_price:.2f}"
        )
    else:
        thesis_desc = (
            f"持仓成本: {cost:.2f}\n"
            f"  - 止损参考: {sl_price:.2f}\n"
            f"  - 目标参考: {tp_price:.2f}"
        )
    
    prompt = f"""
{sop_content}
---
# 👤 ROLE: AI BROOKS GUARDIAN (持仓管家)

你正在管理一笔基于 Al Brooks Price Action 的摆动交易 (Swing Trade)。

## 📋 交易论据 (Trade Thesis)
{thesis_desc}

## 💰 当前持仓
- 股票代码: {code}
- 持仓成本: {cost:.2f}
- 当前价格: {ctx['last_price']:.2f}

## 🔬 日线 K 线数据 (最近 60 个交易日)
{ctx['csv_str']}

## ❓ 请基于 PA 回答以下问题

1. **交易论据是否仍然成立？**
   - 日线 PA 是否出现了任何破坏交易前提 (Premise) 的信号？
   - 如果战略止损位 {sl_price:.2f} 尚未被跌破, 但日线已出现 Climax/反转, 请评估风险。

2. **战术止损如何调整？**
   - 在战略止损 {sl_price:.2f} 之上, 日线是否有更紧凑的 PA 止损位？
   - 是否已有条件将止损移至盈亏平衡 (Breakeven)？
   (参考 SOP Step 15: Breakeven Trigger = 利润达到目标的约一半)

3. **是否需要减仓或离场？**
   - 是否出现买入高潮 (Buy Climax) 或穷尽性缺口 (Exhaustion Gap)？
   - 当前价格距目标 {tp_price:.2f} 的位置, 是否已经达到或接近？

## 📝 输出模版 (严格按此格式)

<STATUS>HOLD / TRIM / EXIT</STATUS>
<WARNING>Climax / Exhaustion / Premise Weakening / None</WARNING>
<NEW_STOP>建议的新止损价格 (必须 >= 战略止损 {sl_price:.2f})</NEW_STOP>
<POSITION_ADJUST>仓位调整建议</POSITION_ADJUST>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>一句话核心逻辑: 当前 PA 状态 + 持仓建议</DISCORD>
<REASON>详细推理过程, 引用具体的 K 线日期和价格行为特征</REASON>
"""
    return prompt

# Alias for compatibility
format_for_ai = format_hunter_prompt
