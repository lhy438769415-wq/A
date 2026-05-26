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
from typing import Dict, Any
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

    # =====================================================================
    # P1: Self-Describing Interface
    # =====================================================================
    @classmethod
    def get_metadata(cls) -> Dict[str, Any]:
        """Structural Gap 策略元数据声明"""
        return {
            'display_name': '结构缺口',
            'sl_column': 'sl_struct_gap',
            'entry_column': 'entry_struct_gap',
            'tp_columns': ['tp_struct_gap'],
            'score_column': 'sig_bar_quality',
            'signal_column': 'signal_struct_gap_confirm',
            'supported_timeframes': ['daily', 'weekly'],
            'tp_multiplier': 2.0,
        }

    @classmethod
    def get_signal_info(cls, df: pd.DataFrame) -> Dict[str, Any]:
        """Structural Gap 信号信息提取 — 包含 EV 评级"""
        result = super().get_signal_info(df)
        
        if df is None or df.empty:
            return result
        
        extra_info = result.get('extra_info', {})
        row = df.iloc[-1]
        
        # Signal bar quality
        q = row.get('sig_bar_quality', 0)
        extra_info['sig_quality'] = q
        
        # 回调连阴数计算
        if 'bars_since_breakout' in df.columns and not pd.isna(row.get('bars_since_breakout')):
            pb_bars = int(row['bars_since_breakout'])
            if pb_bars > 0 and len(df) >= pb_bars:
                pb_df = df.iloc[-(pb_bars+1):-1]
                is_bear = pb_df['close'] < pb_df['open']
                shifts = is_bear != is_bear.shift()
                groups = shifts.cumsum()
                bear_groups = is_bear.groupby(groups).sum()
                extra_info['pb_consec_bear'] = int(bear_groups.max()) if not bear_groups.empty else 0
            else:
                extra_info['pb_consec_bear'] = 0
        else:
            extra_info['pb_consec_bear'] = 0
        
        # 动态 EV 评级
        bears = extra_info['pb_consec_bear']
        if q > 0.8 and bears < 2:
            extra_info['ev_rating'] = '🌟 高预期'
        elif q <= 0.5 and bears >= 2:
            extra_info['ev_rating'] = '⚠️ 低预期'
        else:
            extra_info['ev_rating'] = '👍 常态'
        
        if extra_info:
            result['extra_info'] = extra_info
        
        return result

    @classmethod
    def annotate_chart(cls, ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> None:
        """Structural Gap 图表标注 — 缺口矩形、参数面板、买入点、止盈"""
        _annotate_gap_strategy(ax, plot_df, strategy_type, **kwargs)

    def __init__(self):
        # 1. Lookback Window - 突破判定所需的周期, 默认 60 根 (A股日线约3个月, 周线约1.2年)
        self.LOOKBACK_WINDOW = getattr(settings, 'STRUCT_GAP_LOOKBACK', 60)
        # 2. 确认回调的最大跟踪周期 
        self.MAX_PULLBACK_WINDOW = getattr(settings, 'STRUCT_GAP_MAX_WINDOW', 40)
        # 3. 必须回调的最小周期（避免单根K线上窜下跳，给予真正的回调空间）
        self.MIN_PULLBACK_WINDOW = 2

        # 4. [Evolution] 自动优化的策略硬阈值 (自演进系统写入)
        import os, json
        self.optimized_rules = {}
        rules_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'config', 'gap_optimized_rules.json')
        if os.path.exists(rules_path):
            try:
                with open(rules_path, 'r', encoding='utf-8') as f:
                    self.optimized_rules = json.load(f)
                logger.info(f"Loaded {len(self.optimized_rules)} optimized rules for Structural Gap Strategy.")
            except Exception as e:
                logger.error(f"Failed to load optimized rules: {e}")

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

        # [V9.5 Evolution Filter] 应用系统进化的过滤阈值拦截低胜率形态
        _rules_filter = pd.Series(True, index=df.index)
        if self.optimized_rules:
            if 'min_sig_quality' in self.optimized_rules:
                _rules_filter &= (df['sig_bar_quality'] >= self.optimized_rules['min_sig_quality'])
            
            if 'min_gap_size_atr' in self.optimized_rules:
                # 缺口宽度 (ATR 倍数)
                _gap_top = _group_min_low.shift(1)
                _gap_size_atr = (_gap_top - _gap_floor) / df['atr'].replace(0, np.nan)
                _rules_filter &= (_gap_size_atr >= self.optimized_rules['min_gap_size_atr'])
                
            if 'max_retracement_depth' in self.optimized_rules:
                # 信号前一K线的最高点距离缺口下沿的深度比例 (越小越贴近底部)
                _gap_top = _group_min_low.shift(1)
                _depth = (df['high'] - _gap_floor) / (_gap_top - _gap_floor).replace(0, np.nan)
                _rules_filter &= (_depth <= self.optimized_rules['max_retracement_depth'])

        _signal_raw = _reversal_confirm_raw & _mm_not_reached & _rules_filter

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


