"""
Backtest Run Manager - Flask Backend
Orchestrates backtest execution without modifying existing scripts
"""

import os
import json
import subprocess
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import threading
import queue
import psutil

app = Flask(__name__)
CORS(app)

# Configuration
BASE_DIR = Path(__file__).parent.parent
RESULTS_DIR = BASE_DIR / "results"
BACKTEST_SCRIPTS_DIR = BASE_DIR
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
RESULTS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# In-memory run tracking (use Redis/DB for production)
active_runs = {}
run_history = []

# Execution queue
execution_queue = queue.Queue()


class BacktestRun:
    """Represents a single backtest execution"""
    
    def __init__(self, run_id: str, script_path: str, params: Dict):
        self.run_id = run_id
        self.script_path = script_path
        self.params = params
        self.status = "queued"  # queued, running, completed, failed
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.completed_at = None
        self.output_dir = RESULTS_DIR / f"run_{run_id}"
        self.process = None
        self.exit_code = None
        self.error_message = None
        self.validation_results = {}
        
    def to_dict(self):
        return {
            "run_id": self.run_id,
            "script_path": str(self.script_path),
            "params": self.params,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "output_dir": str(self.output_dir),
            "exit_code": self.exit_code,
            "error_message": self.error_message,
            "validation_results": self.validation_results,
            "duration_seconds": self._calculate_duration()
        }
    
    def _calculate_duration(self):
        if self.started_at and self.completed_at:
            start = datetime.fromisoformat(self.started_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds()
        return None


class FileValidator:
    """Validates backtest output files"""
    
    REQUIRED_FILES = {
        "trades.csv": {
            "required": True,
            "min_size": 100,  # bytes
            "required_columns": ["entry_time", "exit_time", "pnl"]
        },
        "summary.csv": {
            "required": True,
            "min_size": 50,
            "required_columns": ["metric", "value"]
        },
        "pnl.csv": {
            "required": True,
            "min_size": 100,
            "required_columns": ["date", "pnl", "cumulative_pnl"]
        },
        "metadata.json": {
            "required": False,
            "min_size": 20
        }
    }
    
    @staticmethod
    def validate_run(output_dir: Path) -> Dict:
        """
        Comprehensive validation of backtest output
        Returns: {
            "valid": bool,
            "errors": List[str],
            "warnings": List[str],
            "file_checks": Dict
        }
        """
        results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "file_checks": {}
        }
        
        if not output_dir.exists():
            results["valid"] = False
            results["errors"].append(f"Output directory not found: {output_dir}")
            return results
        
        # Check each required file
        for filename, rules in FileValidator.REQUIRED_FILES.items():
            file_path = output_dir / filename
            file_result = FileValidator._validate_file(file_path, rules)
            results["file_checks"][filename] = file_result
            
            if rules["required"] and not file_result["exists"]:
                results["valid"] = False
                results["errors"].append(f"Required file missing: {filename}")
            elif file_result["exists"] and file_result["errors"]:
                results["valid"] = False
                results["errors"].extend(file_result["errors"])
            elif file_result["exists"] and file_result["warnings"]:
                results["warnings"].extend(file_result["warnings"])
        
        return results
    
    @staticmethod
    def _validate_file(file_path: Path, rules: Dict) -> Dict:
        """Validate individual file"""
        result = {
            "exists": False,
            "size": 0,
            "errors": [],
            "warnings": [],
            "row_count": 0,
            "columns": []
        }
        
        if not file_path.exists():
            return result
        
        result["exists"] = True
        result["size"] = file_path.stat().st_size
        
        # Size check
        if result["size"] < rules.get("min_size", 0):
            result["errors"].append(f"File too small: {result['size']} bytes")
        
        # CSV-specific validation
        if file_path.suffix == ".csv" and "required_columns" in rules:
            try:
                df = pd.read_csv(file_path)
                result["row_count"] = len(df)
                result["columns"] = df.columns.tolist()
                
                # Check required columns
                missing_cols = set(rules["required_columns"]) - set(df.columns)
                if missing_cols:
                    result["errors"].append(f"Missing columns: {missing_cols}")
                
                # Check for empty dataframe
                if len(df) == 0:
                    result["warnings"].append("File is empty (0 rows)")
                
                # File-specific validations
                if file_path.name == "pnl.csv":
                    if "cumulative_pnl" in df.columns:
                        last_pnl = df["cumulative_pnl"].iloc[-1]
                        if pd.isna(last_pnl):
                            result["errors"].append("Last cumulative PnL is missing")
                
            except Exception as e:
                result["errors"].append(f"Failed to parse CSV: {str(e)}")
        
        # JSON-specific validation
        elif file_path.suffix == ".json":
            try:
                with open(file_path, 'r') as f:
                    json.load(f)
            except Exception as e:
                result["errors"].append(f"Invalid JSON: {str(e)}")
        
        return result


