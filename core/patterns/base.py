import pandas as pd
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod

class BasePattern(ABC):
    """
    高胜率形态库基类 (High Win-Rate Pattern Base Class)
    
    所有的具体形态识别器都必须继承此类并实现 `detect` 方法。
    """
    
    def __init__(self, params: Dict[str, Any] = None):
        """
        初始化形态识别器。
        
        Args:
            params: 形态识别所需的超参数字典，允许不同形态拥有不同的配置。
        """
        self.params = params or {}
        
    @property
    @abstractmethod
    def pattern_name(self) -> str:
        """形态的全局唯一注册名称"""
        pass
        
    @abstractmethod
    def detect(self, df_daily: pd.DataFrame, df_weekly: pd.DataFrame = None) -> pd.Series:
        """
        执行形态识别逻辑。
        
        Args:
            df_daily: 包含该股票的日线 DataFrame (open, high, low, close, volume 等)。
            df_weekly: 包含该股票的周线 DataFrame (可选，根据形态需要)。
            
        Returns:
            pd.Series: 返回一个与 df_daily 索引对齐的布尔型 Series，True 表示满足该形态。
        """
        pass

class PatternRegistry:
    """形态注册表，用于动态管理和加载所有的形态"""
    _patterns: Dict[str, BasePattern] = {}

    @classmethod
    def register(cls, pattern: BasePattern):
        """注册一个形态实例"""
        cls._patterns[pattern.pattern_name] = pattern

    @classmethod
    def get_pattern(cls, name: str) -> Optional[BasePattern]:
        """通过名称获取形态实例"""
        return cls._patterns.get(name)
        
    @classmethod
    def get_all_patterns(cls) -> Dict[str, BasePattern]:
        """获取所有已注册的形态"""
        return cls._patterns
