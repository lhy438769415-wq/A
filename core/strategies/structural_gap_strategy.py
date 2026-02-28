# core/strategies/structural_gap_strategy.py
"""
[Strategy] Structural Gap Strategy (V3.0 Macro Base Breakout)
Original logic guided by Al Brooks PA principles & Refined through Brooks-AI Lab.

This strategy focuses on macro structural breakaways rather than micro-formations:
Step 1: Breakout Gap Creation (Higher High, Higher Low, and Low > 60-bar High)
Step 2: Identify Swing Extremes (Prior Swing Low & Gap Floor)
Step 3: Gap Remains Open (Pullbacks never penetrate Gap Floor)
Step 4: Reversal Confirmation (Lower Low stopped, High > Prev High)
Step 5: Measured Move (Conservative Mirroring from True Gap Midpoint to Prior Swing Low)
"""

import pandas as pd
import numpy as np
import logging
import re
from typing import Dict
from .base import BaseStrategy
from core.formatter import get_common_context
from config import settings

logger = logging.getLogger(__name__)

class StructuralGapStrategy(BaseStrategy):
    """
    Structural Breakout Gap Strategy
    """

    @property
    def name(self) -> str:
        return "STRATEGY_STRUCTURAL_GAP"

    @property
    def description(self) -> str:
        return "Macro Structural Breakaway Gap & Pullback Reversal (V3.0)"

    @property
    def signal_column(self) -> str:
        return 'signal_struct_gap_confirm'

    def __init__(self):
        # 1. Lookback Window - 突破判定所需的周期, 默认 60 根 (A股日线约3个月, 周线约1.2年)
        self.LOOKBACK_WINDOW = getattr(settings, 'STRUCT_GAP_LOOKBACK', 60)
        # 2. 确认回调的最大跟踪周期 
        self.MAX_PULLBACK_WINDOW = getattr(settings, 'STRUCT_GAP_MAX_WINDOW', 40)
        # 3. 必须回调的最小周期（避免单根K线上窜下跳，给予真正的回调空间）
        self.MIN_PULLBACK_WINDOW = 2

    def calculate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        if len(df) < self.LOOKBACK_WINDOW + 5:
            df['signal_struct_gap_confirm'] = False
            return df

        # Ensure basic indicators are present, though this strategy relies mostly on raw price action
        required = ['atr', 'is_bullish']
        if not all(col in df.columns for col in required):
            logger.warning(f"Struct Gap Strategy missing columns: {[c for c in required if c not in df.columns]}")
            df['signal_struct_gap_confirm'] = False
            return df

        # ==============================================================================
        # 第一步：识别强劲的结构性突破（Breakout Gap Creation）
        # ==============================================================================
        # 1. 微观层面：当前 K 线较前一根，高点和低点均抬升 (Higher High, Higher Low)
        is_hh_hl = (df['high'] > df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
        
        # 2. 宏观层面：确立防守底线 Gap Floor (过去 N 根 K 线的最绝对高点)
        # shift(2) 是因为 K 线上是从突破线前一天(K-1) 为止的过去 N 根
        _gap_floor_raw = df['high'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).max().shift(2)
        
        # 突破认定：极度罕见的大阳线或跳空，连本根的最低低点都高于过去 N 周期的最高点
        # 引入 1e-3 的浮点数容差，防止复权数据产生细微精度问题
        df['is_breakout'] = is_hh_hl & (df['low'] > _gap_floor_raw - 1e-3)

        # ==============================================================================
        # 第二步：提取历史阻力与起涨波段（Swing Extremes）
        # ==============================================================================
        # 本轮大攻势发起前的“深渊底”，用于后续测量目标价的起始丈量点
        _prior_swing_low_raw = df['low'].rolling(min_periods=1, window=self.LOOKBACK_WINDOW).min().shift(2)

        # 把这些底层锚定数据仅在发生突破的时刻锁定，并向下 ffill 给回调期使用
        _breakout_low = np.where(df['is_breakout'], df['low'], np.nan)
        _breakout_low = pd.Series(_breakout_low, index=df.index).ffill()

        _gap_floor = np.where(df['is_breakout'], _gap_floor_raw, np.nan)
        _gap_floor = pd.Series(_gap_floor, index=df.index).ffill()
        
        _prior_swing_low = np.where(df['is_breakout'], _prior_swing_low_raw, np.nan)
        _prior_swing_low = pd.Series(_prior_swing_low, index=df.index).ffill()

        # ==============================================================================
        # 第三步：持续监测防线是否崩塌（Surviving The Pullback）
        # ==============================================================================
        # 1. 为每一次突破编号，以便分组跟踪回调
        _bars_since_breakout = df['is_breakout'].cumsum()
        _bar_count = df.groupby(_bars_since_breakout).cumcount()

        # 2. 提取同一突破信号内，截至当前的累计被探明的最低点
        _group_min_low = df['low'].groupby(_bars_since_breakout).expanding().min().droplevel(0)
        
        # 3. 护城河铁律：这波回调期内，绝不能有任何一K线的最低点跌穿 gap floor
        # （同样加上 1e-3 容差）
        df['struct_gap_open'] = _group_min_low > (_gap_floor - 1e-3)

        # ==============================================================================
        # 第四步：回调衰竭与二次发力确认（Signal Bar Confirmation）
        # ==============================================================================
        # 1. 至少回调了 N 根 K 线 (给市场以确认的时间)
        in_window = (_bar_count >= self.MIN_PULLBACK_WINDOW) & (_bar_count <= self.MAX_PULLBACK_WINDOW)
        in_window = in_window & (_bars_since_breakout > 0) # 排除在没突破前的真空期数据

        # 2. 下跌停止：当前 K 线的 Low 必须高于或等于前一根的 Low (Lower Low 的终结)
        lower_low_stopped = df['low'] >= df['low'].shift(1)
        
        # 3. 强力反转：当前的 High 强势突破昨天阴线/十字星的 High (确认多头重新接管)
        # 并要求当前收盘价尽量是个强阳线，以规避假突破
        high_taken_out = (df['high'] > df['high'].shift(1)) & df['is_bullish'] & (df['close'] > df['close'].shift(1))

        # [V8.0 AL BROOKS PA FILTER]: 信号 K 线的绝对强势判定 (Signal Bar Quality)
        # 不再做硬性拦截，而是将其作为 Probability Score 输出
        _bar_range = df['high'] - df['low']
        _sig_quality = (df['close'] - df['low']) / _bar_range.replace(0, np.nan)
        df['sig_bar_quality'] = _sig_quality.round(3)

        # 4. 组装：缺口幸存 + 时间窗匹配 + 下跌停滞 + 反转突破 (放宽基础入场条件)
        _reversal_confirm_raw = in_window & df['struct_gap_open'] & lower_low_stopped & high_taken_out

        # [高潮规避器]：在确认反转前，该走势不得已经打满翻倍目标。
        # 否则此突破确认就成了强弩之末。
        # 此处提前运算简单的有效顶部（A值）：既然回调能够不断压缩缺口上沿，那么我们的测距中轴线也必须跟着下移。
        # 因此有效顶部必须使用整个回调期砸出的最低点 _group_min_low.shift(1) (容错突破初期用 _breakout_low 保底)
        _effective_gap_top = pd.concat([_group_min_low.shift(1), _breakout_low], axis=1).min(axis=1)
        _gap_mid = (_effective_gap_top + _gap_floor) / 2
        _target_uncond = 2 * _gap_mid - _prior_swing_low

        _group_max_high = df['high'].groupby(_bars_since_breakout).expanding().max().droplevel(0)
        # 如果从突破到当前 K 线（含当前大涨翻转阳线）的最高点已经超过了测距目标，
        # 则说明多头火力已经充分释放，这个“回调”确认已经是强弩之末
        _mm_not_reached = (_group_max_high < _target_uncond) | _target_uncond.isna()
        _mm_not_reached = _mm_not_reached.fillna(True)

        _signal_raw = _reversal_confirm_raw & _mm_not_reached

        # 去重：每次结构性突破，只认首次发生的反转点为最佳入场点，之后的均放弃
        _already_confirmed = _signal_raw.groupby(_bars_since_breakout).cumsum().shift(1).fillna(0) > 0
        df['signal_struct_gap_confirm'] = _signal_raw & ~_already_confirmed

        # 为回测和分析保留数据结构
        df['bars_since_breakout'] = _bars_since_breakout

        # ==============================================================================
        # 第五步：非对称测距目标与定单生成（The Measuring Gap Objective）
        # ==============================================================================
        # 当信号产生时（T日），买单（Buy Stop）挂在今日最高点之上，防守（SL）挂在百日顶被突破的 Gap Floor。
        # TP 则是以有效缺口顶部和 Gap Floor 取中线后与大底的镜像翻倍。
        
        # SL = 百日突破顶 (阻力化支撑)
        df['sl_struct_gap'] = np.where(df['signal_struct_gap_confirm'], _gap_floor, np.nan)
        
        # Entry = 本日反转企稳大阳线的最高点 (次日一旦突破此点，顺势而为)
        df['entry_struct_gap'] = np.where(df['signal_struct_gap_confirm'], df['high'], np.nan)
        
        # TP = _target_uncond (非对称镜像翻倍)
        # TP1 我们设定为一倍镜像距离, 若有 TP2, 可设1.5倍或移动止损
        df['tp_struct_gap'] = np.where(df['signal_struct_gap_confirm'], _target_uncond, np.nan)
        
        # 暴露绘图所需的关键结构锚点
        df['struct_gap_prior_low'] = np.where(df['signal_struct_gap_confirm'], _prior_swing_low, np.nan)
        df['struct_gap_floor_exact'] = np.where(df['signal_struct_gap_confirm'], _gap_floor, np.nan)
        
        # 缺口的上沿：在整个跌回来的回调过程中，价格能够砸到的最低低点。
        # 当在 T 日发出反转信号时，在此之前的整个回调期(包含突破日本身) 的最低点，就是真正的有效缺口上沿。
        # 也就是昨天为止的 _group_min_low。这就完美解决了回调过程中由于价格不断创新低导致缺口渐渐“被压缩缩小”的客观现象。
        df['struct_gap_top_exact'] = np.where(df['signal_struct_gap_confirm'], _group_min_low.shift(1), np.nan)
        
        # 🟢 新增画图定点所需的三个时间坐标序列 (Index)
        # 获取 _prior_swing_low 对应的 Date (波段底基准K线)
        # 使用 Series 内置的 rolling apply, 转成 Series 获取它的 idxmin
        def get_idxmin(x):
            return pd.Series(x).idxmin()
            
        def get_idxmax(x):
            return pd.Series(x).idxmax()
            
        # 注意: apply 会返回数值而不是 timestamp index,
        # 更稳健的方法是使用 argsort 或 numpy
        # 但是对于小数据量，我们可以牺牲一点性能，直接使用 rolling 窗口切片
        # 为避免复杂，最直接的 pandas 原生查找：
        # 构建一个与原 df index 一致的 Series
        try:
            # 尝试较为现代的版本 (如果支持)
            df['struct_gap_prior_low_date'] = df['low'].rolling(window=60, min_periods=1).idxmin().shift(2)
            df['struct_gap_floor_date'] = df['high'].rolling(window=60, min_periods=1).idxmax().shift(2)
        except AttributeError:
            # Pandas 旧版不支持 rolling.idxmin, 用 apply
            # rolling apply() 需要 raw=False 才能访问 index, 但非常慢。
            # 为了高性能解决，我们转而在绘图侧 notifier.py 处理
            pass
        
        # 记录首次探底测试缺口的K线（用来锚定画缺口矩形的右侧边界）
        # 就是触发信号前的那根K线 (也就是引发确认反转的低值坑)
        df['struct_gap_test_date'] = df.index.to_series().shift(1)

        return df

    def _calculate_context(self, df: pd.DataFrame) -> str:
        try:
            latest = df.iloc[-1]
            gap_status = "OPEN (Structurally Protected)" if latest.get('struct_gap_open', False) else "CLOSED / COMPROMISED"
            
            sig = latest.get('signal_struct_gap_confirm', False)
            entry = latest.get('entry_struct_gap', np.nan)
            sl = latest.get('sl_struct_gap', np.nan)
            tp = latest.get('tp_struct_gap', np.nan)
            
            if sig and not np.isnan(entry):
                status_str = f"LOCKED ✅ Buy Stop={entry:.2f} | Floor(SL)={sl:.2f} | Proj(TP)={tp:.2f}"
            else:
                status_str = "MONITORING THE PULLBACK"
                
            return f"""
<STRUCT_GAP_CONTEXT>
  <MACRO_WINDOW>{self.LOOKBACK_WINDOW} Bars</MACRO_WINDOW>
  <BREAKOUT_DEFENSIVE_FLOOR>{gap_status}</BREAKOUT_DEFENSIVE_FLOOR>
  <SETUP_STATUS>{status_str}</SETUP_STATUS>
</STRUCT_GAP_CONTEXT>
"""
        except:
            return "<STRUCT_GAP_CONTEXT_ERROR/>"

    def format_prompt(self, context_data: Dict) -> str:
        code = context_data.get('code', 'Unknown')
        df = context_data['df']
        ctx = get_common_context(df)
        context_xml = self._calculate_context(df)
        
        return f"""
# 👤 ROLE: Al Brooks (Price Action Master)

您正在审计极高胜率与盈亏比的【Structural Base Breakout (百周/百日底突破)】形态。
这里不再拘泥于 3根或4根阳线，而是关注绝对的物理跳空结构是否守擂成功。

# 🕵️ Brooks Framework For Structural Gaps
1. **The Breakout (脱离性质)**: 股价之前跨越了 60 个周期的最高点，这代表了供需失衡的极端质变。
2. **The Surviving Pullback (幸存的回撤)**: 只要回撤底部从没有摸到过被突破的百根高点，该缺口就是完全无菌的“ Measuring Gap ”。它是多头无死角的领地。
3. **The Signal (进攻型号)**: H1 止跌、且高点被突破。代表最后的抛压洗盘结束。
4. **The Math (算法倍率)**: 盈亏比（目标价位距离/止损区距离）如果大于 1:2，属于绝对的神级入场。

# 📊 市场微观结构与指标
{ctx['csv_str']}

# 🧪 结构系统探测器输出
{context_xml}

# 📝 审计报告 (XML)
请严格审视其反转企稳 K 线的质量（实体是否饱满，是否十字星）：
<ANALYSIS>
- Breakout Validation: (How convincing was the initial structural break?)
- Pullback Action: (Did it probe smoothly or violently?)
- Signal Bar Quality: (Is today a decent High 1 / High 2 reversal bar?)
</ANALYSIS>
<PA_TAGS>结构性跳空, 缩量回踩, 破前低企稳, 阻力转支撑确认</PA_TAGS>
<VERDICT>PASS / NO TRADE</VERDICT>
<DISCORD>审计结论</DISCORD>
"""

    def parse_result(self, response_text: str) -> Dict:
        from core.formatter import parse_response
        parsed = parse_response(response_text)
        
        tags_match = re.search(r"<PA_TAGS>(.*?)</PA_TAGS>", response_text, re.DOTALL | re.IGNORECASE)
        if tags_match:
            tags = tags_match.group(1).strip()
            parsed['pa_tags'] = tags
            parsed['reason'] = tags
            
        return parsed