class BacktestExecutor:
    """Executes backtest scripts as subprocesses"""
    
    @staticmethod
    def execute(run: BacktestRun) -> bool:
        """
        Execute backtest script
        Returns: True if execution started successfully
        """
        try:
            run.status = "running"
            run.started_at = datetime.now().isoformat()
            run.output_dir.mkdir(exist_ok=True, parents=True)
            
            # Build command
            cmd = BacktestExecutor._build_command(run)
            
            # Prepare environment
            env = os.environ.copy()
            env["BACKTEST_RUN_ID"] = run.run_id
            env["BACKTEST_OUTPUT_DIR"] = str(run.output_dir)
            
            # Log file
            log_file = run.output_dir / "execution.log"
            
            # Execute
            with open(log_file, 'w') as log:
                run.process = subprocess.Popen(
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=BACKTEST_SCRIPTS_DIR,
                    env=env,
                    shell=True
                )
            
            return True
            
        except Exception as e:
            run.status = "failed"
            run.error_message = f"Execution failed: {str(e)}"
            run.completed_at = datetime.now().isoformat()
            return False
    
    @staticmethod
    def _build_command(run: BacktestRun) -> str:
        """Build command line for backtest execution"""
        cmd_parts = ["python", str(run.script_path)]
        
        # Add parameters
        for key, value in run.params.items():
            cmd_parts.append(f"--{key}")
            cmd_parts.append(str(value))
        
        # Add output directory
        cmd_parts.append("--output_dir")
        cmd_parts.append(str(run.output_dir))
        
        return " ".join(cmd_parts)
    
    @staticmethod
    def monitor(run: BacktestRun):
        """Monitor running process"""
        if run.process:
            run.exit_code = run.process.wait()
            run.completed_at = datetime.now().isoformat()
            
            if run.exit_code == 0:
                # Validate output
                validation = FileValidator.validate_run(run.output_dir)
                run.validation_results = validation
                
                if validation["valid"]:
                    run.status = "completed"
                else:
                    run.status = "failed"
                    run.error_message = f"Validation failed: {validation['errors']}"
            else:
                run.status = "failed"
                run.error_message = f"Process exited with code {run.exit_code}"


def execution_worker():
    """Background worker to process execution queue"""
    while True:
        try:
            run = execution_queue.get()
            if run is None:  # Shutdown signal
                break
            
            # Execute
            if BacktestExecutor.execute(run):
                # Monitor completion
                BacktestExecutor.monitor(run)
            
            # Update history
            run_history.append(run.to_dict())
            
            execution_queue.task_done()
            
        except Exception as e:
            print(f"Worker error: {e}")


# Start background worker
worker_thread = threading.Thread(target=execution_worker, daemon=True)
worker_thread.start()


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "active_runs": len([r for r in active_runs.values() if r.status == "running"]),
        "queued_runs": execution_queue.qsize()
    })


@app.route('/api/backtest/run', methods=['POST'])
def trigger_backtest():
    """
    Trigger a new backtest run
    
    Request body:
    {
        "script": "strategy_analyzer.py",
        "params": {
            "strategy": "short_straddle",
            "start_date": "2020-01-01",
            "end_date": "2020-12-31",
            "capital": 100000
        }
    }
    """
    try:
        data = request.json
        
        # Validate request
        if not data.get("script"):
            return jsonify({"error": "script parameter required"}), 400
        
        script_path = BACKTEST_SCRIPTS_DIR / data["script"]
        if not script_path.exists():
            return jsonify({"error": f"Script not found: {data['script']}"}), 404
        
        # Create run
        run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        run = BacktestRun(
            run_id=run_id,
            script_path=script_path,
            params=data.get("params", {})
        )
        
        # Store and queue
        active_runs[run_id] = run
        execution_queue.put(run)
        
        return jsonify({
            "success": True,
            "run_id": run_id,
            "status": run.status,
            "message": "Backtest queued for execution"
        }), 202
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/backtest/status/<run_id>', methods=['GET'])
def get_status(run_id: str):
    """Get status of a backtest run"""
    run = active_runs.get(run_id)
    
    if not run:
        # Check history
        for hist_run in run_history:
            if hist_run["run_id"] == run_id:
                return jsonify(hist_run)
        return jsonify({"error": "Run not found"}), 404
    
    return jsonify(run.to_dict())


