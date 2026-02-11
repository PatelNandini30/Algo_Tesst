# Strategy Performance Analysis System

## Overview
A comprehensive system for analyzing trading strategy performance with P&L calculations, cumulative returns, and automated CSV report generation. Integrates with your existing database backend and provides both web and command-line interfaces.

## Features
- **P&L Analysis**: Detailed profit and loss calculations
- **Cumulative Returns**: Track portfolio performance over time
- **Risk Metrics**: Drawdown analysis, CAGR, CAR/MDD ratios
- **Multi-Strategy Comparison**: Compare performance across different strategies
- **Automated Reporting**: Export to CSV, Excel, and web formats
- **Web Dashboard**: Interactive visualization interface
- **Command-Line Interface**: Scriptable analysis tools

## Components

### 1. Strategy Analyzer (`strategy_analyzer.py`)
Core analysis engine that:
- Connects to your SQLite database
- Calculates performance metrics (P&L, CAGR, drawdown, etc.)
- Generates cumulative performance charts
- Exports comprehensive reports

### 2. Web Dashboard (`strategy_dashboard.py`)
Streamlit-based web interface featuring:
- Strategy selection and date filtering
- Interactive performance charts
- Comparison tools
- Export capabilities
- Real-time data visualization

### 3. Command-Line Interface (`strategy_cli.py`)
Scriptable analysis tool with commands:
- Single strategy analysis
- Multi-strategy comparison
- Batch report generation
- Automated exports

## Installation

### Prerequisites
```bash
pip install pandas numpy sqlite3 streamlit plotly openpyxl
```

### Quick Start
1. **Verify components work:**
   ```bash
   python verify_components.py
   ```

2. **Run web dashboard:**
   ```bash
   start_dashboard.bat
   ```
   Or manually:
   ```bash
   streamlit run strategy_dashboard.py
   ```

3. **Use command-line interface:**
   ```bash
   python strategy_cli.py --help
   ```

## Usage Examples

### Web Dashboard
```bash
# Start the dashboard
start_dashboard.bat

# Access in browser at http://localhost:8501
```

### Command-Line Analysis

**Single Strategy Analysis:**
```bash
python strategy_cli.py --strategy v1_ce_fut --from-date 2019-01-01 --to-date 2019-12-31 --export-csv
```

**Multi-Strategy Comparison:**
```bash
python strategy_cli.py --strategies v1_ce_fut v2_pe_fut v3_strike_breach --compare --export-excel
```

**Generate Summary Only:**
```bash
python strategy_cli.py --strategy v1_ce_fut --summary
```

### Python API Usage
```python
from strategy_analyzer import StrategyPerformanceAnalyzer

# Initialize analyzer
analyzer = StrategyPerformanceAnalyzer()

# Get strategy results
df = analyzer.get_strategy_results("v1_ce_fut", "2019-01-01", "2019-12-31")

# Calculate metrics
metrics = analyzer.calculate_performance_metrics(df)
print(metrics)

# Generate reports
reports = analyzer.export_strategy_summary(df, "v1_ce_fut")
```

## Generated Reports

### CSV Exports
- `strategy_trades.csv` - Detailed trade records
- `strategy_metrics.csv` - Performance metrics summary
- `chart_data.csv` - Cumulative performance data for visualization

### Excel Reports
- Multiple sheets: Trade Details, Performance Metrics, Cumulative Performance
- Formatted for easy analysis

### Web Dashboard Reports
- Interactive charts and graphs
- Real-time comparison tools
- Exportable visualizations

## Key Performance Metrics

The system calculates these essential metrics:

- **Total P&L**: Absolute profit/loss
- **CAGR**: Compound Annual Growth Rate
- **Win Rate**: Percentage of profitable trades
- **Average Win/Loss**: Mean profit/loss per trade
- **Expectancy**: Risk-adjusted return expectation
- **Max Drawdown**: Largest peak-to-trough decline
- **CAR/MDD**: CAGR to Max Drawdown ratio
- **Recovery Factor**: Total P&L to Max Drawdown ratio
- **ROI vs Spot**: Performance relative to underlying index

## Database Integration

The system integrates with your existing `bhavcopy_data.db` database:

- Reads execution results from strategy runs
- Accesses historical trade data
- Leverages existing database schema
- Maintains backward compatibility

## File Structure
```
strategy_analysis/
├── strategy_analyzer.py      # Core analysis engine
├── strategy_dashboard.py     # Web interface
├── strategy_cli.py          # Command-line interface
├── verify_components.py     # Component verification
├── start_dashboard.bat      # Dashboard launcher
├── test_integration.py      # Integration tests
└── strategy_reports/        # Generated reports (auto-created)
```

## Troubleshooting

### Common Issues

1. **Database Connection Errors**
   - Verify `bhavcopy_data.db` exists in project root
   - Check database schema matches expected structure

2. **Missing Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Web Interface Issues**
   - Ensure Streamlit is installed: `pip install streamlit`
   - Check firewall settings for port 8501

4. **Data Not Found**
   - Verify strategy names match database entries
   - Check date ranges overlap with available data

### Verification
Run the verification script to test all components:
```bash
python verify_components.py
```

## Customization

### Adding New Metrics
Modify the `calculate_performance_metrics()` method in `strategy_analyzer.py` to add custom calculations.

### Strategy Support
Extend the system by modifying the database query in `get_strategy_results()` to support new strategy types.

### Report Templates
Customize export formats by modifying the export methods in the analyzer class.

## Performance Considerations

- Large datasets may require additional memory
- Consider using database indexes for better query performance
- Web dashboard performance optimized for datasets up to 10,000 trades
- CLI interface more efficient for batch processing

## Next Steps

1. **Connect to your database**: Update connection settings if needed
2. **Run sample analysis**: Test with existing strategy data
3. **Customize metrics**: Add any specific calculations required
4. **Deploy dashboard**: Set up for team access
5. **Schedule reports**: Automate regular performance reporting

## Support
For issues or feature requests, check the component tests and verification scripts to diagnose problems.