# =====================================================================
# P1: Shared Gap Strategy Chart Annotation Function
# =====================================================================
def _annotate_gap_strategy(ax, plot_df: pd.DataFrame, strategy_type: str, **kwargs) -> None:
    """
    通用的缺口策略图表标注函数，供 StructuralGap / GapPinbar / GapH2 共用。
    
    从 notifier.py 中提取的逻辑，消除重复的策略名称判断。
    
    Args:
        ax: matplotlib Axes 对象
        plot_df: 绘图用 DataFrame (已 Datetime-indexed)
        strategy_type: 策略名称字符串
        **kwargs:
            - sl_price: float — 止损价 (用于水平线)
            - tp1: float — 第一止盈价
            - ev_rating: str — EV 评级标签
            - sig_quality: float — 信号K线质量分
            - bears: int — 连阴数
    """
    from matplotlib.patches import Rectangle
    
    sl_price = kwargs.get('sl_price', 0)
    tp1 = kwargs.get('tp1', 0)
    ev_rating = kwargs.get('ev_rating', '')
    sig_quality = kwargs.get('sig_quality', 0)
    bears = kwargs.get('bears', 0)
    
    # 策略列映射 — 基于 metadata 驱动
    from core.strategy_registry import StrategyRegistry
    strat_upper = strategy_type.upper()
    
    # 解析策略类以获取 metadata
    try:
        strat_cls = type(StrategyRegistry.get_strategy(strategy_type))
        meta = strat_cls.get_metadata()
    except Exception:
        # 兼容回退：如果不能获取 metadata，跳过标注
        return
    
    sig_col = meta.get('signal_column', '')
    prior_low_col = None
    floor_exact_col = None
    top_exact_col = None
    entry_col = meta.get('entry_column', '')
    sl_col = meta.get('sl_column', '')
    
    # 根据策略名称推导绘图专用列
    # 这些列不在 metadata 中因为它们仅供绘图使用
    if 'STRUCTURAL_GAP' in strat_upper:
        prior_low_col = 'struct_gap_prior_low'
        floor_exact_col = 'struct_gap_floor_exact'
        top_exact_col = 'struct_gap_top_exact'
    elif 'GAP_PINBAR' in strat_upper:
        prior_low_col = 'gap_pinbar_prior_low'
        floor_exact_col = 'gap_pinbar_floor_exact'
        top_exact_col = 'gap_pinbar_top_exact'
    elif 'GAP_H2' in strat_upper:
        prior_low_col = 'gap_h2_prior_low'
        floor_exact_col = 'gap_h2_floor_exact'
        top_exact_col = 'gap_h2_top_exact'
    
    try:
        # 1. 定位关键点
        signal_date = None
        is_pending_track = False
        
        if sig_col and sig_col in plot_df.columns and plot_df[sig_col].any():
            signal_date = plot_df[plot_df[sig_col]].index[-1]
            is_pending_track = False
        else:
            # 尝试寻找 pending 状态的突破
            breakout_col = None
            for col_name in ['is_breakout', 'is_breakout_gp', 'is_breakout_h2']:
                if col_name in plot_df.columns and plot_df[col_name].any():
                    if ev_rating and '追踪' in str(ev_rating):
                        signal_date = plot_df.index[-1]
                        breakout_col = col_name
                        is_pending_track = True
                        break
            if signal_date is None:
                return
        
        signal_price = plot_df.loc[signal_date]['low']
        
        # 获取精确防守价
        floor_price = sl_price if sl_price > 0 else (plot_df.loc[signal_date][floor_exact_col] if floor_exact_col and floor_exact_col in plot_df.columns else plot_df.loc[signal_date]['low'])
        if pd.isna(floor_price) or floor_price <= 0:
            pre_signal = plot_df.loc[:signal_date]
            floor_price = pre_signal['low'].min()
        
        prior_low = plot_df.loc[signal_date][prior_low_col] if prior_low_col and prior_low_col in plot_df.columns else floor_price * 0.98
        if pd.isna(prior_low) or prior_low <= 0:
            prior_low = floor_price * 0.98
        
        # 兼容: 倒求历史极值坐标点
        pre_signal = plot_df.loc[:signal_date]
        floor_candidates = pre_signal[pre_signal['high'] >= floor_price * 0.99]
        if not floor_candidates.empty:
            floor_date = floor_candidates.index[0]
        else:
            floor_date = pre_signal.index[len(pre_signal)//3]
        
        # 起点: 定位波段最低价那天
        abs_diff = (pre_signal['low'] - prior_low).abs()
        if abs_diff.min() < 1e-4:
            origin_date = abs_diff.idxmin()
        else:
            origin_date = pre_signal.index[0]
        
        # 回调测试极值点
        test_date = pre_signal.index[-2] if len(pre_signal) > 1 else pre_signal.index[0]
        
        # timestamp 转换为 x 坐标
        date_list = list(plot_df.index)
        try:
            signal_x = date_list.index(signal_date)
            origin_x = date_list.index(origin_date)
            floor_x = date_list.index(floor_date)
            test_x = date_list.index(test_date)
            
            origin_true_low = prior_low
            floor_true_high = plot_df.loc[floor_date, 'high']
            test_true_low = plot_df.loc[test_date, 'low']
        except ValueError:
            signal_x = len(plot_df) - 1
            origin_x, floor_x, test_x = signal_x - 40, signal_x - 20, signal_x - 1
            origin_true_low, floor_true_high, test_true_low = prior_low, floor_price, floor_price * 1.02
        
        exact_top_series = plot_df.loc[signal_date][top_exact_col] if top_exact_col and top_exact_col in plot_df.columns else None
        exact_floor_series = plot_df.loc[signal_date][floor_exact_col] if floor_exact_col and floor_exact_col in plot_df.columns else None
        
        final_gap_high = exact_top_series if pd.notna(exact_top_series) else test_true_low
        final_gap_low = exact_floor_series if pd.notna(exact_floor_series) else floor_true_high
        
        if final_gap_low >= final_gap_high:
            final_gap_high = final_gap_low * 1.02
        
        # 标注 1. 开放的缺口区域 (矩形绘制)
        center_x = max(0, len(plot_df) // 2)
        rect_start_x = min(center_x, test_x - 5)
        rect_width = test_x - rect_start_x
        rect_height = final_gap_high - final_gap_low
        
        gap_rect = Rectangle(
            (rect_start_x - 0.5, final_gap_low), 
            rect_width + 1, rect_height,
            linewidth=1.2, facecolor='none', edgecolor='#2962FF', linestyle='--', alpha=0.6
        )
        ax.add_patch(gap_rect)
        
        # 缺口下沿警戒线
        ax.axhline(y=final_gap_low, color='#2962FF', linestyle='-', linewidth=1, alpha=0.5)
        
        # 在矩形悬空居中位置标字
        label_x_mid = rect_start_x + rect_width / 2
        label_y_mid = final_gap_low + rect_height / 2
        ax.text(label_x_mid, label_y_mid,
                "防守缺口\n(Gap Zone)", color='#2962FF', fontsize=9, fontweight='normal', ha='center', va='center',
                bbox=dict(boxstyle='square,pad=0.2', facecolor='white', edgecolor='#2962FF', alpha=0.8))
        
        # 标注 2. 左上角参数面板
        if not is_pending_track:
            entry_price = plot_df.loc[signal_date][entry_col] if entry_col and entry_col in plot_df.columns else plot_df.loc[signal_date]['high']
            if pd.isna(entry_price):
                entry_price = plot_df.loc[signal_date]['high']
            rr_ratio = (tp1 - entry_price) / (entry_price - final_gap_low) if tp1 > entry_price and entry_price > final_gap_low else 0
        else:
            entry_price = plot_df.loc[signal_date]['high'] + 0.01
            rr_ratio = (tp1 - entry_price) / (entry_price - final_gap_low) if tp1 > entry_price and entry_price > final_gap_low else 0
        
        # 移除 emoji 防止 matplotlib 乱码
        def _strip_emoji(text):
            if not text: return text
            import re
            text = str(text)
            text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
            text = re.sub(r'[\u2600-\u27bf]', '', text)
            return text.strip()
        
        panel_text = f"买入点：{entry_price:.2f}\n" \
                     f"极限防守：{final_gap_low:.2f}\n" \
                     f"对称止盈：{tp1:.2f} ({rr_ratio:.2f}R)\n" \
                     f"------------------\n" \
                     f"动能质量：{sig_quality:.2f}\n" \
                     f"回调连阴：{bears} 连阴\n" \
                     f"系统评级：{_strip_emoji(ev_rating) if ev_rating else 'N/A'}"
        
        ax.text(0.02, 0.96, panel_text, transform=ax.transAxes, fontsize=10,
                verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray'))
        
        # 标注 3. 测量缺口的起点
        ax.annotate("起跳支点", 
                    xy=(origin_x + 0.5, origin_true_low), 
                    xytext=(origin_x + 6.5, origin_true_low),
                    arrowprops=dict(arrowstyle="->", color='#6A1B9A', lw=1.5, alpha=0.7),
                    fontsize=9, color='#6A1B9A', fontweight='normal', ha='left', va='center',
                    bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
        
        # 标注 3. 入场点
        if not is_pending_track:
            ax.annotate("买入点 (Buy Stop)", 
                        xy=(signal_x + 0.5, entry_price), 
                        xytext=(signal_x + 6.5, entry_price),
                        arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5),
                        fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                        bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
        else:
            ax.annotate("预期买点 (待反转)", 
                        xy=(signal_x + 0.5, entry_price), 
                        xytext=(signal_x + 6.5, entry_price),
                        arrowprops=dict(arrowstyle="->", color='#D32F2F', lw=1.5, linestyle="--"),
                        fontsize=9, color='#D32F2F', fontweight='bold', ha='left', va='center',
                        bbox=dict(boxstyle='square,pad=0.1', facecolor='white', edgecolor='none', alpha=0.8))
        
        # 标注 4. 测量缺口止盈
        if tp1 > 0:
            ax.axhline(y=tp1, color='#D32F2F', linestyle='--', linewidth=1.2, alpha=0.6)
            ax.annotate("TP (目标)", 
                        xy=(signal_x, tp1), 
                        xytext=(signal_x - 8, tp1),
                        arrowprops=dict(arrowstyle="-", color='#D32F2F', alpha=0),
                        fontsize=9, color='#D32F2F', fontweight='bold', ha='right', va='center',
                        bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#D32F2F', alpha=0.7))
                        
    except Exception as e:
        if str(e) != "SILENT_SKIP":
            logger.warning(f"Gap Strategy Annotation Failed: {e}")
