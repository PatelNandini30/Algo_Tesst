"""Generic Strategy Engine Abstraction Layer

This module provides a unified interface for both legacy engines (v1-v9) and 
dynamic multi-leg strategies, maintaining backward compatibility while enabling
flexible strategy composition.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import pandas as pd
from abc import ABC, abstractmethod

# Import existing engines for backward compatibility
from .engines.v1_ce_fut import run_v1_main1, run_v1_main2, run_v1_main3, run_v1_main4, run_v1_main5
from .engines.v2_pe_fut import run_v2_main1, run_v2_main2, run_v2_main3, run_v2_main4, run_v2_main5
from .engines.v3_strike_breach import run_v3_main1, run_v3_main2, run_v3_main3, run_v3_main4, run_v3_main5
from .engines.v4_strangle import run_v4_main1, run_v4_main2, run_v4_main3, run_v4_main4, run_v4_main5
from .engines.v5_protected import run_v5_call_main1, run_v5_call_main2, run_v5_put_main1, run_v5_put_main2
from .engines.v6_inverse_strangle import run_v6_main1, run_v6_main2, run_v6_main3, run_v6_main4
from .engines.v7_premium import run_v7_main1, run_v7_main2, run_v7_main3, run_v7_main4
from .engines.v8_ce_pe_fut import run_v8_main1, run_v8_main2, run_v8_main3, run_v8_main4
from .engines.v8_hsl import run_v8_hsl_main1, run_v8_hsl_main2, run_v8_hsl_main3, run_v8_hsl_main4, run_v8_hsl_main5
from .engines.v9_counter import run_v9_main1, run_v9_main2, run_v9_main3, run_v9_main4


class InstrumentType(Enum):
    OPTION = "OPTION"
    FUTURE = "FUTURE"


class OptionType(Enum):
    CE = "CE"
    PE = "PE"


class PositionType(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExpiryType(Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class StrikeSelectionType(Enum):
    ATM = "ATM"
    ITM = "ITM"
    OTM = "OTM"
    SPOT = "SPOT"


@dataclass
class StrikeSelection:
    """Defines how to select strikes for a leg"""
    type: StrikeSelectionType
    value: float  # percentage or points depending on type
    spot_adjustment_mode: int = 0  # 0-4 as per requirements
    spot_adjustment: float = 0.0


@dataclass
class Leg:
    """Represents a single leg in a strategy"""
    instrument: InstrumentType
    option_type: Optional[OptionType]  # Required for OPTION instruments
    position: PositionType
    strike_selection: StrikeSelection
    quantity: int = 1
    expiry_type: ExpiryType = ExpiryType.WEEKLY
    
    def __post_init__(self):
        if self.instrument == InstrumentType.OPTION and self.option_type is None:
            raise ValueError("option_type is required for OPTION instrument")


@dataclass
class StrategyDefinition:
    """Defines a complete strategy with multiple legs"""
    name: str
    legs: List[Leg]
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.legs:
            raise ValueError("Strategy must have at least one leg")


class StrategyExecutor(ABC):
    """Abstract base class for strategy execution"""
    
    @abstractmethod
    def execute(self, strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        """Execute the strategy and return (trades_df, summary, pivot)"""
        pass


class LegacyEngineExecutor(StrategyExecutor):
    """Executor that routes to existing v1-v9 engines for backward compatibility"""
    
    def __init__(self):
        # Mapping of legacy engine names to their execution functions
        self.legacy_engines = {
            # V1 engines
            "v1_ce_fut": run_v1_main1,
            "v1_ce_fut_t1": run_v1_main2,
            "v1_ce_fut_t2": run_v1_main3,
            "v1_ce_fut_monthly": run_v1_main4,
            "v1_ce_fut_monthly_t1": run_v1_main5,
            
            # V2 engines
            "v2_pe_fut": run_v2_main1,
            "v2_pe_fut_t1": run_v2_main2,
            "v2_pe_fut_t2": run_v2_main3,
            "v2_pe_fut_monthly": run_v2_main4,
            "v2_pe_fut_monthly_t1": run_v2_main5,
            
            # V3 engines
            "v3_strike_breach": run_v3_main1,
            "v3_strike_breach_t1": run_v3_main2,
            "v3_strike_breach_t2": run_v3_main3,
            "v3_strike_breach_monthly": run_v3_main4,
            "v3_strike_breach_monthly_t1": run_v3_main5,
            
            # V4 engines
            "v4_strangle": run_v4_main1,
            "v4_strangle_t1": run_v4_main2,
            "v4_strangle_t2": run_v4_main3,
            "v4_strangle_monthly": run_v4_main4,
            "v4_strangle_monthly_t1": run_v4_main5,
            
            # V5 engines
            "v5_call": run_v5_call_main1,
            "v5_call_t1": run_v5_call_main2,
            "v5_put": run_v5_put_main1,
            "v5_put_t1": run_v5_put_main2,
            
            # V6 engines
            "v6_inverse_strangle": run_v6_main1,
            "v6_inverse_strangle_t1": run_v6_main2,
            "v6_inverse_strangle_t2": run_v6_main3,
            "v6_inverse_strangle_monthly": run_v6_main4,
            
            # V7 engines
            "v7_premium": run_v7_main1,
            "v7_premium_t1": run_v7_main2,
            "v7_premium_t2": run_v7_main3,
            "v7_premium_monthly": run_v7_main4,
            
            # V8 engines
            "v8_ce_pe_fut": run_v8_main1,
            "v8_ce_pe_fut_t1": run_v8_main2,
            "v8_ce_pe_fut_t2": run_v8_main3,
            "v8_ce_pe_fut_monthly": run_v8_main4,
            
            "v8_hsl": run_v8_hsl_main1,
            "v8_hsl_t1": run_v8_hsl_main2,
            "v8_hsl_t2": run_v8_hsl_main3,
            "v8_hsl_monthly": run_v8_hsl_main4,
            "v8_hsl_monthly_t1": run_v8_hsl_main5,
            
            # V9 engines
            "v9_counter": run_v9_main1,
            "v9_counter_t1": run_v9_main2,
            "v9_counter_t2": run_v9_main3,
            "v9_counter_monthly": run_v9_main4,
        }
    
    def execute(self, strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        """
        Execute using legacy engine if the strategy matches known patterns,
        otherwise raise exception.
        """
        engine_name = params.get('strategy', '')
        
        if engine_name in self.legacy_engines:
            engine_func = self.legacy_engines[engine_name]
            return engine_func(params)
        else:
            raise ValueError(f"Unknown legacy engine: {engine_name}")


class DynamicStrategyExecutor(StrategyExecutor):
    """Executor for dynamic multi-leg strategies using generic engine"""
    
    def __init__(self):
        # Import the generic multi-leg engine
        from .engines.generic_multi_leg import run_generic_multi_leg
        self.generic_engine = run_generic_multi_leg
    
    def execute(self, strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        """
        Execute dynamic strategy using the generic multi-leg engine
        """
        # Convert strategy definition to format expected by generic engine
        engine_params = {
            'strategy_definition': strategy_def,
            **params
        }
        return self.generic_engine(engine_params)


class StrategyRouter:
    """Main router that determines whether to use legacy or dynamic execution"""
    
    def __init__(self):
        self.legacy_executor = LegacyEngineExecutor()
        self.dynamic_executor = DynamicStrategyExecutor()
    
    def is_legacy_pattern(self, strategy_def: StrategyDefinition, params: Dict[str, Any]) -> bool:
        """
        Determine if this strategy should use legacy engine for backward compatibility
        """
        # If strategy is specified by name (like v1_ce_fut), use legacy
        engine_name = params.get('strategy', '')
        if engine_name and engine_name in self.legacy_executor.legacy_engines:
            return True
        
        # If it's a simple CE Sell + Future Buy pattern (classic v1), use legacy
        if (len(strategy_def.legs) == 2 and
            any(leg.instrument == InstrumentType.OPTION and 
                leg.option_type == OptionType.CE and 
                leg.position == PositionType.SELL for leg in strategy_def.legs) and
            any(leg.instrument == InstrumentType.FUTURE and 
                leg.position == PositionType.BUY for leg in strategy_def.legs)):
            return True
            
        return False
    
    def execute(self, strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
        """
        Execute strategy using appropriate engine
        """
        if self.is_legacy_pattern(strategy_def, params):
            return self.legacy_executor.execute(strategy_def, params)
        else:
            return self.dynamic_executor.execute(strategy_def, params)


# Global instance of strategy router
strategy_router = StrategyRouter()


def validate_strategy_definition(strategy_def: StrategyDefinition) -> bool:
    """
    Validate strategy definition before execution
    
    Args:
        strategy_def: StrategyDefinition to validate
        
    Returns:
        True if valid, False otherwise
    """
    # Check that strategy has at least one leg
    if not strategy_def.legs or len(strategy_def.legs) == 0:
        raise ValueError("Strategy must have at least one leg")
    
    # Validate each leg
    for i, leg in enumerate(strategy_def.legs):
        # Validate instrument type
        try:
            InstrumentType(leg.instrument.value)
        except ValueError:
            raise ValueError(f"Invalid instrument type for leg {i+1}: {leg.instrument}")
        
        # Validate option type if instrument is OPTION
        if leg.instrument == InstrumentType.OPTION:
            if leg.option_type is None:
                raise ValueError(f"option_type is required for OPTION instrument in leg {i+1}")
            try:
                OptionType(leg.option_type.value)
            except ValueError:
                raise ValueError(f"Invalid option type for leg {i+1}: {leg.option_type}")
        
        # Validate position type
        try:
            PositionType(leg.position.value)
        except ValueError:
            raise ValueError(f"Invalid position type for leg {i+1}: {leg.position}")
        
        # Validate strike selection
        if leg.instrument == InstrumentType.OPTION:
            try:
                StrikeSelectionType(leg.strike_selection.type.value)
            except ValueError:
                raise ValueError(f"Invalid strike selection type for leg {i+1}: {leg.strike_selection.type}")
        
        # Validate quantity
        if leg.quantity <= 0:
            raise ValueError(f"Quantity must be positive for leg {i+1}")
        
        # Validate expiry type
        try:
            ExpiryType(leg.expiry_type.value)
        except ValueError:
            raise ValueError(f"Invalid expiry type for leg {i+1}: {leg.expiry_type}")
    
    return True


def validate_data_availability(index: str, from_date: str, to_date: str) -> bool:
    """
    Validate that required data is available for the strategy
    
    Args:
        index: Index name
        from_date: Start date
        to_date: End date
        
    Returns:
        True if data is available, False otherwise
    """
    from .base import get_strike_data, load_expiry
    import pandas as pd
    
    try:
        # Check if strike data is available
        spot_df = get_strike_data(index, from_date, to_date)
        if spot_df.empty:
            raise ValueError(f"No strike data available for {index} between {from_date} and {to_date}")
        
        # Check if expiry data is available
        weekly_exp = load_expiry(index, "weekly")
        monthly_exp = load_expiry(index, "monthly")
        
        if weekly_exp.empty and monthly_exp.empty:
            raise ValueError(f"No expiry data available for {index}")
        
        return True
    except Exception as e:
        raise ValueError(f"Data validation failed: {str(e)}")


def execute_strategy(strategy_def: StrategyDefinition, params: Dict[str, Any]) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Public function to execute a strategy using the appropriate engine
    """
    # Perform validation
    validate_strategy_definition(strategy_def)
    
    # Validate data availability
    index = params.get('index', 'NIFTY')
    from_date = params.get('from_date', '')
    to_date = params.get('to_date', '')
    validate_data_availability(index, from_date, to_date)
    
    return strategy_router.execute(strategy_def, params)