import re
import queue
import logging
from typing import Dict, List, Tuple, Optional

# 假定其他导入已经存在，这里只重写 prepare_daily_chart 函数

def prepare_daily_chart(stage1_item: Dict[str, any], passed: bool = True, reason: str = "") -> Tuple[Optional[Dict[str, any]], Optional[bytes], str]:
    """[Phase 4] 准备日线图表 (支持通过和拒绝两种状态)
    
    Args:
        stage1_item: 包含初步筛选结果的字典
        passed: AI 是否通过
        reason: AI 拒绝或通过的具体理由
        
    Returns:
        tuple: (处理后的项目, 图片缓冲区, 结果描述)
    """
    try:
        from tools.notifier import generate_chart_bytes, fetch_stock_name
    except ImportError:
        return stage1_item, None, "导入Notifier失败"

    code = stage1_item['code']
    name = fetch_stock_name(code)
    
    # 构造结果
    stage1_item['name_cn'] = name
    
    # 🟢 优先级：1. 传入的 reason, 2. 解析出的理由, 3. 原始文本
    raw_reason = reason
    if not raw_reason:
        raw_reason = stage1_item.get('ai_parsed', {}).get('reason', "")
    if not raw_reason:
        raw_reason = stage1_item.get('ai_daily_view', "N/A")

    try:
        # 1. 强力清洗所有 XML 标签
        clean_view = re.sub(r"<[^>]+>", "", raw_reason).strip()
        # 2. 去除常见的开头确认词
        clean_view = re.sub(r"^(YES|PASS|FAIL|REJECT|VERDICT|通过|拒绝|日线拒绝)[:\.\s]*", "", clean_view, flags=re.IGNORECASE).strip()
        
        # 3. 如果包含换行，尝试保留第一段主要内容
        lines = [l.strip() for l in clean_view.split('\n') if l.strip()]
        if lines:
            clean_view = lines[0]
            # 如果第一行只是个标题（比如 "Analysis:"），那么拿第二行
            if (len(clean_view) < 10 or clean_reason_is_title(clean_view)) and len(lines) > 1:
                clean_view = lines[1]
        
        # 4. 限制长度
        if len(clean_view) > 100:
            clean_view = clean_view[:97] + "..."
        
        # 🟢 使用标准符号 √ 和 ×，避免字体乱码
        symbol = "√" if passed else "×"
        display_reason = f"[{symbol}] {clean_view}"
        
        # 获取 TP 参数
        info = stage1_item.get('info', {})
        tp1 = info.get('tp1', 0)
        tp2 = info.get('tp2', 0)
        
        chart_buf = generate_chart_bytes(
            code, name, stage1_item['type'], info.get('sl', 0), 
            tp1=tp1, tp2=tp2, 
            reason=display_reason
        )
        return stage1_item, chart_buf, "图表生成完成"
    except Exception as e:
        logging.error(f"Chart generation failed for {code}: {e}")
        return stage1_item, None, f"绘图失败({e})"

def clean_reason_is_title(text):
    """判断是否是无意义的标题行"""
    titles = ["分析:", "理由:", "观点:", "PA观点:", "原因:", "Analysis:", "Reason:"]
    return any(text.startswith(t) for t in titles)
