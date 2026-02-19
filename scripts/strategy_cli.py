"""
Strategy Performance CLI
=======================
Command-line interface for strategy performance analysis
"""

import argparse
import pandas as pd
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy_analyzer import StrategyPerformanceAnalyzer

def main():
    parser = argparse.ArgumentParser(
        description='Strategy Performance Analysis Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze single strategy
  python strategy_cli.py --strategy v1_ce_fut --from-date 2019-01-01 --to-date 2019-12-31
  
  # Compare multiple strategies
  python strategy_cli.py --strategies v1_ce_fut v2_pe_fut v3_strike_breach --compare
  
  # Generate comprehensive report
  python strategy_cli.py --strategies v1_ce_fut v2_pe_fut --from-date 2019-01-01 --export-csv
  
  # Show available strategies
  python strategy_cli.py --list-strategies
        """
    )
    
    # Strategy selection
    parser.add_argument(
        '--strategy',
        help='Single strategy to analyze'
    )
    
    parser.add_argument(
        '--strategies',
        nargs='+',
        help='Multiple strategies to analyze/compare'
    )
    
    # Date filters
    parser.add_argument(
        '--from-date',
        help='Start date (YYYY-MM-DD)'
    )
    
    parser.add_argument(
        '--to-date',
        help='End date (YYYY-MM-DD)'
    )
    
    # Output options
    parser.add_argument(
        '--compare',
        action='store_true',
        help='Compare multiple strategies'
    )
    
    parser.add_argument(
        '--export-csv',
        action='store_true',
        help='Export results to CSV files'
    )
    
    parser.add_argument(
        '--export-excel',
        action='store_true',
        help='Export results to Excel file'
    )
    
    parser.add_argument(
        '--summary',
        action='store_true',
        help='Show summary statistics only'
    )
    
    parser.add_argument(
        '--list-strategies',
        action='store_true',
        help='List available strategies in database'
    )
    
    parser.add_argument(
        '--output-dir',
        default='strategy_reports',
        help='Output directory for reports (default: strategy_reports)'
    )
    
    args = parser.parse_args()
    
    # Initialize analyzer
    analyzer = StrategyPerformanceAnalyzer()
    
    try:
        if args.list_strategies:
            list_available_strategies(analyzer)
            return
        
        # Validate input
        if not args.strategy and not args.strategies:
            print("Error: Please specify --strategy or --strategies")
            parser.print_help()
            return
        
        strategies = [args.strategy] if args.strategy else args.strategies
        
        if args.compare or len(strategies) > 1:
            compare_strategies(analyzer, strategies, args.from_date, args.to_date, 
                             args.export_csv, args.export_excel, args.output_dir)
        else:
            analyze_single_strategy(analyzer, strategies[0], args.from_date, args.to_date,
                                  args.export_csv, args.export_excel, args.summary, args.output_dir)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        analyzer.disconnect()

def list_available_strategies(analyzer):
    """List all available strategies in the database"""
    print("Available Strategies:")
    print("=" * 30)
    
    # Common strategy names (you can expand this based on your actual strategies)
    common_strategies = [
        "v1_ce_fut", "v2_pe_fut", "v3_strike_breach", "v4_strangle",
        "v5_protected", "v6_inverse_strangle", "v7_premium",
        "v8_ce_pe_fut", "v8_hsl", "v9_counter"
    ]
    
    for i, strategy in enumerate(common_strategies, 1):
        print(f"{i}. {strategy}")
    
    print(f"\nTo analyze a strategy, use: --strategy {common_strategies[0]}")

def analyze_single_strategy(analyzer, strategy_name, from_date, to_date, 
                          export_csv, export_excel, summary_only, output_dir):
    """Analyze a single strategy"""
    print(f"Analyzing strategy: {strategy_name}")
    if from_date and to_date:
        print(f"Date range: {from_date} to {to_date}")
    print()
    
    # Get strategy results
    df = analyzer.get_strategy_results(strategy_name, from_date, to_date)
    
    if df.empty:
        print("No results found for the specified strategy and date range")
        return
    
    print(f"Found {len(df)} trades")
    
    # Calculate metrics
    metrics = analyzer.calculate_performance_metrics(df)
    
    if summary_only:
        # Show summary only
        print("\nPerformance Summary:")
        print("=" * 30)
        for key, value in metrics.items():
            print(f"{key}: {value}")
    else:
        # Show detailed results
        print("\nDetailed Results:")
        print("=" * 30)
        print(df.head(10).to_string())
        
        print(f"\nPerformance Metrics:")
        print("=" * 30)
        for key, value in metrics.items():
            print(f"{key}: {value}")
    
    # Export if requested
    if export_csv or export_excel:
        print(f"\nExporting results to {output_dir}...")
        reports = analyzer.export_strategy_summary(df, strategy_name, output_dir)
        
        if export_csv:
            print(f"CSV reports: {reports['trade_details']}")
            print(f"Metrics CSV: {reports['metrics']}")
            print(f"Chart data: {reports['chart_data']}")
        
        if export_excel:
            print(f"Excel report: {reports['excel_report']}")

def compare_strategies(analyzer, strategies, from_date, to_date, 
                      export_csv, export_excel, output_dir):
    """Compare multiple strategies"""
    print(f"Comparing strategies: {', '.join(strategies)}")
    if from_date and to_date:
        print(f"Date range: {from_date} to {to_date}")
    print()
    
    # Generate comprehensive report
    report_info = analyzer.generate_comprehensive_report(
        strategies, from_date, to_date, output_dir
    )
    
    if 'error' in report_info:
        print(f"Error: {report_info['error']}")
        return
    
    print(f"Analysis complete!")
    print(f"Strategies analyzed: {report_info['strategies_analyzed']}")
    print(f"Total trades: {report_info['total_trades']}")
    print(f"Reports saved to: {output_dir}")
    
    # Show comparison results
    if export_csv:
        print(f"Comparison CSV: {report_info['comparison_csv']}")
        print(f"Ranking CSV: {report_info['ranking_csv']}")
        
        # Display comparison data
        try:
            comparison_df = pd.read_csv(report_info['comparison_csv'])
            print("\nStrategy Comparison:")
            print("=" * 50)
            print(comparison_df.to_string(index=False))
        except Exception as e:
            print(f"Could not display comparison data: {e}")

if __name__ == "__main__":
    main()