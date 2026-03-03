import os
import sys
import json
import logging
from openai import OpenAI
from dotenv import load_dotenv

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
load_dotenv(os.path.join(project_root, '.env'))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def consult_al_brooks():
    # Attempt to load DeepSeek API key from environment
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    
    if not api_key:
        logger.error("❌ DEEPSEEK_API_KEY not found in environment variables. Please set it in your .env file.")
        return

    client = OpenAI(api_key=api_key, base_url=base_url)

    system_prompt = """
# Role: Al Brooks (Creator of Brooks Price Action)

You are Al Brooks, the legendary price action trader and author. You focus deeply on bar-by-bar analysis, institutional order flow, trapped traders, and context. You do not use indicators; you read the chart.

A quantitative architect is presenting their statistical findings regarding the "Measuring Gap" (Structural Gap breakout and 1x Measure Move target). They have tested 1053 trades on the A-share market.

Your goal is to:
1. Review their findings from the perspective of your Price Action theory.
2. Confirm or challenge their interpretations.
3. Provide deeper nuance, especially regarding the difference between a tight bear channel pullback (toxic overlap) and a wedge bull flag (constructive overlap).

Respond in Chinese, keeping your tone authoritative, analytical, and focused strictly on price action mechanics (trapped bears, FOMO bulls, probability, limit orders vs stop orders).
"""

    user_prompt = """
尊敬的 Al Brooks 先生，我们在A股市场基于您的理论，针对百日周期级别的“结构性突破并在回撤中防守住缺口（Gap Floor）”形态，进行了 1053 笔实盘级别样本的测算，目标是达到非对称的一倍 Measure Move。

以下是我们的统计发现与推论，请您以 PA 原理进行点评：

1. 缺口宽度和回撤深度的预测性极低。唯一的绝对核心是触底当天的 Signal Bar 质量。如果该阳线几乎没有上影线（质量 > 0.95），胜率就会从 50% 飙升至 60%。我们认为这代表了机构极其“迫切”的抢筹，日内没有给空方任何喘息机会，次日一旦突破极易触发空头的止损盘和场外观望多头的 FOMO 追价。

2. 在整个回踩通道（Pullback Channel）中，如果没有出现任何“连续 2 根以上的阴线（0 连阴）”，胜率（56.6%）远高于出现连阴的形态（46%）。我们推论：没有连阴说明空头无法建立有效的抛压，每次试图做空都会撞上多头的限价买单（Limit Orders），趋势并没有发生真正的转换。

3. 关于 K 线重叠度（Overlap）：
   - “低重叠度的瀑布式闪跌”胜率达到了 55.4%。我们认为这是经典的“流动性真空测试”和空头陷阱，跌得太快导致没有散户跟进套牢，一旦极品 Signal Bar 出现，拉升时上方没有筹码阻力。
   - “高重叠度（阴阳交错的阴跌）”胜率极低（46.4%）。我们认为这说明每一根 K 线都有多头试图抢反弹而被套，上方积累了大量的套牢盘，成了拉伸的绞肉机。

4. 关于三推牛旗（Wedge Bull Flag）的困惑：
   按照您的理论，向下的三推牛旗是极佳的买点。但这似乎与上述“高重叠度有害”的统计相违背（因为三推必然伴随两次反弹，会导致较高的波段重叠度）。我们内部的解释是：三推牛旗的宏观重叠是“良性重叠”，因为它的两次反弹实际上在清洗早期的套牢盘，当推 3 竭尽时上方反而干净了；而恶性重叠是单根 K 线级别的紧凑阴跌（Tight Channel）。

请问这种将量化数据与您的 PA 本质（筹码、心理、陷阱）相映射的推论是否准确？有何更深层的指教？
"""

    logger.info("🤖 正在呼叫 DeepSeek (Al Brooks 角色) 进行学术研讨，请稍候...")
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        reply = response.choices[0].message.content
        logger.info("\n" + "="*60)
        logger.info("🎓 Al Brooks (Powered by DeepSeek) 的回复:")
        logger.info("="*60 + "\n")
        logger.info(reply)
        logger.info("\n" + "="*60)
        
        # Save to markdown artifact
        report_path = os.path.join(project_root, "docs", "deepseek_al_brooks_discussion.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("# 与 Al Brooks (DeepSeek) 的测算研讨记录\n\n")
            f.write("## 我们的汇报与困惑\n")
            f.write(user_prompt)
            f.write("\n\n## Al Brooks 的回复\n")
            f.write(reply)
            
        logger.info(f"研讨记录已保存至: {report_path}")
        
    except Exception as e:
        logger.error(f"❌ 调用 API 失败: {e}")
        logger.error("提示: 确保在项目的 .env 文件中配置了有效的 DEEPSEEK_API_KEY。")

if __name__ == "__main__":
    consult_al_brooks()
