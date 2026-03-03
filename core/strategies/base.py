from abc import ABC, abstractmethod
from typing import Dict

class BaseStrategy(ABC):
    """
    Abstract Base Class for Al Brooks Strategies.
    All strategies must implement these methods to be usable by Hunter/Guardian.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy Name (e.g., 'MTR_V1')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Brief description for UI/Logs"""
        pass

    @abstractmethod
    def format_prompt(self, context_data: Dict) -> str:
        """
        Generate the specific prompt for this strategy.
        Args:
            context_data: Dict containing 'code', 'df', 'ctx' (common data)
        Returns:
            str: The full prompt text
        """
        pass

    @abstractmethod
    def parse_result(self, response_text: str) -> Dict:
        """
        Parse the specific XML tags returned by this strategy.
        Returns:
            Dict: Parsed result (verdict, reason, etc.)
        """
        pass
