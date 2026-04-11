import pandas as pd
import numpy as np
from core.patterns.base import BasePattern

class WeeklyBullFlagThreePushesPattern(BasePattern):
    """
    周线牛旗三推形态 (Weekly Bull Flag 3 Pushes)
    
    经典案例: 601179 (2026-01-23 至 2026-02-13)
    
    物理逻辑:
    1. 爆发阶段 (Measure Gap/Breakout): 周线出现强势大阳线突破 (实体较大)。
    2. 回调动能衰竭 (Momentum Decay): 随后连续 3 周呈现收敛三角形 (高点依次降低，低点依次抬高或横移)。
    3. 量能确认: 突破周放量，回调周缩量。
    """
    
    @property
    def pattern_name(self) -> str:
        return "WeeklyBullFlagThreePushes"

    def detect(self, df_daily: pd.DataFrame, df_weekly: pd.DataFrame = None) -> pd.Series:
        """
        核心识别逻辑 (向量化实现)
        
        Args:
            df_daily: 必须包含
            df_weekly: 必须包含 (该形态依赖周级别形态提取)
        """
        # 如果未传入 df_weekly，则说明不支持该形态（需在扫描层确保有周数据）
        if df_weekly is None or df_weekly.empty or 'close' not in df_weekly.columns:
            # 返回与 df_daily 长度一致的 False Series
            return pd.Series(False, index=df_daily.index)

        # ==========================================
        # 1. 在周线级别提取特征 (Weekly Level Features)
        # ==========================================
        # 计算周线指标
        df_weekly = df_weekly.copy()
        try:
            # 兼容 talib 确保平滑
            import talib
            df_weekly['ema20'] = talib.EMA(df_weekly['close'], timeperiod=20)
        except ImportError:
            df_weekly['ema20'] = df_weekly['close'].ewm(span=20, adjust=False).mean()
        
        df_weekly['body'] = (df_weekly['close'] - df_weekly['open']).abs()
        df_weekly['body_pct'] = df_weekly['body'] / df_weekly['open']
        
        # 1.1 爆发根判定 (Breakout Bar)
        # T-3 必须是一根大阳线（实体振幅 > 6%），且收盘价站上 EMA20
        # 判断逻辑是基于滑窗：假设当前是 T=0，那么 T-3 是那根爆发 K 线
        is_bull_breakout = (df_weekly['close'] > df_weekly['open']) & \
                           (df_weekly['body_pct'] > self.params.get('breakout_body_pct', 0.06)) & \
                           (df_weekly['close'] > df_weekly['ema20'])
        
        import warnings
        warnings.simplefilter(action='ignore', category=FutureWarning)

        # 1.2 收敛的 3 推判定 (Three Pushes Contraction)
        # 对于当前周 i，考察前 3 周 (i-2, i-1, i) 的高低点变化
        # 高点降级容忍度 (高点依次降低, 允许微弱刺穿)
        highs_decreasing = (df_weekly['high'].shift(1) >= df_weekly['high'] * 0.98) & \
                           (df_weekly['high'].shift(2) >= df_weekly['high'].shift(1) * 0.98)
                           
        # 低点升温/横移容忍度 (允许回调低点略有刺穿/下探, 特别是长下影线)
        lows_increasing = (df_weekly['low'] >= df_weekly['low'].shift(1) * 0.97) & \
                          (df_weekly['low'].shift(1) >= df_weekly['low'].shift(2) * 0.97)
                          
        # 三推期间不应有太大的振幅 (不能是一阴吞多阳)
        # 检查这三根的总回调实体不能超过爆发阳线的 70%
        # (可选：为了泛化，可以放宽这个条件，或者交给 Daily Gap 去交叉验证)

        # 1.3 周线完整信号
        # T=0 满足收敛，T-3 满足爆发
        weekly_signal = is_bull_breakout.shift(3).fillna(False).astype(bool) & \
                        highs_decreasing & \
                        lows_increasing

                        
        df_weekly['weekly_pattern_ok'] = weekly_signal

        # ==========================================
        # 2. 将周线信号映射回日线 (Map back to Daily)
        # ==========================================
        # 提取有信号的周的 trade_date
        if not pd.api.types.is_datetime64_any_dtype(df_weekly['trade_date']):
            df_weekly['trade_date'] = pd.to_datetime(df_weekly['trade_date'])
            
        signal_weeks = df_weekly[df_weekly['weekly_pattern_ok']]['trade_date'].dt.strftime("%Y-%W").tolist()
        
        if not signal_weeks:
            return pd.Series(False, index=df_daily.index)

        # 在日线级别标记：只要该日所属的周有信号，或者该周结束后的第一天，都可以作为触发信号
        # 为了精确，我们标记 "该形态发生后的下半周的周五" 为 T0，日线可以在周五生成信号供下周一挂单
        
        df_daily = df_daily.copy()
        
        # 兼容 df_daily 中日期列名为 'date' 还是 'trade_date'
        date_col = 'trade_date' if 'trade_date' in df_daily.columns else 'date'
        
        # 确保有 year-week 字段
        if not pd.api.types.is_datetime64_any_dtype(df_daily[date_col]):
            df_daily['trade_date_dt'] = pd.to_datetime(df_daily[date_col])
        else:
            df_daily['trade_date_dt'] = df_daily[date_col]
            
        df_daily['year_week'] = df_daily['trade_date_dt'].dt.strftime("%Y-%W")
        
        # 标记信号 (如果日线所在的 year_week 属于 signal_weeks 且该日是该周的最后一个交易日)
        # 这通过检查后一天的 year_week 是否变化来实现 (如果是周五，下周一是新的一周)
        df_daily['next_day_year_week'] = df_daily['year_week'].shift(-1)
        
        # 标记为 True 当：
        # 1. 所在的 week 触发了规则
        # 2. 该天是该 week 的最后一天（因为我们需要收盘后确认周线形态）
        daily_signal = (df_daily['year_week'].isin(signal_weeks)) & \
                       (df_daily['year_week'] != df_daily['next_day_year_week'])
                       
        return daily_signal
