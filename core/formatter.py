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
    [Guardian Mode Prompt: Persona + CSV + Template]
    用户指定: "我是一个基于 Al Brooks 价格行为学的交易员... 持仓成本XX"
    """
    code = holding_data['code']
    cost = holding_data.get('cost', 0)
    df = holding_data['df']
    
    ctx = _get_common_context(df, window_size=60)
    sop_content = load_sop_rules()
    
    prompt = f"""
{sop_content}
---
# 👤 ROLE: AI BROOKS TRADER
我是一个基于 AI Brooks 价格行为学的交易员。请根据以下 OHLC 数据，像 Al Brooks 一样分析这只股票 ({code}) 的市场情绪。
我当前的持仓成本是 {cost:.2f} 元。
重点告诉我：
1. 当前走势是否出现高潮 (Climax) 也就是潜在反转信号？
2. 我应该如何移动止损或减仓？ (Guardian Focus)

# 🔬 MARKET DATA (Last 60 Days)
{ctx['csv_str']}

# 📝 PLAN TEMPLATE (持仓计划模版)
并按照我的模版输出持仓计划，以下是我的持仓计划模版：

<STATUS>HOLD / TRIM / EXIT</STATUS>
<WARNING>Climax / Resistance / None</WARNING>
<NEW_STOP>Price</NEW_STOP>
<POSITION_ADJUST>Note on scaling</POSITION_ADJUST>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>一句话核心逻辑和买卖点定论</DISCORD>
<REASON>Reasoning</REASON>
"""
    return prompt

# Alias for compatibility
format_for_ai = format_hunter_prompt
