# Backtest Run Manager - Complete Guide

## Overview

This system wraps your existing backtest scripts without modifying them. It provides:
- REST API for triggering backtests
- Real-time status monitoring
- Automated file validation
- Result file management

## Architecture Principles

### 1. **Non-Invasive Design**
- Your backtest scripts remain completely untouched
- File outputs are the single source of truth
- Backend only orchestrates and validates

### 2. **Execution Flow**

```
┌─────────────────────────────────────────────────────────────┐
│ 1. API Request                                               │
│    POST /api/backtest/run                                    │
│    {                                                         │
│      "script": "strategy_analyzer.py",                       │
│      "params": {"strategy": "short_straddle", ...}           │
│    }                                                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Run Creation                                              │
│    • Generate unique run_id                                  │
│    • Create output directory: results/run_{id}/              │
│    • Set status: "queued"                                    │
│    • Add to execution queue                                  │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Background Worker Picks Up                                │
│    • Dequeue run                                             │
│    • Set status: "running"                                   │
│    • Build command with parameters                           │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Execute Backtest Script                                   │
│    python strategy_analyzer.py \                             │
│      --strategy short_straddle \                             │
│      --start_date 2020-01-01 \                               │
│      --end_date 2020-12-31 \                                 │
│      --output_dir results/run_{id}/                          │
│                                                              │
│    Environment variables:                                    │
│    • BACKTEST_RUN_ID={id}                                    │
│    • BACKTEST_OUTPUT_DIR=results/run_{id}/                   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Monitor Process                                           │
│    • Capture stdout/stderr to execution.log                  │
│    • Wait for process completion                             │
│    • Record exit code                                        │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Validate Output Files                                     │
│    ✓ Check required files exist                              │
│    ✓ Verify file sizes                                       │
│    ✓ Parse CSV/JSON structure                                │
│    ✓ Validate required columns                               │
│    ✓ Check data integrity                                    │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 7. Set Final Status                                          │
│    • "completed" if validation passes                        │
│    • "failed" if validation fails or exit_code != 0          │
│    • Store validation results                                │
└─────────────────────────────────────────────────────────────┘
```

## File Validation Checklist

### Critical Validations (MUST PASS)

#### 1. **trades.csv**
```
✓ File exists
✓ Size > 100 bytes
✓ Valid CSV format
✓ Required columns present:
  - entry_time
  - exit_time
  - pnl
  - strike (optional but recommended)
  - option_type (optional)
✓ At least 1 row of data
✓ No null values in critical columns
```

#### 2. **summary.csv**
```
✓ File exists
✓ Size > 50 bytes
✓ Valid CSV format
✓ Required columns:
  - metric
  - value
✓ Contains key metrics:
  - total_pnl
  - win_rate
  - max_drawdown
  - sharpe_ratio (if available)
```

#### 3. **pnl.csv**
```
✓ File exists
✓ Size > 100 bytes
✓ Valid CSV format
✓ Required columns:
  - date
  - pnl
  - cumulative_pnl
✓ Last row has valid cumulative_pnl (not NaN)
✓ Dates are in chronological order
✓ No gaps in date sequence (trading days only)
```

### Optional Validations (WARNINGS)

#### 4. **metadata.json**
```
✓ File exists (optional)
✓ Valid JSON format
✓ Contains run parameters
✓ Contains execution timestamp
```

#### 5. **execution.log**
```
✓ File exists
✓ Contains script output
✓ No critical errors logged
```

### Custom Validation Rules

You can extend validation by adding custom rules:

```python
# In FileValidator class
CUSTOM_RULES = {
    "trades.csv": {
        "max_loss_per_trade": -10000,  # Alert if any trade loses more
        "min_trades": 10,  # Minimum trades expected
    },
    "pnl.csv": {
        "max_drawdown_pct": -30,  # Alert if drawdown > 30%
    }
}
```

## API Reference

### 1. Trigger Backtest

```bash
POST /api/backtest/run
Content-Type: application/json

{
  "script": "strategy_analyzer.py",
  "params": {
    "strategy": "short_straddle",
    "start_date": "2020-01-01",
    "end_date": "2020-12-31",
    "capital": 100000,
    "strike_selection": "atm",
    "entry_time": "09:20",
    "exit_time": "15:15"
  }
}

Response (202 Accepted):
{
  "success": true,
  "run_id": "20260210_143022_a3f8b2c1",
  "status": "queued",
  "message": "Backtest queued for execution"
}
```

### 2. Check Status

```bash
GET /api/backtest/status/{run_id}

Response:
{
  "run_id": "20260210_143022_a3f8b2c1",
  "script_path": "strategy_analyzer.py",
  "params": {...},
  "status": "running",  # queued, running, completed, failed
  "created_at": "2026-02-10T14:30:22",
  "started_at": "2026-02-10T14:30:25",
  "completed_at": null,
  "output_dir": "results/run_20260210_143022_a3f8b2c1",
  "exit_code": null,
  "error_message": null,
  "validation_results": {},
  "duration_seconds": null
}
```

