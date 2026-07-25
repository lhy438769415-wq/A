import pandas as pd
import numpy as np

class BullishPinbarStrategy:
    """
    量化形态: 强趋势/支撑位长下影阳线 (Bullish Pinbar)
    """

    def __init__(self):
        # 参数设置 (可调)
        # 用户要求: 下影线占比需超过2/3 (0.66), 收盘位置为最高 (接近 1.0)
        self.MIN_LOWER_WICK_PCT = 0.66  # 下影线至少占 K 线全长的 66%
        self.MIN_CLOSE_LOC = 0.95       # 收盘价位置极高 (接近光头)
        self.EMA_PERIOD = 20            # 趋势均线
        self.SUPPORT_TOLERANCE_PCT = 0.01 # 支撑位判定的容差 (1%)

    def detect(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算信号并返回带有 'signal_pinbar' 列的 DataFrame
        """
        if df.empty: return df
        df = df.copy()

        # 1. 基础指标计算 (如果 df 中没有)
        if 'ema20' not in df.columns:
            df['ema20'] = df['close'].ewm(span=self.EMA_PERIOD, adjust=False).mean()
        
        # K线几何特征
        df['range'] = df['high'] - df['low']
        df['body_bottom'] = np.where(df['close'] >= df['open'], df['open'], df['close'])
        df['body_top'] = np.where(df['close'] >= df['open'], df['close'], df['open'])
        df['lower_wick'] = df['body_bottom'] - df['low']
        df['upper_wick'] = df['high'] - df['body_top']
        
        # 避免除以零
        df['range'] = df['range'].replace(0, 0.0001)
        
        df['lower_wick_pct'] = df['lower_wick'] / df['range']
        df['close_loc'] = (df['close'] - df['low']) / df['range']
        df['is_bullish'] = df['close'] > df['open']

        # ==========================================
        # 2. 信号棒筛选 (Signal Bar Filters)
        # ==========================================
        # 条件1: 阳线
        cond_bullish = df['is_bullish']
        
        # 条件2: 长下影线 (占比大)
        cond_long_wick = df['lower_wick_pct'] >= self.MIN_LOWER_WICK_PCT
        
        # 条件3: 高位收盘 (几乎光头)
        cond_high_close = df['close_loc'] >= self.MIN_CLOSE_LOC

        signal_bar_ok = cond_bullish & cond_long_wick & cond_high_close

        # ==========================================
        # 3. 环境与共振 (Context & Confluence)
        # ==========================================
        
        # 环境A: EMA20 趋势支持
        # 收盘价 > EMA20 (保持多头结构)
        cond_trend_up = df['close'] > df['ema20']
        
        # 回踩 EMA20: Low 触及 EMA20 (High > EMA > Low) OR (Low < EMA < Low+Tolerance)
        # 简单判定: Low <= 均线 * 1.01 AND Low >= 均线 * 0.98 (在均线附近)
        # 或者更简单的 Brooks 定义: "Touching Moving Average"
        dist_ema_pct = (df['low'] - df['ema20']) / df['ema20']
        cond_touch_ema = (dist_ema_pct.abs() < 0.015) & cond_trend_up # 1.5% 容差

        # 环境B: 前低支撑 (Double Bottom)
        # 过去 20 根 K 线内的最低价 (不包含当前 K 线)
        rolling_low = df['low'].shift(1).rolling(window=20).min()
        dist_to_low = (df['low'] - rolling_low).abs() / df['close']
        cond_double_bottom = dist_to_low < self.SUPPORT_TOLERANCE_PCT

        # 环境C: 密集下影线共振 (Tweezer/Micro DB)
        # 过去 5 天是否有共振低点
        def has_nearby_low(series):
            current_low = series.iloc[-1]
            past_lows = series.iloc[:-1]
            diffs = np.abs(past_lows - current_low) / current_low
            return (diffs < 0.005).any() # 0.5% 极度精确的共振

        cond_confluence = df['low'].rolling(window=5).apply(has_nearby_low, raw=False).fillna(0).astype(bool)

        # ==========================================
        # 环境D: 缺口回踩 (Breakout Gap Pullback)
        # ==========================================
        # 逻辑: 过去 N 天内曾出现过向上缺口 (Gap Up)，且当前价格(Low) 没有完全回补该缺口
        # Gap Up: Low(t) > High(t-1)
        # 我们寻找过去 10 天内最大的 Gap
        
        # 计算每一天的 Gap Size (如果是 Gap Up)
        prev_high = df['high'].shift(1)
        curr_low = df['low']
        gap_size = curr_low - prev_high
        is_gap_up = gap_size > (df['close'] * 0.002) # 至少 0.2% 的缺口才算数
        
        # 检查过去 10 天是否有 Gap Up，且当前 Low 依然高于当你 Gap 发生时的 Prev High
        # 这是一个 "Unfilled Gap" 的检查
        # 简化版: 只要检测到过去 10 天有 Gap Up，并且 Current Low > That Gap's Base (Prev High)
        
        def check_gap_support(window_df):
            # window_df 包含当前 K 线 (最后一行) 和过去 N 行
            curr_bar_low = window_df['low'].iloc[-1]
            
            # 遍历过去 K 线寻找 Gap
            # 数据从旧到新
            for i in range(1, len(window_df)-1):
                # i 是 gap 发生的 K 线 (gap bar)
                # i-1 是 gap base (pre-gap bar)
                g_low = window_df['low'].iloc[i]
                g_base_high = window_df['high'].iloc[i-1]
                
                if g_low > g_base_high: # Found a gap
                    # 检查 gap 是否足够大 (可选)
                    # 检查当前 low 是否守住了 gap (即 curr_low >= g_base_high)
                    if curr_bar_low >= g_base_high:
                        return True
            return False

        # 过去 10 天 (window=11 includig current)
        # 注意: rolling apply 比较慢，但在日线级别完全没事
        # 需要传入 High 和 Low，Rolling Apply 只能针对 Series
        # 这里的 dirty hack: 我们假设 gap 是基于 Close 这种近似? 不行。
        # 只能用循环或者特定技巧。
        # 向量化方案: 
        # 1. 标识所有 Gap Up 的位置 -> mask
        # 2. 记录这些 Gap Base 的 High 价格 -> gap_support_level
        # 3. Forward Fill 这些 Level? 不行，可能有多个 Gap。
        # 简单起见，用 rolling apply on explicit structure 比较难。
        # 这里改用 Python 循环处理 (因为只在 daily 运行，且只针对 filtered candidates 也可以，但这里是 full detect)
        # 为了性能，我们只对 "signal_bar_ok" 的行进行环境检查? 
        # 不，detect 需要返回全量。
        
        # 妥协: 用最近的一个 Gap 判断
        # 寻找最近一次 Gap Up 的 Index
        
        # 暂时用 Rolling Apply (性能尚可)
        # 构造一个 tuple series? 不支持。
        # 构造 High_Low series?
        
        # 笨办法: 
        cond_gap_support = pd.Series(False, index=df.index)
        
        # 稍微优化: 只计算 signal_bar_ok 的点
        potential_idxs = df.index[signal_bar_ok]
        
        # 转为 numpy 加速
        highs = df['high'].values
        lows = df['low'].values
        
        for idx in potential_idxs:
            # Look back 10 bars
            pos = df.index.get_loc(idx)
            if pos < 10: continue
            
            c_low = lows[pos]
            
            # Check last 10 bars for gap
            for i in range(pos-10, pos):
                # Gap between i and i-1
                if i <= 0: continue
                if lows[i] > highs[i-1]:
                    # Gap exists
                    # Check if hold
                    if c_low >= highs[i-1]:
                        cond_gap_support.at[idx] = True
                        break

        # ==========================================
        # 环境E: 密集区突破跟随 (TTR Breakout Follow)
        # ==========================================
        # 1. 之前是 TTR (Tight Trading Range): 比如过去 5 根 K 线 range 很小，重叠度高
        # 2. 刚刚发生突破: 前一根 (t-1) 是大阳线，突破了 TTR 
        # 3. 当前 (t) 是 Pinbar 回踩或者是 High 1
        
        # TTR 定义: 过去 5 根 (t-6 to t-2) 的 High-Low 波动率极低
        # Breakout 定义: t-1 收盘价创 5 日新高，且实体大
        
        # 过去 5 根 (Shift 2)
        range_ma5 = df['range'].shift(2).rolling(5).mean()
        # 前一根 (Shift 1) 实体
        prev_body = (df['close'] - df['open']).abs().shift(1)
        prev_breakout = (df['close'].shift(1) > df['high'].shift(2).rolling(5).max()) & \
                        (prev_body > range_ma5 * 1.5) # 大实体突破
        
        cond_ttr_breakout = prev_breakout
        
        # ==========================================
        # 环境F: 趋势反转/新低 (Reversal with New Low)
        # ==========================================
        # 用户要求: "该信号k应该创造新低或者接近长期前低"
        # 创造新低: Low < Lowest(Past 20)
        past_20_low = df['low'].shift(1).rolling(20).min()
        cond_new_low = df['low'] < past_20_low
        
        # ==========================================
        # 综合信号分类 (Priority Matches User Specs)
        # ==========================================
        
        df['match_type'] = None
        mask_base = signal_bar_ok
        
        # 1. Gap Support (强趋势特征) - "突破回踩不破突破缺口"
        df.loc[mask_base & cond_gap_support, 'match_type'] = 'Gap Pullback'
        
        # 2. TTR Breakout Follow - "突破某个密集交易区后有好的跟随"
        # 需结合 EMA20 上方
        df.loc[mask_base & cond_ttr_breakout & cond_trend_up, 'match_type'] = 'TTR Breakout Buy'

        # 3. Reversal (New Low / Double Bottom) - "前低附近、区间底部、创造新低"
        # 包含 Double Bottom 或 New Low Sweep
        cond_reversal = cond_double_bottom | cond_new_low
        # 这种反转通常发生在 EMA20 下方(V型) 或 上方(深度回调)
        # 只要是 Reversal Bar (Pinbar) at extreme
        df.loc[mask_base & cond_reversal, 'match_type'] = 'Reversal/Sweep' # 覆盖了 Double Bottom

        # 4. EMA20 Pullback (常规趋势) - "趋势起点"
        mask_trend = mask_base & cond_touch_ema & pd.isna(df['match_type'])
        df.loc[mask_trend, 'match_type'] = 'EMA20 Pullback'
        
        # 5. Confluence (保底)
        mask_conf = mask_base & cond_confluence & pd.isna(df['match_type'])
        df.loc[mask_conf, 'match_type'] = 'Micro DB/Confluence'

        df['signal_pinbar'] = df['match_type'].notna()
        
        return df

if __name__ == "__main__":
    # 简单自测
    print("Testing implementation syntax...")
    fake_data = pd.DataFrame({
        'open': [10, 10.5, 10.2, 10.0, 10.0],
        'high': [11, 11.0, 10.8, 10.5, 11.0],
        'low':  [9,  9.5,  9.8,  9.0,  9.0], # Last bar matches first bar low (Double bottom)
        'close':[10.5, 9.8, 10.0, 9.5, 10.8] # Last bar: Open 10, Close 10.8 (Bull), Low 9 (Long wick)
    })
    strategy = BullishPinbarStrategy()
    res = strategy.detect(fake_data)
    print(res[['close', 'low', 'lower_wick_pct', 'signal_pinbar']])
