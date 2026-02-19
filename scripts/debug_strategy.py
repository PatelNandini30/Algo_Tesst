import os
import sys
import pandas as pd
from datetime import datetime

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))


def show_data_sources_overview():
    """Show overview of all data sources"""
    print("Data Sources Directory Structure:")
    print("├── cleaned_csvs/     ← Bhavcopy data files (2000-2020)")
    print("├── strikeData/       ← Index and stock spot data")
    print("├── expiryData/       ← Expiry dates for options")
    print("└── Filter/           ← Base2 filter periods")
    
    # Count files in each directory
    cleaned_csvs_count = len([f for f in os.listdir("cleaned_csvs") if f.endswith(".csv")])
    strike_data_count = len([f for f in os.listdir("strikeData") if f.endswith(".csv")])
    expiry_data_count = len([f for f in os.listdir("expiryData") if f.endswith(".csv")])
    filter_count = len([f for f in os.listdir("Filter") if f.endswith(".csv")])
    
    print(f"\nFile Counts:")
    print(f"  cleaned_csvs: {cleaned_csvs_count} files")
    print(f"  strikeData: {strike_data_count} files")
    print(f"  expiryData: {expiry_data_count} files")
    print(f"  Filter: {filter_count} files")


def show_file_structure_detailed():
    """Show detailed file structure with sample data"""
    
    # Show sample from cleaned_csvs
    print("1. CLEANED_CSVS (Bhavcopy Data Sample):")
    sample_file = "cleaned_csvs/2020-12-31.csv"
    if os.path.exists(sample_file):
        df = pd.read_csv(sample_file, nrows=3)
        print(f"   File: {sample_file}")
        print(f"   Columns: {list(df.columns)}")
        print(f"   Sample rows:\n{df.to_string(index=False)}")
    else:
        print("   No sample file found")
    print()
    
    # Show sample from strikeData
    print("2. STRIKE DATA (Index Spot Data Sample):")
    sample_file = "strikeData/Nifty_strike_data.csv"
    if os.path.exists(sample_file):
        df = pd.read_csv(sample_file, nrows=3)
        print(f"   File: {sample_file}")
        print(f"   Columns: {list(df.columns)}")
        print(f"   Sample rows:\n{df.to_string(index=False)}")
    else:
        print("   No sample file found")
    print()
    
    # Show sample from expiryData
    print("3. EXPIRY DATA (Options Expiry Sample):")
    sample_file = "expiryData/NIFTY.csv"
    if os.path.exists(sample_file):
        df = pd.read_csv(sample_file, nrows=3)
        print(f"   File: {sample_file}")
        print(f"   Columns: {list(df.columns)}")
        print(f"   Sample rows:\n{df.to_string(index=False)}")
    else:
        print("   No sample file found")
    print()
    
    # Show sample from Filter
    print("4. FILTER DATA (Base2 Periods Sample):")
    sample_file = "Filter/base2.csv"
    if os.path.exists(sample_file):
        df = pd.read_csv(sample_file)
        print(f"   File: {sample_file}")
        print(f"   Columns: {list(df.columns)}")
        print(f"   Sample rows:\n{df.head(3).to_string(index=False)}")
    else:
        print("   No sample file found")


def show_csv_data_availability(from_date, to_date):
    """Show which CSV files are available for the given date range"""
    from_date_obj = datetime.strptime(from_date, "%Y-%m-%d")
    to_date_obj = datetime.strptime(to_date, "%Y-%m-%d")
    
    available_files = []
    csv_dir = "cleaned_csvs"
    
    if os.path.exists(csv_dir):
        for filename in os.listdir(csv_dir):
            if filename.endswith(".csv"):
                try:
                    file_date_str = filename.replace(".csv", "")
                    file_date = datetime.strptime(file_date_str, "%Y-%m-%d")
                    if from_date_obj <= file_date <= to_date_obj:
                        available_files.append(filename)
                except ValueError:
                    continue
    
    available_files.sort()
    return available_files


