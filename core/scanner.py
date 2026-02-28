# core/scanner.py
import traceback
import logging
import time
import warnings
# 🟢 Suppress FutureWarnings (e.g., from pandas internals)
warnings.simplefilter(action='ignore', category=FutureWarning)

from tools.data_manager import get_stock_data
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
                
                # 映射止损列
                sl_col_map = {
                    'STRATEGY_3K_EX': 'sl_3k',
                    'STRATEGY_3K': 'sl_3k_gap_test',
                    'STRATEGY_STRUCTURAL_GAP': 'sl_struct_gap',
                    'MTR_V29_MASTER': 'sl_price',
                    'MTR_MASTER': 'sl_price',
                    'MTR_V35_STRUCTURAL': 'sl_price' # 🟢 V35.0 Explicit Mapping
                }

                if strat.name in sl_col_map:
                    target_col = sl_col_map[strat.name]
                    if target_col in df_strat.columns:
                        df_strat['sl_price'] = df_strat[target_col]
                        
                # 🟢 为 3K 映射入场价 (Buy Stop)
                if '3K' in strat.name.upper() and 'entry_3k_gap_test' in df_strat.columns:
                    df_strat['entry_price'] = df_strat['entry_3k_gap_test']
                    
                # 🟢 为 Structural Gap 映射入场价与止盈价
                if 'STRUCTURAL_GAP' in strat.name.upper():
                    if 'entry_struct_gap' in df_strat.columns: df_strat['entry_price'] = df_strat['entry_struct_gap']
                    if 'tp_struct_gap' in df_strat.columns: df_strat['tp1_price'] = df_strat['tp_struct_gap']
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
                        # 🟢 V8.13 归位：遵循 Brooks 理论，不再拍脑袋。
                        # 3K 是 1R 止盈，MTR 是 2R 止盈
                        if "3K" in strat.name.upper():
                            df_strat['tp1_price'] = df_strat['entry_price'] + (risk * 1.0)
                            df_strat['tp2_price'] = df_strat['entry_price'] + (risk * 2.0)
                        else:
                            df_strat['tp1_price'] = df_strat['entry_price'] + (risk * 2.0)
                            df_strat['tp2_price'] = df_strat['entry_price'] + (risk * 4.0)

                row = df_strat.iloc[-1]

                # 🟢 [3K V2.1] 补充缺口测试确认信息和评分
                extra_info = {}
                if '3K' in strat.name.upper():
                    # 尝试找最近的缺口测试确认信号 (可能在最近20根K线内)
                    gt_col = 'signal_3k_gap_test'
                    if gt_col in df_strat.columns:
                        recent = df_strat.tail(25)
                        gt_rows = recent[recent[gt_col] == True]
                        if not gt_rows.empty:
                            gt_row = gt_rows.iloc[-1]
                            import numpy as np
                            gt_entry = gt_row.get('entry_3k_gap_test', np.nan)
                            gt_sl = gt_row.get('sl_3k_gap_test', np.nan)
                            gt_tp = gt_row.get('tp_3k_gap_test', np.nan)
                            if not np.isnan(gt_entry):
                                extra_info['gap_test_entry'] = gt_entry
                                extra_info['gap_test_sl'] = gt_sl
                                extra_info['gap_test_tp'] = gt_tp
                                risk = gt_entry - gt_sl if not np.isnan(gt_sl) else 0
                                reward = gt_tp - gt_entry if not np.isnan(gt_tp) else 0
                                extra_info['gap_test_rr'] = round(reward / risk, 1) if risk > 0 else 0
                    # 3K 评分 (简化版: 使用 location_pct)
                    loc = row.get('location_pct', 0.5)
                    extra_info['score'] = round((1 - loc) * 100, 1)  # 低位加分

                # 🟢 [Structural Gap V8.5] 补充 EV 概率评级分
                if 'STRUCTURAL_GAP' in strat.name.upper():
                    q = row.get('sig_bar_quality', 0)
                    extra_info['sig_quality'] = q
                    
                    # 尝试找到回调期的连阴数
                    if 'bars_since_breakout' in df_strat.columns and not pd.isna(row.get('bars_since_breakout')):
                        pb_bars = int(row['bars_since_breakout'])
                        # 取从突破日以来的切片
                        if pb_bars > 0 and len(df_strat) >= pb_bars:
                            pb_df = df_strat.iloc[-(pb_bars+1):-1]
                            is_bear = pb_df['close'] < pb_df['open']
                            shifts = is_bear != is_bear.shift()
                            groups = shifts.cumsum()
                            bear_groups = is_bear.groupby(groups).sum()
                            extra_info['pb_consec_bear'] = int(bear_groups.max()) if not bear_groups.empty else 0
                        else:
                            extra_info['pb_consec_bear'] = 0
                    else:
                        extra_info['pb_consec_bear'] = 0
                        
                    # 动态评级 (Dynamic EV Rating)
                    bears = extra_info['pb_consec_bear']
                    if q > 0.8 and bears < 2:
                        extra_info['ev_rating'] = '🌟 高预期'
                    elif q <= 0.5 and bears >= 2:
                        extra_info['ev_rating'] = '⚠️ 低预期'
                    else:
                        extra_info['ev_rating'] = '👍 常态'

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