### 3. Get Execution Logs

```bash
GET /api/backtest/logs/{run_id}?lines=100

Response:
{
  "run_id": "20260210_143022_a3f8b2c1",
  "lines": [
    "Starting backtest...",
    "Loading data from 2020-01-01 to 2020-12-31",
    "Processing 252 trading days",
    "..."
  ],
  "total_lines": 1543
}
```

### 4. Validate Results

```bash
POST /api/backtest/validate/{run_id}

Response:
{
  "valid": true,
  "errors": [],
  "warnings": ["pnl.csv: Only 5 trades found"],
  "file_checks": {
    "trades.csv": {
      "exists": true,
      "size": 15234,
      "errors": [],
      "warnings": [],
      "row_count": 45,
      "columns": ["entry_time", "exit_time", "pnl", "strike"]
    },
    "summary.csv": {...},
    "pnl.csv": {...}
  }
}
```

### 5. List Result Files

```bash
GET /api/backtest/results/{run_id}

Response:
{
  "run_id": "20260210_143022_a3f8b2c1",
  "output_dir": "results/run_20260210_143022_a3f8b2c1",
  "files": [
    {
      "name": "trades.csv",
      "size": 15234,
      "modified": "2026-02-10T14:35:22"
    },
    {
      "name": "summary.csv",
      "size": 523,
      "modified": "2026-02-10T14:35:22"
    },
    {
      "name": "pnl.csv",
      "size": 8932,
      "modified": "2026-02-10T14:35:22"
    }
  ]
}
```

### 6. Download Result File

```bash
GET /api/backtest/results/{run_id}/trades.csv

Response: File download
```

### 7. List All Runs

```bash
GET /api/backtest/list?status=completed&limit=50

Response:
{
  "total": 127,
  "runs": [
    {
      "run_id": "20260210_143022_a3f8b2c1",
      "status": "completed",
      "created_at": "2026-02-10T14:30:22",
      ...
    },
    ...
  ]
}
```

### 8. Cancel Running Backtest

```bash
POST /api/backtest/cancel/{run_id}

Response:
{
  "success": true,
  "message": "Run cancelled"
}
```

## Adapting Your Existing Scripts

### Option 1: No Changes Required (Recommended)

If your scripts already accept `--output_dir` parameter:

```python
# Your existing script
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--strategy', required=True)
parser.add_argument('--start_date', required=True)
parser.add_argument('--end_date', required=True)
parser.add_argument('--output_dir', default='results')  # ← Already there!

args = parser.parse_args()

# Save results
trades_df.to_csv(f"{args.output_dir}/trades.csv", index=False)
summary_df.to_csv(f"{args.output_dir}/summary.csv", index=False)
pnl_df.to_csv(f"{args.output_dir}/pnl.csv", index=False)
```

**No changes needed!** The manager will pass `--output_dir` automatically.

### Option 2: Use Environment Variable

If you prefer environment variables:

```python
import os

# Get output directory from environment
output_dir = os.getenv('BACKTEST_OUTPUT_DIR', 'results')
run_id = os.getenv('BACKTEST_RUN_ID', 'default')

# Save results
trades_df.to_csv(f"{output_dir}/trades.csv", index=False)
```

### Option 3: Wrapper Script (If Scripts Can't Be Modified)

Create a thin wrapper:

```python
# wrapper_strategy_analyzer.py
import sys
import subprocess
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--output_dir', required=True)
# ... other args

args = parser.parse_args()

# Call original script
result = subprocess.run([
    'python', 'strategy_analyzer.py',
    '--strategy', args.strategy,
    # ... pass other args
], capture_output=True)

# Move output files to specified directory
import shutil
shutil.move('results/trades.csv', f'{args.output_dir}/trades.csv')
# ... move other files

sys.exit(result.returncode)
```

## Windows-Specific Best Practices

### 1. **Path Handling**
```python
# Use pathlib for cross-platform compatibility
from pathlib import Path

output_dir = Path(args.output_dir)
trades_file = output_dir / "trades.csv"
```

### 2. **Large Dataset Handling**
```python
# For large CSVs, use chunking
chunk_size = 10000
for chunk in pd.read_csv('large_file.csv', chunksize=chunk_size):
    process_chunk(chunk)
```

### 3. **Memory Management**
```python
# Clear memory after processing
import gc

del large_dataframe
gc.collect()
```

### 4. **Process Priority**
```python
# Set lower priority for background backtests
import psutil
p = psutil.Process()
p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # Windows
```

### 5. **File Locking**
```python
# Ensure files are closed before validation
with open(output_file, 'w') as f:
    f.write(data)
# File automatically closed here
```

## Production Deployment

### 1. **Use Production WSGI Server**

```bash
pip install gunicorn  # Linux
pip install waitress  # Windows

# Run with waitress (Windows)
waitress-serve --host=0.0.0.0 --port=5001 backend.backtest_manager:app
```

### 2. **Add Database for Run Tracking**

Replace in-memory storage with SQLite/PostgreSQL:

```python
# models.py
from sqlalchemy import create_engine, Column, String, DateTime, JSON
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class BacktestRun(Base):
    __tablename__ = 'backtest_runs'
    
    run_id = Column(String, primary_key=True)
    status = Column(String)
    params = Column(JSON)
    created_at = Column(DateTime)
    # ... other fields
```

### 3. **Add Redis for Queue Management**

```python
from rq import Queue
from redis import Redis

redis_conn = Redis()
queue = Queue(connection=redis_conn)

# Enqueue job
job = queue.enqueue(execute_backtest, run)
```

### 4. **Add Authentication**

```python
from flask_httpauth import HTTPTokenAuth

auth = HTTPTokenAuth(scheme='Bearer')

@auth.verify_token
def verify_token(token):
    # Verify JWT token
    return validate_jwt(token)

@app.route('/api/backtest/run', methods=['POST'])
@auth.login_required
def trigger_backtest():
    # ... protected endpoint
```

### 5. **Add Rate Limiting**

```python
from flask_limiter import Limiter

limiter = Limiter(app, key_func=lambda: request.remote_addr)

@app.route('/api/backtest/run', methods=['POST'])
@limiter.limit("10 per hour")
def trigger_backtest():
    # ... rate limited
```

## Testing

### 1. **Test Script Execution**

```bash
# Test your script manually first
python strategy_analyzer.py \
  --strategy short_straddle \
  --start_date 2020-01-01 \
  --end_date 2020-01-31 \
  --output_dir test_output

# Verify files created
ls test_output/
```

### 2. **Test API**

```bash
# Start manager
python backend/backtest_manager.py

# Trigger backtest
curl -X POST http://localhost:5001/api/backtest/run \
  -H "Content-Type: application/json" \
  -d '{
    "script": "strategy_analyzer.py",
    "params": {
      "strategy": "short_straddle",
      "start_date": "2020-01-01",
      "end_date": "2020-01-31"
    }
  }'

# Check status
curl http://localhost:5001/api/backtest/status/{run_id}
```

### 3. **Test Validation**

```python
# test_validation.py
from backend.backtest_manager import FileValidator
from pathlib import Path

# Test with sample output
result = FileValidator.validate_run(Path('test_output'))
print(result)
```

## Monitoring & Debugging

### 1. **Check Active Runs**

```bash
GET /api/backtest/list?status=running
```

### 2. **View Logs**

```bash
GET /api/backtest/logs/{run_id}?lines=1000
```

### 3. **System Health**

```bash
GET /api/health

Response:
{
  "status": "healthy",
  "timestamp": "2026-02-10T14:30:22",
  "active_runs": 2,
  "queued_runs": 5
}
```

### 4. **Debug Failed Runs**

```python
# Check validation details
validation = FileValidator.validate_run(Path('results/run_xxx'))
print(validation['errors'])
print(validation['file_checks'])
```

## Integration with Frontend

### React Example

```javascript
// Trigger backtest
const runBacktest = async (params) => {
  const response = await fetch('http://localhost:5001/api/backtest/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      script: 'strategy_analyzer.py',
      params: params
    })
  });
  
  const data = await response.json();
  return data.run_id;
};

// Poll for status
const pollStatus = async (runId) => {
  const response = await fetch(`http://localhost:5001/api/backtest/status/${runId}`);
  const data = await response.json();
  
  if (data.status === 'completed') {
    // Fetch results
    const results = await fetch(`http://localhost:5001/api/backtest/results/${runId}`);
    return results.json();
  } else if (data.status === 'failed') {
    throw new Error(data.error_message);
  }
  
  // Still running, poll again
  setTimeout(() => pollStatus(runId), 2000);
};
```

## Troubleshooting

### Issue: Script not found
```
Error: Script not found: strategy_analyzer.py

Solution:
- Ensure script is in BACKTEST_SCRIPTS_DIR
- Use relative path from project root
- Check file permissions
```

### Issue: Validation fails
```
Error: Required file missing: trades.csv

Solution:
- Check script actually creates the file
- Verify output_dir parameter is used
- Check for script errors in execution.log
```

### Issue: Process hangs
```
Status stuck at "running"

Solution:
- Check execution.log for errors
- Verify script doesn't wait for user input
- Check for infinite loops in script
- Cancel and retry: POST /api/backtest/cancel/{run_id}
```

### Issue: Large memory usage
```
Solution:
- Process data in chunks
- Clear variables after use
- Use generators instead of lists
- Monitor with: psutil.Process().memory_info()
```

## Next Steps

1. **Start the manager**: `python backend/backtest_manager.py`
2. **Test with a simple backtest**: Use curl or Postman
3. **Integrate with your frontend**: Use the API endpoints
4. **Add custom validation rules**: Extend FileValidator
5. **Deploy to production**: Use waitress + database

## Support

For issues or questions:
1. Check execution logs: `GET /api/backtest/logs/{run_id}`
2. Validate output manually: `python -c "from backend.backtest_manager import FileValidator; print(FileValidator.validate_run('results/run_xxx'))"`
3. Test script independently: `python strategy_analyzer.py --help`
