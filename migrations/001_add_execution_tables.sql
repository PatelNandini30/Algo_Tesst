-- Migration: Add execution tracking tables
-- Version: 001
-- Description: Add tables for strategy registry, execution runs, results, and parameter cache

-- Strategy Registry Table
CREATE TABLE IF NOT EXISTS strategy_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    version TEXT NOT NULL,
    parameter_schema TEXT NOT NULL,  -- JSON schema
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strategy_name ON strategy_registry(name);

-- Execution Runs Table
CREATE TABLE IF NOT EXISTS execution_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    parameters_hash TEXT NOT NULL,  -- MD5 hash of parameters for caching
    parameters_json TEXT NOT NULL,  -- Full parameters as JSON
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'completed', 'failed')),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    user_id TEXT DEFAULT 'system',
    error_message TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(id)
);

CREATE INDEX IF NOT EXISTS idx_execution_strategy ON execution_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_execution_status ON execution_runs(status);
CREATE INDEX IF NOT EXISTS idx_execution_started ON execution_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_params_hash ON execution_runs(parameters_hash);

-- Execution Results Table
CREATE TABLE IF NOT EXISTS execution_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id INTEGER NOT NULL,
    result_data TEXT NOT NULL,  -- JSON with result data
    row_count INTEGER NOT NULL,
    metadata TEXT,  -- JSON with additional metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES execution_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_result_execution ON execution_results(execution_id);

-- Parameter Cache Table (for quick lookups of previously executed strategies)
CREATE TABLE IF NOT EXISTS parameter_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id INTEGER NOT NULL,
    parameters_hash TEXT NOT NULL,
    result_data TEXT NOT NULL,  -- Cached result as JSON
    row_count INTEGER NOT NULL,
    execution_time_ms INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    access_count INTEGER DEFAULT 1,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(id),
    UNIQUE(strategy_id, parameters_hash)
);

CREATE INDEX IF NOT EXISTS idx_cache_strategy_params ON parameter_cache(strategy_id, parameters_hash);
CREATE INDEX IF NOT EXISTS idx_cache_last_accessed ON parameter_cache(last_accessed DESC);

-- Metadata Table (for tracking database version and migrations)
CREATE TABLE IF NOT EXISTS db_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('schema_version', '001');
INSERT OR REPLACE INTO db_metadata (key, value) VALUES ('last_migration', datetime('now'));
