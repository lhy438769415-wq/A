import requests
import logging
import time
from config.settings import DEEPSEEK_API_KEY, API_URL, DEEPSEEK_MODEL
from typing import Optional

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 🟢 优化：添加重试机制和超时控制
_MAX_RETRIES = 3
_RETRY_DELAY = 2  # 秒

def query_deepseek(prompt: str) -> str:
    """调用DeepSeek API进行AI推理

    Args:
        prompt: 发送给AI的提示词

    Returns:
        AI的响应结果
    """
    # 检查是否为模拟模式
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.startswith("sk-dummy"):
        logger.info(f"[MOCK API] Received prompt (length: {len(prompt)})")
        # 根据 Prompt 内容返回模拟结果
        if "MISSION CONTEXT" in prompt and "OHLC" not in prompt:
            # 日线海选模拟
            return "PHASE 1: ANALYSIS\n形态确认: 支持建仓。信号确认: 合格。\nVERDICT: PASS"
        elif "OHLC" in prompt:
            # 分钟复核模拟
            return "VERDICT: TAKE TRADE\nOrder Type: STOP ENTRY\nEntry Price: 10.50\nInitial Stop: 10.00\nTarget Price: 11.50"
        return "VERDICT: NO TRADE"

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.0
    }

    # 🟢 优化：添加重试机制
    for attempt in range(_MAX_RETRIES):
        try:
            logger.debug(f"Sending request to DeepSeek API (attempt {attempt + 1}) with prompt length: {len(prompt)}")
            resp = requests.post(API_URL, headers=headers, json=data, timeout=120)
            if resp.status_code == 200:
                result = resp.json()['choices'][0]['message']['content']
                logger.debug("Successfully received response from DeepSeek API")
                return result
            elif resp.status_code >= 500:  # 服务器错误，应重试
                error_msg = f"API Server Error: {resp.status_code} - {resp.text}"
                logger.warning(f"Attempt {attempt + 1} failed with server error: {error_msg}")
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY * (2 ** attempt))  # 指数退避
                    continue
            else:
                error_msg = f"API Client Error: {resp.status_code} - {resp.text}"
                logger.error(error_msg)
                return error_msg
        except requests.exceptions.Timeout:
            error_msg = f"Request Timeout (attempt {attempt + 1})"
            logger.warning(error_msg)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue
        except requests.exceptions.RequestException as e:
            error_msg = f"Network Request Error (attempt {attempt + 1}): {e}"
            logger.warning(error_msg)
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue
        except Exception as e:
            error_msg = f"Unexpected Error (attempt {attempt + 1}): {e}"
            logger.error(error_msg)
            return error_msg

    # 所有重试都失败了
    return f"API request failed after {_MAX_RETRIES} attempts"