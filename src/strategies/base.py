"""
Base Strategy Interface for NSE Options Strategy Execution
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import pandas as pd


@dataclass
class StrategyParameter:
    """Definition of a strategy parameter"""
    name: str
    type: str  # 'int', 'float', 'str', 'bool', 'date', 'select'
    required: bool = True
    default: Any = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    options: Optional[List[Any]] = None  # For select type
    description: str = ""


@dataclass
class StrategyResult:
    """Standardized strategy execution result"""
    data: pd.DataFrame
    metadata: Dict[str, Any]
    execution_time_ms: float
    row_count: int
    success: bool
    error_message: Optional[str] = None


class StrategyInterface(ABC):
    """Abstract base class for all trading strategies"""
    
    @abstractmethod
    def get_name(self) -> str:
        """Return strategy name"""
        pass
    
    @abstractmethod
    def get_description(self) -> str:
        """Return strategy description"""
        pass
    
    @abstractmethod
    def get_version(self) -> str:
        """Return strategy version"""
        pass
    
    @abstractmethod
    def get_parameter_schema(self) -> List[StrategyParameter]:
        """Return list of parameters this strategy accepts"""
        pass
    
    def validate_parameters(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate parameters against schema
        Returns: (is_valid, error_message)
        """
        schema = {p.name: p for p in self.get_parameter_schema()}
        
        # Check required parameters
        for param_name, param_def in schema.items():
            if param_def.required and param_name not in params:
                return False, f"Required parameter '{param_name}' is missing"
        
        # Validate parameter values
        for param_name, param_value in params.items():
            if param_name not in schema:
                return False, f"Unknown parameter '{param_name}'"
            
            param_def = schema[param_name]
            
            # Type validation
            if param_def.type == 'int' and not isinstance(param_value, int):
                return False, f"Parameter '{param_name}' must be an integer"
            elif param_def.type == 'float' and not isinstance(param_value, (int, float)):
                return False, f"Parameter '{param_name}' must be a number"
            elif param_def.type == 'bool' and not isinstance(param_value, bool):
                return False, f"Parameter '{param_name}' must be a boolean"
            elif param_def.type == 'str' and not isinstance(param_value, str):
                return False, f"Parameter '{param_name}' must be a string"
            
            # Range validation
            if param_def.min_value is not None and param_value < param_def.min_value:
                return False, f"Parameter '{param_name}' must be >= {param_def.min_value}"
            if param_def.max_value is not None and param_value > param_def.max_value:
                return False, f"Parameter '{param_name}' must be <= {param_def.max_value}"
            
            # Options validation
            if param_def.options and param_value not in param_def.options:
                return False, f"Parameter '{param_name}' must be one of {param_def.options}"
        
        return True, None
    
    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> StrategyResult:
        """
        Execute the strategy with given parameters
        Returns: StrategyResult with data and metadata
        """
        pass
