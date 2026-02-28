from typing import Dict, Type
from .strategies.base import BaseStrategy
from .strategies.mtr_strategy import MTRStrategy
from .strategies.three_k_strategy import ThreeKStrategy
from .strategies.structural_gap_strategy import StructuralGapStrategy

class StrategyRegistry:
    """
    Central registry for all trading strategies.
    Simplified to only MTR_MASTER, STRATEGY_3K and STRATEGY_STRUCTURAL_GAP.
    """
    
    # 内部映射表
    _strategies: Dict[str, Type[BaseStrategy]] = {
        "MTR_MASTER": MTRStrategy,
        "STRATEGY_3K": ThreeKStrategy,
        "STRATEGY_STRUCTURAL_GAP": StructuralGapStrategy,
    }
    
    # 官方对外展示列表
    _OFFICIAL_LIST = ["MTR_MASTER", "STRATEGY_3K", "STRATEGY_STRUCTURAL_GAP"]
    
    @classmethod
    def get_strategy(cls, name: str) -> BaseStrategy:
        """
        工厂方法：根据名称实例化策略。支持别名自动映射。
        """
        name_upper = name.upper()
        
        # 兼容性映射
        if "MTR" in name_upper:
            return MTRStrategy()
        if "3K" in name_upper:
            return ThreeKStrategy()
        if "STRUCTURAL" in name_upper:
            return StructuralGapStrategy()
            
        # 默认回退
        strategy_class = cls._strategies.get(name_upper, MTRStrategy)
        return strategy_class()

    @classmethod
    def list_strategies(cls):
        """仅返回官方定义的策略，消除菜单污染"""
        return cls._OFFICIAL_LIST
