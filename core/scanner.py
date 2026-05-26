# core/scanner.py
import traceback
import logging
import time
import warnings
import pandas as pd
# 🟢 Suppress FutureWarnings (e.g., from pandas internals)
warnings.simplefilter(action='ignore', category=FutureWarning)

# 🟢 [Phase1] 统一数据层：直接使用 core.data_provider，不经 tools.data_manager 薄代理
from core.data_provider import get_stock_data
from core.calculator import add_indicators, calculate_targets
from core.strategy_registry import StrategyRegistry
from typing import Optional, Dict, Any
from config import settings

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def run_scanner(code: str, strategy_name: str = 'MTR_MASTER') -> Optional[Dict[str, Any]]:
    """
    [V8.8 向量化多策略扫描器]
    逻辑变更为：
    1. 获取数据 (一次)
    2. 计算指标 (一次)
    3. 并行/串行计算指定策略信号 (Vectorized)
    4. 检查最后一根K线是否有信号，返回命中的第一个策略 (保持与旧接口兼容)
    """
    start_time = time.time()
    
    # 兼容性处理：如果传入的是字符串，转为列表
    strategy_names = [strategy_name] if isinstance(strategy_name, str) else strategy_name
    if not strategy_names:
        strategy_names = ['MTR_MASTER']

    # 1. 获取数据 (Pre-fetch)
    df = get_stock_data(code, limit=settings.STRATEGY_DATA_FETCH_LIMIT)

    if df is None or len(df) < settings.STRATEGY_MIN_DATA_LENGTH:
        return None

    # 数据清洗
    if df.isna().any().any():
        df = df.ffill().bfill().infer_objects(copy=False)
    
    # 🟢 [Optimization] Reset Index for consistent alignment & avoid FutureWarnings
    df.reset_index(drop=True, inplace=True)

    # 2. 计算通用指标 (Indicators) - 仅计算一次
    try:
        df = add_indicators(df)
    except Exception as e:
        logger.error(f"Indicator calculation failed for {code}: {e}")
        return None

    # ==========================================
    # 策略扫描 (Multi-Strategy Execution)
    # ==========================================
    for name in strategy_names:
        try:
            strat = StrategyRegistry.get_strategy(name)
            # V8.0: Vectorized calculation
            df_strat = strat.calculate_signals(df.copy())
            
            # 检查最新K线信号
            latest_signal = df_strat.iloc[-1][strat.signal_column]
                
            if latest_signal:
                logger.info(f"✨ 策略命中 [{strat.name}]: {code}")
                
                # P1: 使用策略自描述接口替代硬编码列映射
                signal_info = strat.get_signal_info(df_strat)
                
                # 映射止损列
                sl_col = strat.get_metadata().get('sl_column', '')
                if sl_col and sl_col in df_strat.columns:
                    df_strat['sl_price'] = df_strat[sl_col]
                
                # 映射入场价
                entry_col = strat.get_metadata().get('entry_column', '')
                if entry_col and entry_col in df_strat.columns:
                    df_strat['entry_price'] = df_strat[entry_col]
                
                # 映射止盈价
                tp_cols = strat.get_metadata().get('tp_columns', [])
                if tp_cols and tp_cols[0] in df_strat.columns:
                    df_strat['tp1_price'] = df_strat[tp_cols[0]]
                    if len(tp_cols) > 1 and tp_cols[1] in df_strat.columns:
                        df_strat['tp2_price'] = df_strat[tp_cols[1]]
                
                # Ensure targets exist
                if 'sl_price' not in df_strat.columns:
                    df_strat = calculate_targets(df_strat)
                
                if 'tp1_price' not in df_strat.columns:
                    if 'entry_price' not in df_strat.columns: df_strat['entry_price'] = df_strat['close']
                    if 'tp1_target' in df_strat.columns:
                        df_strat['tp1_price'] = df_strat['tp1_target']
                        df_strat['tp2_price'] = df_strat['tp2_target']
                    else:
                        risk = (df_strat['entry_price'] - df_strat['sl_price']).abs()
                        # P1: 使用 metadata 中的 tp_multiplier 代替硬编码策略名判断
                        tp_mult = strat.get_metadata().get('tp_multiplier', 2.0)
                        df_strat['tp1_price'] = df_strat['entry_price'] + (risk * tp_mult)
                        df_strat['tp2_price'] = df_strat['entry_price'] + (risk * tp_mult * 2.0)

                row = df_strat.iloc[-1]

                # P1: extra_info 从 get_signal_info 获取 (策略自描述)
                extra_info = signal_info.get('extra_info', {})
                
                # 兼容: score 字段回退
                if 'score' not in extra_info:
                    score_col = strat.get_metadata().get('score_column', '')
                    if score_col and score_col in row.index:
                        val = row.get(score_col, 0)
                        extra_info['score'] = float(val) if pd.notna(val) else 0
                    elif 'mtr_score' in row.index:
                        extra_info['score'] = row.get('mtr_score', 0)

                return {
                    'code': code,
                    'type': strat.name,
                    'info': {
                        'price': row['close'],
                        'entry': row.get('entry_price', row['close']),
                        'sl': row.get('sl_price', 0),
                        'tp1': row.get('tp1_price', 0),
                        'tp2': row.get('tp2_price', 0),
                        'atr': row.get('atr', 1),
                        'type': strat.name,
                        'score': extra_info.get('score', row.get('mtr_score', 0) if 'mtr_score' in row else 0),
                        'signal_bar_idx': int(row['mtr_signal_bar_idx']) if ('mtr_signal_bar_idx' in row and row['mtr_signal_bar_idx'] == row['mtr_signal_bar_idx']) else -1,
                        **extra_info
                    },
                    'df': df_strat.tail(70)
                }

        except Exception as e:
            logger.warning(f"Strategy {name} error for {code}: {e}")
            continue

    return None
