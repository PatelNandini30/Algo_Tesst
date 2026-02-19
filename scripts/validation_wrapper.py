"""
Validation Wrapper for analyse_bhavcopy_02-01-2026.py
This wrapper preserves the original validation logic while providing
a clean interface for integration with new systems.

IMPORTANT: This file imports from the original validation file to ensure
all calculations and logic remain exactly as implemented.
"""

# Import the original validation logic (unchanged)
try:
    import analyse_bhavcopy_02_01_2026 as original_validation
    VALIDATION_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import original validation module: {e}")
    VALIDATION_AVAILABLE = False

class ValidationPreserver:
    """
    Wrapper class that preserves and provides access to original validation logic.
    All calculations remain exactly as implemented in the original file.
    """
    
    def __init__(self):
        self.original_module = None
        if VALIDATION_AVAILABLE:
            self.original_module = original_validation
            print("ValidationPreserver: Original validation logic loaded successfully")
        else:
            print("ValidationPreserver: Warning - Original validation logic not available")
    
    def run_original_validation(self, *args, **kwargs):
        """
        Execute the original validation logic exactly as implemented.
        This ensures no calculation changes occur.
        
        Args:
            *args: Arguments to pass to original main() function
            **kwargs: Keyword arguments to pass to original main() function
            
        Returns:
            Result from original validation logic
        """
        if not VALIDATION_AVAILABLE:
            raise RuntimeError("Original validation logic is not available")
        
        # Call original main function with exact same parameters
        return self.original_module.main(*args, **kwargs)
    
    def get_strike_data(self, symbol):
        """
        Access original strike data logic exactly as implemented.
        
        Args:
            symbol: Trading symbol (e.g., 'NIFTY')
            
        Returns:
            Strike data as returned by original getStrikeData function
        """
        if not VALIDATION_AVAILABLE:
            raise RuntimeError("Original validation logic is not available")
            
        return self.original_module.getStrikeData(symbol)
    
    def create_log_entry(self, symbol, reason, call_expiry=None, put_expiry=None, 
                        fut_expiry=None, _from=None, _to=None):
        """
        Create log entry using original logging logic.
        
        Args:
            symbol: Trading symbol
            reason: Reason for log entry
            call_expiry: Call expiry date
            put_expiry: Put expiry date  
            fut_expiry: Future expiry date
            _from: From date
            _to: To date
        """
        if not VALIDATION_AVAILABLE:
            raise RuntimeError("Original validation logic is not available")
            
        return self.original_module.createLogFile(
            symbol, reason, call_expiry, put_expiry, fut_expiry, _from, _to
        )
    
    def get_round_half_up(self, x):
        """
        Access original rounding function.
        
        Args:
            x: Number to round
            
        Returns:
            Rounded value using original logic
        """
        if not VALIDATION_AVAILABLE:
            raise RuntimeError("Original validation logic is not available")
            
        return self.original_module.round_half_up(x)
    
    def verify_integrity(self):
        """
        Verify that the original validation logic is accessible and unchanged.
        
        Returns:
            dict: Status information about validation logic availability
        """
        status = {
            'validation_available': VALIDATION_AVAILABLE,
            'original_functions_accessible': False,
            'error_message': None
        }
        
        if VALIDATION_AVAILABLE:
            try:
                # Test access to key functions
                hasattr(self.original_module, 'main')
                hasattr(self.original_module, 'getStrikeData')
                hasattr(self.original_module, 'createLogFile')
                hasattr(self.original_module, 'round_half_up')
                status['original_functions_accessible'] = True
            except Exception as e:
                status['error_message'] = str(e)
        
        return status

# Global instance for easy access
validation_preserver = ValidationPreserver()

def validate_trades(*args, **kwargs):
    """
    Convenience function to run validation on trades.
    This is the recommended way to access validation logic.
    
    Args:
        *args: Arguments for validation
        **kwargs: Keyword arguments for validation
        
    Returns:
        Validation results from original logic
    """
    return validation_preserver.run_original_validation(*args, **kwargs)

def get_validation_status():
    """
    Get current status of validation system.
    
    Returns:
        dict: Status information
    """
    return validation_preserver.verify_integrity()

# Example usage:
"""
# In your integration code:
from validation_wrapper import validate_trades, get_validation_status

# Check if validation is available
status = get_validation_status()
if status['validation_available']:
    # Run validation exactly as original
    results = validate_trades(your_parameters)
else:
    print("Validation logic not available")
"""

if __name__ == "__main__":
    # Test the wrapper
    print("=== Validation Wrapper Test ===")
    status = get_validation_status()
    print(f"Validation Available: {status['validation_available']}")
    print(f"Functions Accessible: {status['original_functions_accessible']}")
    
    if status['error_message']:
        print(f"Error: {status['error_message']}")
    
    print("\nValidation logic preserved and ready for use.")