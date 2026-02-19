"""
Strategy Performance Dashboard - Frontend Interface
=================================================
Web interface for strategy performance analysis and reporting
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, date
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_analyzer import StrategyPerformanceAnalyzer

# Page configuration
st.set_page_config(
    page_title="Strategy Performance Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
    .positive {
        color: #00cc96;
        font-weight: bold;
    }
    .negative {
        color: #ef553b;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

def main():
    st.markdown('<div class="main-header">📊 Strategy Performance Dashboard</div>', unsafe_allow_html=True)
    
    # Initialize analyzer
    @st.cache_resource
    def get_analyzer():
        return StrategyPerformanceAnalyzer()
    
    analyzer = get_analyzer()
    
    # Sidebar for configuration
    st.sidebar.header("Configuration")
    
    # Strategy selection
    strategies = ["v1_ce_fut", "v2_pe_fut", "v3_strike_breach", "v4_strangle", 
                  "v5_protected", "v6_inverse_strangle", "v7_premium", 
                  "v8_ce_pe_fut", "v8_hsl", "v9_counter"]
    
    selected_strategies = st.sidebar.multiselect(
        "Select Strategies",
        strategies,
        default=["v1_ce_fut"]
    )
    
    # Date range selection
    col1, col2 = st.sidebar.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date",
            value=date(2019, 1, 1),
            min_value=date(2000, 1, 1),
            max_value=date(2026, 12, 31)
        )
    with col2:
        end_date = st.date_input(
            "End Date",
            value=date(2019, 12, 31),
            min_value=start_date,
            max_value=date(2026, 12, 31)
        )
    
    # Analysis type selection
    analysis_type = st.sidebar.radio(
        "Analysis Type",
        ["Individual Strategy", "Strategy Comparison", "Performance Charts"]
    )
    
    # Action buttons
    st.sidebar.markdown("---")
    if st.sidebar.button("📊 Generate Report", type="primary"):
        generate_report(analyzer, selected_strategies, start_date, end_date, analysis_type)
    
    if st.sidebar.button("📥 Export All Data"):
        export_all_data(analyzer, selected_strategies, start_date, end_date)

def generate_report(analyzer, strategies, start_date, end_date, analysis_type):
    """Generate and display the selected report"""
    
    if not strategies:
        st.warning("Please select at least one strategy")
        return
    
    # Convert dates to string format
    from_date = start_date.strftime("%Y-%m-%d")
    to_date = end_date.strftime("%Y-%m-%d")
    
    try:
        if analysis_type == "Individual Strategy":
            generate_individual_report(analyzer, strategies[0], from_date, to_date)
        elif analysis_type == "Strategy Comparison":
            generate_comparison_report(analyzer, strategies, from_date, to_date)
        elif analysis_type == "Performance Charts":
            generate_charts_report(analyzer, strategies, from_date, to_date)
            
    except Exception as e:
        st.error(f"Error generating report: {str(e)}")
        st.exception(e)

def generate_individual_report(analyzer, strategy, from_date, to_date):
    """Generate individual strategy report"""
    st.subheader(f"📈 Individual Strategy Analysis: {strategy}")
    
    with st.spinner("Loading strategy data..."):
        df = analyzer.get_strategy_results(strategy, from_date, to_date)
    
    if df.empty:
        st.warning(f"No data found for strategy '{strategy}' in the selected date range")
        return
    
    # Calculate metrics
    metrics = analyzer.calculate_performance_metrics(df)
    
    # Display key metrics in cards
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Total P&L", 
            f"₹{metrics.get('Total P&L', 0):,.2f}",
            delta=f"{metrics.get('CAGR (Options)', 0)}% CAGR"
        )
    
    with col2:
        st.metric(
            "Win Rate", 
            f"{metrics.get('Win Rate (%)', 0)}%",
            delta=f"{metrics.get('Total Trades', 0)} trades"
        )
    
    with col3:
        st.metric(
            "Max Drawdown", 
            f"{metrics.get('Max Drawdown (%)', 0)}%",
            delta=None
        )
    
    with col4:
        st.metric(
            "CAR/MDD", 
            f"{metrics.get('CAR/MDD', 0)}",
            delta=None
        )
    
    # Detailed metrics table
    st.subheader("📊 Performance Metrics")
    metrics_df = pd.DataFrame([metrics])
    st.dataframe(metrics_df, use_container_width=True)
    
    # Trade details
    st.subheader("📋 Trade Details")
    st.dataframe(df.head(50), use_container_width=True)
    
    # Export options
    st.subheader("📥 Export Options")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Export to CSV"):
            reports = analyzer.export_strategy_summary(df, strategy)
            st.success(f"Reports exported successfully!")
            for report_type, path in reports.items():
                st.code(path)
    
    with col2:
        # Display sample data
        if st.button("Show Sample Data"):
            st.write("First 5 trades:")
            st.dataframe(df.head(), use_container_width=True)

def generate_comparison_report(analyzer, strategies, from_date, to_date):
    """Generate strategy comparison report"""
    st.subheader("🆚 Strategy Comparison")
    
    all_metrics = []
    
    with st.spinner("Analyzing strategies..."):
        for strategy in strategies:
            df = analyzer.get_strategy_results(strategy, from_date, to_date)
            if not df.empty:
                metrics = analyzer.calculate_performance_metrics(df)
                metrics['Strategy'] = strategy
                all_metrics.append(metrics)
    
    if not all_metrics:
        st.warning("No data found for selected strategies")
        return
    
    # Create comparison DataFrame
    comparison_df = pd.DataFrame(all_metrics)
    
    # Display comparison table
    st.dataframe(comparison_df, use_container_width=True)
    
    # Create visualizations
    col1, col2 = st.columns(2)
    
    with col1:
        # CAGR comparison
        fig_cagr = px.bar(
            comparison_df,
            x='Strategy',
            y='CAGR (Options)',
            title='CAGR Comparison',
            color='CAGR (Options)',
            color_continuous_scale='viridis'
        )
        st.plotly_chart(fig_cagr, use_container_width=True)
    
    with col2:
        # Win rate comparison
        fig_winrate = px.bar(
            comparison_df,
            x='Strategy',
            y='Win Rate (%)',
            title='Win Rate Comparison',
            color='Win Rate (%)',
            color_continuous_scale='blues'
        )
        st.plotly_chart(fig_winrate, use_container_width=True)
    
    # Drawdown comparison
    fig_dd = px.bar(
        comparison_df,
        x='Strategy',
        y='Max Drawdown (%)',
        title='Maximum Drawdown Comparison',
        color='Max Drawdown (%)',
        color_continuous_scale='reds'
    )
    st.plotly_chart(fig_dd, use_container_width=True)
    
    # Export comparison
    if st.button("Export Comparison Report"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"strategy_comparison_{timestamp}.csv"
        comparison_df.to_csv(filename, index=False)
        st.success(f"Comparison report exported to {filename}")

def generate_charts_report(analyzer, strategies, from_date, to_date):
    """Generate performance charts for selected strategies"""
    st.subheader("📊 Performance Charts")
    
    all_chart_data = []
    
    with st.spinner("Generating charts..."):
        for strategy in strategies:
            df = analyzer.get_strategy_results(strategy, from_date, to_date)
            if not df.empty:
                chart_data = analyzer.generate_cumulative_chart_data(df)
                chart_data['Strategy'] = strategy
                all_chart_data.append(chart_data)
    
    if not all_chart_data:
        st.warning("No chart data available")
        return
    
    # Combine all chart data
    combined_chart_data = pd.concat(all_chart_data, ignore_index=True)
    
    # Cumulative P&L chart
    st.subheader("💰 Cumulative P&L Over Time")
    fig_pnl = px.line(
        combined_chart_data,
        x='Date',
        y='Cumulative P&L',
        color='Strategy',
        title='Cumulative P&L by Strategy'
    )
    st.plotly_chart(fig_pnl, use_container_width=True)
    
    # Portfolio value chart
    st.subheader("💼 Portfolio Value Over Time")
    fig_portfolio = px.line(
        combined_chart_data,
        x='Date',
        y='Portfolio Value',
        color='Strategy',
        title='Portfolio Value by Strategy'
    )
    st.plotly_chart(fig_portfolio, use_container_width=True)
    
    # Drawdown chart
    st.subheader("📉 Drawdown Analysis")
    fig_dd = px.area(
        combined_chart_data,
        x='Date',
        y='Drawdown %',
        color='Strategy',
        title='Drawdown Percentage by Strategy'
    )
    st.plotly_chart(fig_dd, use_container_width=True)
    
    # Monthly performance heatmap
    st.subheader("📅 Monthly Performance")
    generate_monthly_heatmap(combined_chart_data)

def generate_monthly_heatmap(chart_data):
    """Generate monthly performance heatmap"""
    # Group by month and strategy
    chart_data['Month'] = pd.to_datetime(chart_data['Date']).dt.to_period('M')
    monthly_data = chart_data.groupby(['Strategy', 'Month'])['Cumulative P&L'].last().reset_index()
    
    # Calculate monthly returns
    monthly_returns = []
    for strategy in monthly_data['Strategy'].unique():
        strategy_data = monthly_data[monthly_data['Strategy'] == strategy].copy()
        strategy_data = strategy_data.sort_values('Month')
        strategy_data['Monthly_Return'] = strategy_data['Cumulative P&L'].pct_change() * 100
        monthly_returns.append(strategy_data)
    
    if monthly_returns:
        combined_returns = pd.concat(monthly_returns, ignore_index=True)
        
        # Pivot for heatmap
        pivot_data = combined_returns.pivot(
            index='Strategy',
            columns='Month',
            values='Monthly_Return'
        )
        
        # Create heatmap
        fig_heatmap = px.imshow(
            pivot_data,
            title='Monthly Returns Heatmap (%)',
            color_continuous_scale='RdYlGn',
            aspect='auto'
        )
        st.plotly_chart(fig_heatmap, use_container_width=True)

def export_all_data(analyzer, strategies, start_date, end_date):
    """Export all data for selected strategies"""
    st.subheader("📥 Bulk Data Export")
    
    from_date = start_date.strftime("%Y-%m-%d")
    to_date = end_date.strftime("%Y-%m-%d")
    
    with st.spinner("Exporting data..."):
        try:
            report_info = analyzer.generate_comprehensive_report(
                strategies, from_date, to_date
            )
            
            st.success("✅ Data export completed!")
            st.write(f"Strategies analyzed: {report_info.get('strategies_analyzed', 0)}")
            st.write(f"Total trades: {report_info.get('total_trades', 0)}")
            st.write("Generated files:")
            
            for key, path in report_info.items():
                if key not in ['strategies_analyzed', 'total_trades', 'error']:
                    st.code(path)
                    
        except Exception as e:
            st.error(f"Export failed: {str(e)}")

if __name__ == "__main__":
    main()