def debug_v1_strategy():
    """Debug why V1 strategy isn't generating trades"""
    
    print("=== V1 Strategy Debug - Comprehensive Data View ===\n")
    
    # Show data sources overview
    print("=== DATA SOURCES OVERVIEW ===")
    show_data_sources_overview()
    print()
    
    # Show detailed file structure
    print("=== DETAILED FILE STRUCTURE ===")
    show_file_structure_detailed()
    print()
    
    try:
        from backend.engines.v1_ce_fut import run_v1
        from backend.base import get_strike_data, load_expiry, load_base2
        
        # Test with a smaller date range that should have data
        params = {
            "strategy_version": "v1",
            "expiry_window": "weekly_expiry",
            "spot_adjustment_type": 0,
            "spot_adjustment": 1.0,
            "call_sell_position": 0.0,
            "put_sell_position": 0.0,
            "put_strike_pct_below": 1.0,
            "protection": False,
            "protection_pct": 1.0,
            "call_premium": True,
            "put_premium": True,
            "premium_multiplier": 1.0,
            "call_sell": True,
            "put_sell": True,
            "call_hsl_pct": 100,
            "put_hsl_pct": 100,
            "max_put_spot_pct": 0.04,
            "pct_diff": 0.3,
            "from_date": "2019-01-01",  # Use available data range
            "to_date": "2019-12-31",    # 2019 data
            "index": "NIFTY"
        }
        
        print("Testing V1 strategy with parameters:")
        print(f"  Date range: {params['from_date']} to {params['to_date']}")
        print(f"  Index: {params['index']}")
        print()
        
        # Load data to check availability - Enhanced with detailed info
        print("=== DATA LOADING DETAILS ===")
        
        try:
            print("1. Loading SPOT DATA (from strikeData directory):")
            spot_data = get_strike_data("NIFTY", params["from_date"], params["to_date"])
            print(f"   ✓ Spot data loaded: {len(spot_data)} rows")
            print(f"   ✓ Columns: {list(spot_data.columns)}")
            print(f"   ✓ Date range: {spot_data['Date'].min()} to {spot_data['Date'].max()}")
            print(f"   ✓ Sample prices: {spot_data['Close'].head(5).tolist()}")
            print(f"   ✓ Data types:\n{spot_data.dtypes}")
            print()
        except Exception as e:
            print(f"   ✗ Error loading spot data: {e}")
            return
            
        try:
            print("2. Loading EXPIRY DATA (from expiryData directory):")
            weekly_expiry = load_expiry("NIFTY", "weekly")
            print(f"   ✓ Weekly expiry data loaded: {len(weekly_expiry)} rows")
            if len(weekly_expiry) > 0:
                print(f"   ✓ Columns: {list(weekly_expiry.columns)}")
                print(f"   ✓ Sample dates: {weekly_expiry.head(3).to_string(index=False)}")
            print()
        except Exception as e:
            print(f"   ✗ Error loading expiry data: {e}")
            return
            
        try:
            print("3. Loading FILTER DATA (from Filter directory):")
            base2 = load_base2()
            print(f"   ✓ Base2 filter data loaded: {len(base2)} rows")
            if len(base2) > 0:
                print(f"   ✓ Columns: {list(base2.columns)}")
                print(f"   ✓ Sample periods: {base2.head(3).to_string(index=False)}")
            print()
        except Exception as e:
            print(f"   ✗ Error loading base2 data: {e}")
            return
            
        try:
            print("4. Loading CLEANED CSV DATA (from cleaned_csvs directory):")
            # Show what CSV files are available for the date range
            csv_files = show_csv_data_availability(params["from_date"], params["to_date"])
            print(f"   ✓ Available CSV files for date range: {len(csv_files)} files")
            if csv_files:
                print(f"   ✓ Sample files: {csv_files[:5]}" + ("..." if len(csv_files) > 5 else ""))
            print()
        except Exception as e:
            print(f"   ✗ Error checking CSV data: {e}")
            return
        
        print("\n=== Running V1 Strategy ===")
        try:
            trades_df, meta, analytics = run_v1(params)
            print(f" Strategy executed successfully")
            print(f"   Trades generated: {len(trades_df)}")
            print(f"   Meta info: {meta}")
            if len(trades_df) > 0:
                print(f"   Sample trades:")
                print(trades_df.head())
        except Exception as e:
            print(f" Strategy execution failed: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f" Import error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_v1_strategy()