@app.route('/api/backtest/logs/<run_id>', methods=['GET'])
def get_logs(run_id: str):
    """Get execution logs"""
    run = active_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    
    log_file = run.output_dir / "execution.log"
    if not log_file.exists():
        return jsonify({"error": "Log file not found"}), 404
    
    # Get last N lines
    lines = int(request.args.get('lines', 100))
    
    with open(log_file, 'r') as f:
        all_lines = f.readlines()
        last_lines = all_lines[-lines:]
    
    return jsonify({
        "run_id": run_id,
        "lines": last_lines,
        "total_lines": len(all_lines)
    })


@app.route('/api/backtest/validate/<run_id>', methods=['POST'])
def validate_results(run_id: str):
    """Manually trigger validation"""
    run = active_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    
    validation = FileValidator.validate_run(run.output_dir)
    run.validation_results = validation
    
    return jsonify(validation)


@app.route('/api/backtest/results/<run_id>', methods=['GET'])
def list_results(run_id: str):
    """List all result files for a run"""
    run = active_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    
    if not run.output_dir.exists():
        return jsonify({"error": "Output directory not found"}), 404
    
    files = []
    for file_path in run.output_dir.iterdir():
        if file_path.is_file():
            files.append({
                "name": file_path.name,
                "size": file_path.stat().st_size,
                "modified": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
            })
    
    return jsonify({
        "run_id": run_id,
        "output_dir": str(run.output_dir),
        "files": files
    })


@app.route('/api/backtest/results/<run_id>/<filename>', methods=['GET'])
def download_result(run_id: str, filename: str):
    """Download a specific result file"""
    run = active_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    
    file_path = run.output_dir / filename
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    
    # Security: prevent directory traversal
    if not file_path.resolve().is_relative_to(run.output_dir.resolve()):
        return jsonify({"error": "Invalid file path"}), 403
    
    return send_file(file_path, as_attachment=True)


@app.route('/api/backtest/list', methods=['GET'])
def list_runs():
    """List all backtest runs"""
    status_filter = request.args.get('status')
    limit = int(request.args.get('limit', 50))
    
    # Combine active and history
    all_runs = [r.to_dict() for r in active_runs.values()] + run_history
    
    # Filter by status
    if status_filter:
        all_runs = [r for r in all_runs if r["status"] == status_filter]
    
    # Sort by created_at descending
    all_runs.sort(key=lambda x: x["created_at"], reverse=True)
    
    return jsonify({
        "total": len(all_runs),
        "runs": all_runs[:limit]
    })


@app.route('/api/backtest/cancel/<run_id>', methods=['POST'])
def cancel_run(run_id: str):
    """Cancel a running backtest"""
    run = active_runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    
    if run.status not in ["queued", "running"]:
        return jsonify({"error": f"Cannot cancel run with status: {run.status}"}), 400
    
    # Kill process if running
    if run.process and run.process.poll() is None:
        try:
            parent = psutil.Process(run.process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
            run.status = "cancelled"
            run.completed_at = datetime.now().isoformat()
            return jsonify({"success": True, "message": "Run cancelled"})
        except Exception as e:
            return jsonify({"error": f"Failed to cancel: {str(e)}"}), 500
    
    return jsonify({"success": True, "message": "Run cancelled"})


if __name__ == '__main__':
    print("=" * 60)
    print("BACKTEST RUN MANAGER")
    print("=" * 60)
    print(f"Results directory: {RESULTS_DIR}")
    print(f"Scripts directory: {BACKTEST_SCRIPTS_DIR}")
    print("Starting Flask server on http://localhost:5001")
    print("=" * 60)
    
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
