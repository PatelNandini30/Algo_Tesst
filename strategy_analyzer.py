"""
Strategy Performance Analyzer
============================
Generates comprehensive performance reports with P&L, cumulative returns,
and exports results by strategy to CSV files.

Integrates with database backend and provides frontend-ready summaries.
"""

import pandas as pd
import numpy as np
import sqlite3
import os
from datetime import datetime
from typing import Dict, List, Optional
import json

class StrategyPerformanceAnalyzer:
    """Analyzes strategy performance and generates comprehensive reports"""
    
    def __init__(self, db_path: str = "bhavcopy_data.db"):
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        """Connect to database"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
    
    def disconnect(self):
        """Disconnect from database"""
        if self.conn:
            self.conn.close()
    
    def get_strategy_results(self, strategy_name: str, from_date: str = None, to_date: str = None) -> pd.DataFrame:
        """
        Get strategy results from database
        
        Args:
            strategy_name: Name of strategy to analyze
            from_date: Start date filter (YYYY-MM-DD)
            to_date: End date filter (YYYY-MM-DD)
            
        Returns:
            DataFrame with strategy results
        """
        if not self.conn:
            self.connect()
        
        # Query execution results for the strategy
        query = """
        SELECT er.result_data, er.created_at, er.row_count
        FROM execution_results er
        JOIN execution_runs erun ON er.execution_id = erun.id
        JOIN strategy_registry sr ON erun.strategy_id = sr.id
        WHERE sr.name = ?
        """
        
        params = [strategy_name]
        
        if from_date:
            query += " AND erun.started_at >= ?"
            params.append(from_date)
        
        if to_date:
            query += " AND erun.started_at <= ?"
            params.append(to_date)
        
        query += " ORDER BY erun.started_at DESC"
        
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        results = cursor.fetchall()
        
        # Parse JSON results
        all_data = []
        for row in results:
            try:
                result_data = json.loads(row['result_data'])
                if isinstance(result_data, list) and len(result_data) > 0:
                    df = pd.DataFrame(result_data)
                    all_data.append(df)
            except Exception as e:
                print(f"Error parsing result data: {e}")
                continue
        
        if all_data:
            return pd.concat(all_data, ignore_index=True)
        else:
            return pd.DataFrame()
    
    def calculate_performance_metrics(self, df: pd.DataFrame) -> Dict:
        """
        Calculate comprehensive performance metrics
        
        Args:
            df: DataFrame with strategy results containing P&L data
            
        Returns:
            Dictionary with performance metrics
        """
        if df.empty:
            return {}
        
        # Ensure required columns exist
        required_cols = ['Net P&L', 'Entry Spot', 'Exit Spot', 'Entry Date', 'Exit Date']
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Required column '{col}' not found in data")
        
        # Convert date columns
        df['Entry Date'] = pd.to_datetime(df['Entry Date'])
        df['Exit Date'] = pd.to_datetime(df['Exit Date'])
        
        # Basic metrics
        total_pnl = df['Net P&L'].sum()
        count = len(df)
        
        # Wins and losses
        wins = df[df['Net P&L'] > 0]
        losses = df[df['Net P&L'] < 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        win_pct = round((win_count / count) * 100, 2) if count > 0 else 0
        
        # Average win/loss
        avg_win = round(wins['Net P&L'].mean(), 2) if win_count > 0 else 0
        avg_loss = round(losses['Net P&L'].mean(), 2) if loss_count > 0 else 0
        
        # Expectancy
        expectancy = 0
        if avg_loss != 0:
            expectancy = round(((avg_win/abs(avg_loss)) * win_pct - (100-win_pct)) / 100, 2)
        
        # CAGR calculation
        initial_capital = df.iloc[0]['Entry Spot']
        final_capital = df.iloc[-1]['Exit Spot'] + df['Net P&L'].sum()
        start_date = df['Entry Date'].min()
        end_date = df['Exit Date'].max()
        n_years = (end_date - start_date).days / 365.25
        
        cagr_options = 0
        if n_years > 0 and initial_capital > 0:
            cagr_options = round(100 * (((final_capital / initial_capital) ** (1 / n_years)) - 1), 2)
        
        # Drawdown calculations
        df_sorted = df.sort_values('Exit Date').copy()
        df_sorted['Cumulative'] = initial_capital + df_sorted['Net P&L'].cumsum()
        df_sorted['Peak'] = df_sorted['Cumulative'].cummax()
        df_sorted['DD'] = df_sorted['Cumulative'] - df_sorted['Peak']
        df_sorted['%DD'] = np.where(df_sorted['DD'] == 0, 0, 
                                   round(100 * (df_sorted['DD'] / df_sorted['Peak']), 2))
        
        max_dd_pct = df_sorted['%DD'].min()
        max_dd_pts = df_sorted['DD'].min()
        
        # Spot performance
        total_spot_change = (df['Exit Spot'] - df['Entry Spot']).sum()
        spot_return_pct = round((total_spot_change / (initial_capital * count)) * 100, 2)
        
        # Risk-adjusted metrics
        car_mdd = round(cagr_options / abs(max_dd_pct), 2) if max_dd_pct != 0 else 0
        recovery_factor = round(total_pnl / abs(max_dd_pts), 2) if max_dd_pts != 0 else 0
        
        metrics = {
            "Strategy": "Custom Analysis",
            "Period": f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            "Total Trades": count,
            "Winning Trades": win_count,
            "Losing Trades": loss_count,
            "Win Rate (%)": win_pct,
            "Total P&L": round(total_pnl, 2),
            "Average Win": avg_win,
            "Average Loss": avg_loss,
            "Expectancy": expectancy,
            "CAGR (Options)": cagr_options,
            "Max Drawdown (%)": max_dd_pct,
            "Max Drawdown (Points)": max_dd_pts,
            "CAR/MDD": car_mdd,
            "Recovery Factor": recovery_factor,
            "Spot Return (%)": spot_return_pct,
            "ROI vs Spot": round((total_pnl / abs(total_spot_change)) * 100, 2) if total_spot_change != 0 else 0
        }
        
        return metrics
    
    def generate_cumulative_chart_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate cumulative P&L chart data for visualization
        
        Args:
            df: DataFrame with strategy results
            
        Returns:
            DataFrame with cumulative performance data
        """
        if df.empty:
            return pd.DataFrame()
        
        # Sort by exit date
        df_sorted = df.sort_values('Exit Date').copy()
        
        # Calculate cumulative metrics
        initial_capital = df_sorted.iloc[0]['Entry Spot']
        df_sorted['Cumulative_P&L'] = df_sorted['Net P&L'].cumsum()
        df_sorted['Portfolio_Value'] = initial_capital + df_sorted['Cumulative_P&L']
        df_sorted['Spot_Value'] = df_sorted['Exit Spot'].cumsum() - df_sorted.iloc[0]['Entry Spot'] + initial_capital
        
        # Calculate drawdowns
        df_sorted['Peak'] = df_sorted['Portfolio_Value'].cummax()
        df_sorted['Drawdown_Points'] = df_sorted['Portfolio_Value'] - df_sorted['Peak']
        df_sorted['Drawdown_Pct'] = np.where(df_sorted['Drawdown_Points'] == 0, 0,
                                           (df_sorted['Drawdown_Points'] / df_sorted['Peak']) * 100)
        
        # Select relevant columns for charting
        chart_data = df_sorted[[
            'Exit Date', 'Portfolio_Value', 'Spot_Value', 'Cumulative_P&L',
            'Drawdown_Points', 'Drawdown_Pct'
        ]].copy()
        
        chart_data.columns = ['Date', 'Portfolio Value', 'Spot Value', 'Cumulative P&L',
                             'Drawdown Points', 'Drawdown %']
        
        return chart_data
    
    def export_strategy_summary(self, df: pd.DataFrame, strategy_name: str, 
                              output_dir: str = "strategy_reports") -> str:
        """
        Export comprehensive strategy summary to CSV and Excel
        
        Args:
            df: DataFrame with strategy results
            strategy_name: Name of the strategy
            output_dir: Directory to save reports
            
        Returns:
            Path to generated report files
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"{strategy_name}_summary_{timestamp}"
        
        # Calculate performance metrics
        metrics = self.calculate_performance_metrics(df)
        
        # Generate cumulative chart data
        chart_data = self.generate_cumulative_chart_data(df)
        
        # Create summary DataFrames
        metrics_df = pd.DataFrame([metrics])
        
        # Export to CSV
        csv_path = os.path.join(output_dir, f"{base_filename}.csv")
        df.to_csv(csv_path, index=False)
        
        # Export metrics to separate CSV
        metrics_csv_path = os.path.join(output_dir, f"{base_filename}_metrics.csv")
        metrics_df.to_csv(metrics_csv_path, index=False)
        
        # Export chart data
        chart_csv_path = os.path.join(output_dir, f"{base_filename}_chart_data.csv")
        chart_data.to_csv(chart_csv_path, index=False)
        
        # Export to Excel with multiple sheets
        excel_path = os.path.join(output_dir, f"{base_filename}.xlsx")
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Trade Details', index=False)
            metrics_df.to_excel(writer, sheet_name='Performance Metrics', index=False)
            chart_data.to_excel(writer, sheet_name='Cumulative Performance', index=False)
        
        return {
            "trade_details": csv_path,
            "metrics": metrics_csv_path,
            "chart_data": chart_csv_path,
            "excel_report": excel_path
        }
    
    def generate_comprehensive_report(self, strategies: List[str], 
                                    from_date: str = None, to_date: str = None,
                                    output_dir: str = "comprehensive_reports") -> Dict:
        """
        Generate comprehensive report comparing multiple strategies
        
        Args:
            strategies: List of strategy names to analyze
            from_date: Start date filter
            to_date: End date filter
            output_dir: Directory to save reports
            
        Returns:
            Dictionary with report paths and summary
        """
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        all_metrics = []
        all_trades = []
        
        for strategy in strategies:
            print(f"Analyzing strategy: {strategy}")
            df = self.get_strategy_results(strategy, from_date, to_date)
            
            if not df.empty:
                metrics = self.calculate_performance_metrics(df)
                metrics['Strategy'] = strategy
                all_metrics.append(metrics)
                
                # Add strategy identifier to trades
                df['Strategy'] = strategy
                all_trades.append(df)
                
                # Generate individual report
                individual_reports = self.export_strategy_summary(df, strategy, output_dir)
                print(f"Individual reports saved for {strategy}")
            else:
                print(f"No results found for strategy: {strategy}")
        
        # Create comparison report
        if all_metrics:
            comparison_df = pd.DataFrame(all_metrics)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            comparison_csv = os.path.join(output_dir, f"strategy_comparison_{timestamp}.csv")
            comparison_df.to_csv(comparison_csv, index=False)
            
            # Sort by CAGR for ranking
            comparison_df_sorted = comparison_df.sort_values('CAGR (Options)', ascending=False)
            ranking_csv = os.path.join(output_dir, f"strategy_ranking_{timestamp}.csv")
            comparison_df_sorted.to_csv(ranking_csv, index=False)
            
            return {
                "comparison_csv": comparison_csv,
                "ranking_csv": ranking_csv,
                "strategies_analyzed": len(all_metrics),
                "total_trades": sum(len(df) for df in all_trades) if all_trades else 0
            }
        
        return {"error": "No valid strategy data found"}

# Example usage
if __name__ == "__main__":
    # Initialize analyzer
    analyzer = StrategyPerformanceAnalyzer()
    
    # Example: Analyze specific strategy
    try:
        # Get strategy results
        df = analyzer.get_strategy_results("v1_ce_fut")
        
        if not df.empty:
            print("Strategy Analysis Results:")
            print("=" * 50)
            
            # Calculate metrics
            metrics = analyzer.calculate_performance_metrics(df)
            for key, value in metrics.items():
                print(f"{key}: {value}")
            
            # Export report
            reports = analyzer.export_strategy_summary(df, "v1_ce_fut")
            print(f"\nReports generated:")
            for report_type, path in reports.items():
                print(f"  {report_type}: {path}")
        else:
            print("No results found for the strategy")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        analyzer.disconnect()