# Production-Grade SaaS Platform Architecture
# NSE Options Strategy Execution & Backtesting System

**Version:** 2.0.0  
**Date:** February 2026  
**Document Type:** Complete Technical Architecture Specification  
**Target Audience:** Development Team, System Architects, DevOps Engineers

---

## IMPORTANT: Document Purpose

**This document describes a PROPOSED architecture for converting the existing NSE Bhavcopy analysis system into a multi-tenant SaaS platform.**

**The current implementation (as of February 2026) uses a different architecture:**
- Command-line Python scripts
- Direct SQLite3 queries (no ORM)
- Function-based strategies (not pluggable components)
- No API layer or web interface
- Single-user, local execution

**See Section 1.3 "Current Implementation Overview" for details on the existing system.**

---

## TABLE OF CONTENTS

1. [Executive Summary](#1-executive-summary)
   - 1.1 Project Vision
   - 1.2 Core Problem Statement
   - 1.3 Current Implementation Overview
2. [System Overview](#2-system-overview)
3. [Architectural Principles](#3-architectural-principles)
4. [Layer 1: Data Ingestion Service](#4-layer-1-data-ingestion-service)
5. [Layer 2: Database Layer](#5-layer-2-database-layer)
6. [Layer 3: Strategy Execution Engine](#6-layer-3-strategy-execution-engine)
7. [Layer 4: API Layer (FastAPI + Flask)](#7-layer-4-api-layer)
8. [Layer 5: Result Caching & Idempotency](#8-layer-5-result-caching--idempotency)
9. [Layer 6: Frontend UI (Next.js)](#9-layer-6-frontend-ui)
10. [Scalability & Growth Roadmap](#10-scalability--growth-roadmap)
11. [Reliability & Production Readiness](#11-reliability--production-readiness)
12. [Security & Access Control](#12-security--access-control)
13. [Implementation Roadmap](#13-implementation-roadmap)
14. [Success Criteria](#14-success-criteria)
15. [Technology Stack](#15-technology-stack)
16. [Deployment Architecture](#16-deployment-architecture)
17. [Monitoring & Observability](#17-monitoring--observability)
18. [Data Flow Diagrams](#18-data-flow-diagrams)
19. [API Specifications](#19-api-specifications)
20. [Database Schema Details](#20-database-schema-details)

---

## 1. EXECUTIVE SUMMARY

### 1.1 Project Vision

Build a production-grade SaaS platform for NSE options strategy execution and backtesting that:
- Automatically ingests 26GB+ of historical market data
- Executes 20+ KLOC strategy codebase via parameter-driven APIs
- Caches results for identical parameter runs (idempotency)
- Provides dynamic UI that adapts to new strategies without code changes
- Scales from MVP to enterprise without architectural rewrites

### 1.2 Core Problem Statement

**Current State:**
- 26GB SQLite database with 6362 CSV files (2000-2026)
- 40+ strategy variations implemented as Python functions
- Command-line execution only
- No multi-user support
- No web interface

**Desired State:**
- Multi-tenant SaaS platform
- REST API for strategy execution
- Web-based UI
- Result caching and idempotency
- Scalable architecture

### 1.3 Current Implementation Overview

The existing system (as implemented in the codebase) uses:

#### Database Layer
- **Technology**: SQLite3 with direct SQL queries (no ORM)
- **Main Table**: `cleaned_csvs` - 26GB of historical NSE data
- **Auxiliary Tables**: `expiry_data`, `strike_data`, `filter_data`, `ingestion_metadata`
- **Business Key**: (Date, Symbol, Instrument, ExpiryDate, StrikePrice, OptionType)

**Current Schema:**
```sql
CREATE TABLE cleaned_csvs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Date DATE NOT NULL,
    ExpiryDate DATE,
    Instrument TEXT,
    Symbol TEXT,
    StrikePrice REAL,
    OptionType TEXT,
    Open REAL,
    High REAL,
    Low REAL,
    Close REAL,
    SettledPrice REAL,
    Contracts INTEGER,
    TurnOver REAL,
    OpenInterest INTEGER
);
```

#### Data Ingestion
- **File**: `bhavcopy_db_builder.py`
- **Pattern**: Batch CSV ingestion with file-level idempotency
- **Deduplication**: Business key validation before insert
- **Performance**: Drops indices during bulk insert, rebuilds after
- **Tracking**: `ingestion_metadata` table tracks processed files by name + size

#### Strategy Execution
- **File**: `analyse_bhavcopy_02-01-2026.py` (20,186 lines)
- **Pattern**: Python functions with parameters (not classes/interfaces)
- **40+ Strategies**: main1-4, main1_V2-V9, main2_V2-V9, main3_V2-V9, main4_V2-V9, etc.
- **Data Access**: Direct CSV file reading from `./cleaned_csvs/` directory
- **No Abstraction**: Direct pandas DataFrame operations

**Example Current Strategy:**
```python
def main1(spot_adjustment_type=0, spot_adjustment=1, call_sell_position=0):
    """
    Weekly expiry-to-expiry: Sell Call + Buy Future
    Parameters:
      - spot_adjustment_type: 0=None, 1=Upside, 2=Downside, 3=Both
      - spot_adjustment: Percentage threshold for re-entry
      - call_sell_position: % above/below spot for call strike
    """
    data_df = getStrikeData("NIFTY")  # Direct CSV read
    base2_df = pd.read_csv("./Filter/base2.csv")  # Direct file access
    # ... pandas operations, no abstraction layers
```

#### Workflow Orchestration
- **File**: `workflow.py`
- **Pattern**: Subprocess orchestration of command-line scripts
- **No API**: All operations via CLI arguments
- **Steps**: Check dependencies → Create DB → Ingest → Build aux tables → Audit

#### Data Access Utilities
- **File**: `db_utils.py`
- **Pattern**: Direct SQLite3 queries for stats, duplicates, integrity checks
- **No Abstraction**: Raw SQL with sqlite3.connect()

**Key Differences from Proposed Architecture:**
| Aspect | Current | Proposed |
|--------|---------|----------|
| **Interface** | Command-line | REST API + Web UI |
| **Strategies** | Python functions | Pluggable strategy classes |
| **Data Access** | Direct CSV/SQL | DataProvider abstraction |
| **Multi-tenancy** | None | User isolation + quotas |
| **Caching** | None | Parameter-based result cache |
| **Scalability** | Single machine | Horizontal scaling |
| **Deployment** | Local scripts | Docker + cloud-ready |

---

## 2. SYSTEM OVERVIEW
- 20+ KLOC strategy code in Python (analyse_bhavcopy_02-01-2026.py)
- Manual execution, no API layer, no caching
- Strategies hardcoded, UI would need updates for each new strategy

**Target State:**
- Automated CSV ingestion with deduplication
- REST APIs for strategy execution
- Parameter-based caching (same params = instant result)
- Schema-driven UI (auto-generates forms from strategy metadata)
- Production-ready with logging, monitoring, error handling

### 1.3 Key Architectural Decisions

| Decision | Rationale |
|----------|-----------|
| **6-Layer Architecture** | Separation of concerns, independent scaling |
| **Strategies as Data** | Add strategies without API/UI changes |
| **Parameter Hashing** | Deterministic cache keys for idempotency |
| **SQLite → PostgreSQL** | Start simple, migrate when scale requires |
| **FastAPI + Flask** | FastAPI for execution speed, Flask for orchestration |
| **Next.js Frontend** | SSR, dynamic forms, modern UX |


---

## 2. SYSTEM OVERVIEW

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE (Next.js)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ Strategy     │  │ Parameter    │  │ Results      │              │
│  │ Discovery    │  │ Form (Auto)  │  │ Visualization│              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS/REST
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      API LAYER (FastAPI + Flask)                     │
│  ┌──────────────────────────┐  ┌──────────────────────────┐        │
│  │ FastAPI Execution Gateway│  │ Flask Orchestration      │        │
│  │ - POST /api/v1/execute   │  │ - Authentication         │        │
│  │ - GET /api/v1/strategies │  │ - Authorization          │        │
│  │ - Parameter Validation   │  │ - User Management        │        │
│  └──────────────────────────┘  └──────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CACHING & IDEMPOTENCY LAYER                       │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ Parameter Hash Generator → Cache Lookup → Execute/Return │      │
│  │ SHA256(strategy_name + sorted(params)) = cache_key       │      │
│  └──────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STRATEGY EXECUTION ENGINE                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Strategy     │  │ Data Provider│  │ Result       │             │
│  │ Interface    │  │ Abstraction  │  │ Formatter    │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATABASE LAYER                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │ Market Data  │  │ Execution    │  │ Strategy     │             │
│  │ (26GB+)      │  │ Results      │  │ Registry     │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└─────────────────────────────────────────────────────────────────────┘
                              ▲
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DATA INGESTION SERVICE                            │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │ Folder Monitor → Deduplication → Validation → Insert     │      │
│  │ (cleaned_csvs, strikeData, expiryData)                   │      │
│  └──────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow: Strategy Execution

```
1. User selects strategy "main1_V7" in UI
2. UI fetches parameter schema from API
3. UI auto-generates form (spot_adjustment_type, call_sell_position, etc.)
4. User fills form and clicks "Execute"
5. Frontend sends POST /api/v1/execute with parameters
6. Flask validates JWT token and user permissions
7. FastAPI receives request and validates parameters against schema
8. Generate cache_key = SHA256("main1_V7" + sorted(parameters))
9. Check parameter_cache table for cache_key
10. IF cache hit:
    - Fetch execution_id from cache
    - Return stored result (cached=true, <50ms response)
11. ELSE cache miss:
    - Create execution_run record (status=PENDING)
    - Load strategy class from registry
    - Instantiate DataProvider with database connection
    - Call strategy.execute(parameters, data_provider)
    - Store result in execution_results table
    - Update execution_run (status=COMPLETED)
    - Insert cache_key → execution_id mapping
    - Return result (cached=false)
12. Frontend displays results with charts and metrics
```

### 2.3 Constraints & Requirements

#### 2.3.1 Existing Infrastructure
- **Database:** SQLite 26GB+ (bhavcopy_data.db) with 6362 CSV files processed
- **CSV Folders:** cleaned_csvs (6362 files), strikeData, expiryData
- **Strategies:** 20+ KLOC Python code (main1, main1_V7, etc.)
- **Data Integrity:** Must preserve existing data, never reinsert

#### 2.3.2 Functional Requirements
- **FR1:** Continuous CSV ingestion with file-level deduplication
- **FR2:** Strategy execution via REST API with parameter validation
- **FR3:** Result caching for identical parameter combinations
- **FR4:** Dynamic UI that adapts to new strategies without code changes
- **FR5:** Execution history and audit trail
- **FR6:** User authentication and authorization

#### 2.3.3 Non-Functional Requirements
- **NFR1:** Idempotency - same params = same result (deterministic)
- **NFR2:** Performance - cached results <50ms, new execution <30s
- **NFR3:** Scalability - support 100+ concurrent users
- **NFR4:** Reliability - 99.9% uptime, graceful error handling
- **NFR5:** Security - JWT auth, RBAC, rate limiting
- **NFR6:** Observability - structured logging, metrics, tracing

---

## 3. ARCHITECTURAL PRINCIPLES

### 3.1 Core Principles

#### 3.1.1 Separation of Concerns
Each layer has a single, well-defined responsibility:
- **Data Ingestion:** Only manages CSV → Database sync
- **Database:** Only stores and retrieves data
- **Strategy Engine:** Only executes business logic
- **API Layer:** Only handles HTTP and validation
- **Caching:** Only manages result reuse
- **Frontend:** Only handles user interaction

#### 3.1.2 Strategies as Data
**Problem:** Hardcoding strategies in API/UI creates tight coupling.

**Solution:** Treat strategies as database records with metadata.

```python
# Strategy Registry Table
strategy_registry:
  - strategy_name: "main1_V7"
  - version: "1.0.0"
  - parameter_schema: {
      "type": "object",
      "properties": {
        "spot_adjustment_type": {"type": "integer", "enum": [0,1,2,3]},
        "spot_adjustment": {"type": "number", "minimum": 0},
        "call_sell_position": {"type": "number"}
      }
    }
  - description: "Weekly expiry to expiry with spot adjustment"
  - is_active: true
```

**Benefits:**
- Add new strategies by inserting database record
- UI auto-generates forms from parameter_schema
- APIs remain stable as strategies evolve
- Version control for strategy changes

#### 3.1.3 Idempotency via Parameter Hashing
**Problem:** Users may accidentally run same backtest multiple times.

**Solution:** Generate deterministic cache key from parameters.

```python
def generate_cache_key(strategy_name: str, parameters: dict) -> str:
    # Sort parameters for consistency
    sorted_params = json.dumps(parameters, sort_keys=True)
    
    # Include strategy name and version
    cache_input = f"{strategy_name}:{sorted_params}"
    
    # SHA256 hash
    return hashlib.sha256(cache_input.encode()).hexdigest()

# Example:
# strategy_name = "main1_V7"
# parameters = {"spot_adjustment_type": 1, "spot_adjustment": 0.8}
# cache_key = "a3f5b2c1..." (deterministic)
```

**Benefits:**
- Instant results for repeated runs (<50ms)
- Reduced compute costs
- Consistent results for same inputs

#### 3.1.4 Database Portability
**Current:** SQLite (26GB+, single file, no network)  
**Future:** PostgreSQL (multi-user, ACID, replication)

**Strategy:** Use SQLAlchemy ORM for database abstraction.

```python
# Works with both SQLite and PostgreSQL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# SQLite
engine = create_engine('sqlite:///bhavcopy_data.db')

# PostgreSQL (future)
engine = create_engine('postgresql://user:pass@host/db')

Session = sessionmaker(bind=engine)
session = Session()

# Same query works on both
results = session.query(CleanedCSV).filter_by(Symbol='NIFTY').all()
```

#### 3.1.5 API Stability
**Problem:** Strategy changes shouldn't break API contracts.

**Solution:** Version endpoints and use generic execution interface.

```python
# Stable endpoint (never changes)
POST /api/v1/execute
{
  "strategy_name": "main1_V7",  # Dynamic
  "parameters": {...}            # Validated against schema
}

# Response format (stable)
{
  "execution_id": "uuid",
  "status": "COMPLETED",
  "cached": false,
  "result": {
    "trades": [...],
    "metrics": {...}
  }
}
```

### 3.2 Design Patterns

#### 3.2.1 Strategy Pattern
All strategies implement common interface:

```python
class StrategyInterface(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        pass
    
    @property
    @abstractmethod
    def parameter_schema(self) -> dict:
        pass
    
    @abstractmethod
    def execute(self, parameters: dict, data_provider: DataProvider) -> dict:
        pass
```

#### 3.2.2 Data Provider Pattern
Strategies don't access database directly:

```python
class DataProvider:
    def __init__(self, db_session):
        self.session = db_session
    
    def get_options_chain(self, symbol: str, date: str, expiry: str) -> pd.DataFrame:
        # Encapsulates database query logic
        pass
    
    def get_spot_price(self, symbol: str, date: str) -> float:
        pass
```

**Benefits:**
- Strategies are testable (mock DataProvider)
- Database changes don't affect strategies
- Can add caching at DataProvider level

#### 3.2.3 Repository Pattern
Database access through repositories:

```python
class ExecutionRepository:
    def __init__(self, session):
        self.session = session
    
    def create_execution(self, strategy_id, parameters, user_id):
        execution = ExecutionRun(...)
        self.session.add(execution)
        self.session.commit()
        return execution
    
    def get_execution_by_id(self, execution_id):
        return self.session.query(ExecutionRun).get(execution_id)
```

---

## 4. LAYER 1: DATA INGESTION SERVICE

### 4.1 Responsibility
Continuously monitor CSV folders and maintain database sync with filesystem.

### 4.2 Core Functions

#### 4.2.1 Folder Monitoring
Monitor three folders for new CSV files:
- `cleaned_csvs/` - Market data (6362 files, 2000-2026)
- `strikeData/` - Underlying prices
- `expiryData/` - Expiry date mappings

```python
class CSVFolderMonitor:
    def __init__(self, folder_path: str, db_session):
        self.folder_path = folder_path
        self.session = db_session
    
    def scan_for_new_files(self) -> List[Path]:
        """Find CSV files not yet in database"""
        all_files = list(Path(self.folder_path).rglob("*.csv"))
        
        new_files = []
        for file_path in all_files:
            file_name = file_path.name
            file_size = file_path.stat().st_size
            
            # Check if already processed
            exists = self.session.query(IngestionMetadata).filter_by(
                file_name=file_name,
                file_size=file_size,
                status='SUCCESS'
            ).first()
            
            if not exists:
                new_files.append(file_path)
        
        return new_files
```

#### 4.2.2 File-Level Deduplication
Track files by name + size (not path):

```sql
CREATE TABLE ingestion_metadata (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT,              -- Full path (for reference)
    file_name TEXT NOT NULL,     -- Basename only
    file_size INTEGER NOT NULL,  -- Bytes
    file_hash TEXT,              -- SHA256 (optional)
    ingestion_date TIMESTAMP,
    row_count INTEGER,
    status TEXT,                 -- SUCCESS/ERROR
    UNIQUE(file_name, file_size) -- Deduplication key
);
```

**Why name + size?**
- Robust to folder reorganization
- Fast lookup (no hash computation)
- Handles file moves gracefully

#### 4.2.3 Row-Level Deduplication
Business key uniquely identifies market data row:

```python
BUSINESS_KEY = [
    'Date',
    'Symbol',
    'Instrument',
    'ExpiryDate',
    'StrikePrice',
    'OptionType'
]

# Remove duplicates before insert
df_clean = df.drop_duplicates(subset=BUSINESS_KEY, keep='first')
```

#### 4.2.4 Performance Optimizations

**Current Implementation (bhavcopy_db_builder.py):**
```python
def ingest_directory(self, csv_directory: str):
    # 1. Drop indices before bulk insert
    self.drop_indices()
    
    # 2. Process files in batches
    for csv_file in csv_files:
        df = pd.read_csv(csv_file, low_memory=False)
        df = self.normalize_csv_data(df)
        df_clean, duplicates = self.validate_business_key(df)
        
        # 3. Batch insert (50K rows/chunk)
        self.insert_cleaned_data(df_clean)
        
        # 4. Mark file as processed
        self.mark_file_processed(csv_file, file_hash, rows_inserted)
    
    # 5. Rebuild indices after completion
    self.rebuild_indices()
```

**Performance Results:**
- Before optimization: 1.2 files/sec (UNIQUE constraint bottleneck)
- After optimization: 9968 files/sec (indices dropped during insert)

**Key Techniques:**
1. **Drop indices before bulk insert** - Massive speedup
2. **Plain INSERT** - No constraint checking during insert
3. **Batch commits** - Single commit per file
4. **Vectorized pandas** - No iterrows(), use .values.tolist()
5. **Rebuild indices after** - One-time cost at end

### 4.3 Crash Recovery

```python
def resume_ingestion(self):
    """Resume from last successful file"""
    # Get last processed file
    last_file = self.session.query(IngestionMetadata)\
        .filter_by(status='SUCCESS')\
        .order_by(IngestionMetadata.ingestion_date.desc())\
        .first()
    
    if last_file:
        logger.info(f"Resuming from: {last_file.file_name}")
    
    # Process only new files
    new_files = self.scan_for_new_files()
    self.ingest_files(new_files)
```

### 4.4 Backfilling Historical Data

```python
def backfill_missing_dates(self, start_date: str, end_date: str):
    """Find and ingest missing dates in range"""
    # Get all dates in range
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')
    
    # Get dates already in database
    existing_dates = self.session.query(CleanedCSV.Date)\
        .distinct()\
        .all()
    existing_dates = set([d[0] for d in existing_dates])
    
    # Find missing dates
    missing_dates = [d for d in date_range if d not in existing_dates]
    
    logger.info(f"Found {len(missing_dates)} missing dates")
    
    # Look for corresponding CSV files
    for date in missing_dates:
        file_name = f"{date.strftime('%Y-%m-%d')}.csv"
        file_path = Path(self.folder_path) / file_name
        
        if file_path.exists():
            logger.info(f"Backfilling: {file_name}")
            self.ingest_csv_file(file_path)
        else:
            logger.warning(f"CSV not found: {file_name}")
```

### 4.5 Output Guarantees

After ingestion completes:
1. ✅ Database reflects exact filesystem state
2. ✅ No duplicate files in database
3. ✅ No duplicate rows (business key enforced)
4. ✅ Full audit trail in ingestion_metadata
5. ✅ Idempotent - re-running is safe
6. ✅ Visual feedback - prints which CSVs stored/skipped

```
✓ Stored: 2025-01-02.csv (1234 rows)
✓ Stored: 2025-01-03.csv (1456 rows)
⊘ Skipped: 2025-01-04.csv (already processed)
✓ Stored: 2025-01-05.csv (1567 rows)
```

---

## 5. LAYER 2: DATABASE LAYER

### 5.1 Responsibility
Single source of truth for all data: market data, execution results, strategy metadata.

### 5.2 Schema Design

#### 5.2.1 Market Data Tables (Existing)

**cleaned_csvs** - Main options/futures data
```sql
CREATE TABLE cleaned_csvs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Date DATE NOT NULL,
    ExpiryDate DATE,
    Instrument TEXT,              -- OPTIDX, FUTIDX, OPTSTK, FUTSTK
    Symbol TEXT,                  -- NIFTY, BANKNIFTY, etc.
    StrikePrice REAL,
    OptionType TEXT,              -- CE, PE
    Open REAL,
    High REAL,
    Low REAL,
    Close REAL,
    SettledPrice REAL,
    Contracts INTEGER,
    TurnOver REAL,
    OpenInterest INTEGER
);

-- Indices for query performance
CREATE INDEX idx_cleaned_csvs_date ON cleaned_csvs(Date);
CREATE INDEX idx_cleaned_csvs_symbol ON cleaned_csvs(Symbol);
CREATE INDEX idx_cleaned_csvs_date_symbol ON cleaned_csvs(Date, Symbol);
CREATE INDEX idx_cleaned_csvs_expiry ON cleaned_csvs(ExpiryDate);
```

**strike_data** - Underlying asset prices
```sql
CREATE TABLE strike_data (
    Ticker TEXT,
    Date DATE,
    Close REAL,
    PRIMARY KEY (Ticker, Date)
);

CREATE INDEX idx_strike_data_date ON strike_data(Date);
```

**expiry_data** - Expiry date mappings
```sql
CREATE TABLE expiry_data (
    Symbol TEXT PRIMARY KEY,
    Previous_Expiry DATE,
    Current_Expiry DATE,
    Next_Expiry DATE
);
```

#### 5.2.2 Strategy Execution Tables (New)

**strategy_registry** - Strategy metadata
```sql
CREATE TABLE strategy_registry (
    strategy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT UNIQUE NOT NULL,
    strategy_version TEXT NOT NULL,
    parameter_schema JSON NOT NULL,  -- JSON Schema for validation
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);

-- Example record:
INSERT INTO strategy_registry VALUES (
    1,
    'main1_V7',
    '1.0.0',
    '{
        "type": "object",
        "properties": {
            "spot_adjustment_type": {"type": "integer", "enum": [0,1,2,3]},
            "spot_adjustment": {"type": "number", "minimum": 0},
            "call_sell_position": {"type": "number"}
        },
        "required": ["spot_adjustment_type", "spot_adjustment", "call_sell_position"]
    }',
    'Weekly expiry to expiry with spot adjustment',
    '2026-01-01 00:00:00',
    '2026-01-01 00:00:00',
    TRUE
);
```

**execution_runs** - Execution tracking
```sql
CREATE TABLE execution_runs (
    execution_id TEXT PRIMARY KEY,  -- UUID
    strategy_id INTEGER NOT NULL,
    parameter_hash TEXT NOT NULL,   -- SHA256 for caching
    parameters JSON NOT NULL,       -- Actual parameters used
    status TEXT NOT NULL,           -- PENDING, RUNNING, COMPLETED, FAILED
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER,
    user_id INTEGER,                -- FK to users table
    error_message TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategy_registry(strategy_id)
);

CREATE INDEX idx_execution_runs_status ON execution_runs(status);
CREATE INDEX idx_execution_runs_user ON execution_runs(user_id);
CREATE INDEX idx_execution_runs_strategy ON execution_runs(strategy_id);
CREATE INDEX idx_execution_runs_param_hash ON execution_runs(parameter_hash);
```

**execution_results** - Execution outputs
```sql
CREATE TABLE execution_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT UNIQUE NOT NULL,
    result_data JSON NOT NULL,      -- Full result (trades, etc.)
    metrics JSON NOT NULL,          -- PnL, Sharpe, drawdown, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES execution_runs(execution_id)
);

CREATE INDEX idx_execution_results_execution ON execution_results(execution_id);
```

**parameter_cache** - Idempotency cache
```sql
CREATE TABLE parameter_cache (
    cache_key TEXT PRIMARY KEY,    -- SHA256(strategy_name + sorted(params))
    execution_id TEXT NOT NULL,
    last_accessed TIMESTAMP,
    access_count INTEGER DEFAULT 1,
    FOREIGN KEY (execution_id) REFERENCES execution_runs(execution_id)
);

CREATE INDEX idx_parameter_cache_execution ON parameter_cache(execution_id);
```

### 5.3 Design Principles

#### 5.3.1 Read-Optimized
- Indices on all query paths
- Denormalized metrics in execution_results
- JSON columns for flexible schema

#### 5.3.2 Append-Only Results
- Never UPDATE execution_results
- Immutable history for audit trail
- New execution = new record

#### 5.3.3 Parameter Hashing
- Deterministic cache keys
- Fast lookup (indexed)
- Collision-resistant (SHA256)

### 5.4 Migration Path: SQLite → PostgreSQL

**Phase 1: SQLite (Current)**
```python
engine = create_engine('sqlite:///bhavcopy_data.db')
```

**Phase 2: PostgreSQL (Future)**
```python
engine = create_engine('postgresql://user:pass@host:5432/bhavcopy')
```

**Migration Steps:**
1. Export SQLite to SQL dump
2. Convert SQLite-specific syntax to PostgreSQL
3. Import into PostgreSQL
4. Update connection string
5. Test queries for compatibility

**SQLAlchemy ensures compatibility:**
```python
# Same code works on both databases
from sqlalchemy.orm import Session

def get_execution(session: Session, execution_id: str):
    return session.query(ExecutionRun).filter_by(
        execution_id=execution_id
    ).first()
```


### 1.4 Success Metrics

- **Performance:** Process 6362 CSV files in <30 minutes
- **API Response:** <50ms for cached results, <5s for new executions
- **Uptime:** 99.9% availability
- **Scalability:** Support 100+ concurrent users
- **Extensibility:** Add new strategy in <1 hour without API/UI changes

---

## 2. SYSTEM OVERVIEW

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND LAYER                          │
│                    Next.js + TypeScript + React                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Strategy     │  │ Dynamic Form │  │ Result       │         │
│  │ Discovery    │  │ Generator    │  │ Visualization│         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              ↕ HTTPS/REST
┌─────────────────────────────────────────────────────────────────┐
│                          API LAYER                              │
│  ┌──────────────────────┐    ┌──────────────────────┐          │
│  │   Flask Backend      │    │   FastAPI Gateway    │          │
│  │   (Orchestration)    │←──→│   (Execution)        │          │
│  │ • Auth/Sessions      │    │ • Strategy Execution │          │
│  │ • User Management    │    │ • Parameter Validation│         │
│  │ • Admin Controls     │    │ • Result Retrieval   │          │
│  └──────────────────────┘    └──────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                    CACHING & IDEMPOTENCY                        │
│  ┌──────────────────────────────────────────────────┐          │
│  │  Parameter Hash → Execution ID Mapping           │          │
│  │  SHA256(strategy_name + sorted(params))          │          │
│  └──────────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                   STRATEGY EXECUTION ENGINE                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Strategy     │  │ Data Provider│  │ Result       │         │
│  │ Interface    │  │ Abstraction  │  │ Formatter    │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                        DATABASE LAYER                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Market Data  │  │ Execution    │  │ Strategy     │         │
│  │ (26GB+)      │  │ Results      │  │ Registry     │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│              SQLite (Current) → PostgreSQL (Future)             │
└─────────────────────────────────────────────────────────────────┘
                              ↕
┌─────────────────────────────────────────────────────────────────┐
│                    DATA INGESTION SERVICE                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Folder       │  │ Deduplication│  │ Metadata     │         │
│  │ Monitor      │  │ Engine       │  │ Tracking     │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│         Watches: cleaned_csvs/, strikeData/, expiryData/        │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow: Strategy Execution

```
User Request → Flask Auth → FastAPI Validation → Cache Check
                                                      ↓
                                              [Cache Hit?]
                                              ↙         ↘
                                          YES            NO
                                           ↓              ↓
                                    Return Result   Execute Strategy
                                                          ↓
                                                   Store Result
                                                          ↓
                                                   Update Cache
                                                          ↓
                                                   Return Result
```


### 5.5 Query Optimization Examples

#### 5.5.1 Get Options Chain for Date
```python
def get_options_chain(session, symbol: str, date: str, expiry: str) -> pd.DataFrame:
    """
    Retrieve options chain for specific symbol, date, and expiry.
    Optimized with proper indices.
    """
    query = session.query(CleanedCSV).filter(
        CleanedCSV.Symbol == symbol,
        CleanedCSV.Date == date,
        CleanedCSV.ExpiryDate == expiry,
        CleanedCSV.Instrument == 'OPTIDX'
    ).order_by(CleanedCSV.StrikePrice)
    
    df = pd.read_sql(query.statement, session.bind)
    return df

# Performance: <100ms for typical options chain (100-200 strikes)
```

#### 5.5.2 Get Historical Spot Prices
```python
def get_spot_prices(session, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    Retrieve spot prices for date range.
    Uses strike_data table for fast lookup.
    """
    query = session.query(StrikeData).filter(
        StrikeData.Ticker == symbol,
        StrikeData.Date >= start_date,
        StrikeData.Date <= end_date
    ).order_by(StrikeData.Date)
    
    df = pd.read_sql(query.statement, session.bind)
    return df

# Performance: <50ms for 1 year of daily data
```

---

## 6. LAYER 3: STRATEGY EXECUTION ENGINE

### 6.1 Responsibility
Execute trading strategies with parameter-driven logic, isolated from database and API concerns.

### 6.2 Strategy Interface Contract

All strategies must implement this interface:

```python
from abc import ABC, abstractmethod
from typing import Dict, Any
import pandas as pd

class StrategyInterface(ABC):
    """
    Base interface for all trading strategies.
    Ensures consistent execution contract across strategies.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier"""
        pass
    
    @property
    @abstractmethod
    def version(self) -> str:
        """Strategy version (semantic versioning)"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable strategy description"""
        pass
    
    @property
    @abstractmethod
    def parameter_schema(self) -> Dict[str, Any]:
        """
        JSON Schema defining valid parameters.
        Used for API validation and UI form generation.
        """
        pass
    
    @abstractmethod
    def execute(self, parameters: Dict[str, Any], data_provider: 'DataProvider') -> Dict[str, Any]:
        """
        Execute strategy with given parameters.
        
        Args:
            parameters: Validated parameters matching parameter_schema
            data_provider: Abstraction for database access
            
        Returns:
            Dictionary containing:
                - trades: List of trade records
                - metrics: Performance metrics (PnL, Sharpe, etc.)
                - metadata: Execution metadata
        """
        pass

### 6.3 DataProvider Abstraction

Strategies access data through DataProvider, not direct database queries:

```python
class DataProvider:
    """
    Provides data access methods for strategies.
    Encapsulates database queries and caching logic.
    """
    
    def __init__(self, db_session):
        self.session = db_session
        self._cache = {}  # In-memory cache for repeated queries
    
    def get_options_chain(self, symbol: str, date: str, expiry: str) -> pd.DataFrame:
        """Get options chain for specific date and expiry"""
        cache_key = f"options_{symbol}_{date}_{expiry}"
        
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        query = self.session.query(CleanedCSV).filter(
            CleanedCSV.Symbol == symbol,
            CleanedCSV.Date == date,
            CleanedCSV.ExpiryDate == expiry,
            CleanedCSV.Instrument == 'OPTIDX'
        )
        
        df = pd.read_sql(query.statement, self.session.bind)
        self._cache[cache_key] = df
        return df
    
    def get_spot_price(self, symbol: str, date: str) -> float:
        """Get spot price for specific date"""
        result = self.session.query(StrikeData.Close).filter(
            StrikeData.Ticker == symbol,
            StrikeData.Date == date
        ).first()
        
        return result[0] if result else None
    
    def get_expiry_dates(self, symbol: str) -> Dict[str, str]:
        """Get expiry date mappings for symbol"""
        result = self.session.query(ExpiryData).filter(
            ExpiryData.Symbol == symbol
        ).first()
        
        if result:
            return {
                'previous': result.Previous_Expiry,
                'current': result.Current_Expiry,
                'next': result.Next_Expiry
            }
        return None
```

**Benefits:**
- Strategies are testable (mock DataProvider)
- Database changes don't affect strategies
- Can add caching at DataProvider level
- Consistent error handling

### 6.4 Example Strategy Implementation

Based on `analyse_bhavcopy_02-01-2026.py` main1() function:

```python
class Main1V7Strategy(StrategyInterface):
    """
    Weekly expiry-to-expiry strategy:
    - Sell Call (current week expiry)
    - Buy Future (next month expiry)
    - Spot adjustment based on parameters
    """
    
    @property
    def name(self) -> str:
        return "main1_V7"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    @property
    def description(self) -> str:
        return "Weekly expiry to expiry with spot adjustment and call sell position"
    
    @property
    def parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "spot_adjustment_type": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3],
                    "description": "0=None, 1=Upside, 2=Downside, 3=Both"
                },
                "spot_adjustment": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 10,
                    "description": "Percentage threshold for re-entry"
                },
                "call_sell_position": {
                    "type": "number",
                    "minimum": -5,
                    "maximum": 5,
                    "description": "Percentage above/below spot for call strike"
                }
            },
            "required": ["spot_adjustment_type", "spot_adjustment", "call_sell_position"]
        }
    
    def execute(self, parameters: Dict[str, Any], data_provider: DataProvider) -> Dict[str, Any]:
        """Execute strategy logic"""
        spot_adjustment_type = parameters['spot_adjustment_type']
        spot_adjustment = parameters['spot_adjustment']
        call_sell_position = parameters['call_sell_position']
        
        # Get weekly expiry dates
        weekly_expiries = data_provider.get_weekly_expiries('NIFTY')
        
        trades = []
        
        for expiry_window in weekly_expiries:
            prev_expiry = expiry_window['previous']
            curr_expiry = expiry_window['current']
            
            # Get spot prices for date range
            spot_data = data_provider.get_spot_prices('NIFTY', prev_expiry, curr_expiry)
            
            # Calculate re-entry points based on spot adjustment
            intervals = self._calculate_intervals(spot_data, spot_adjustment_type, spot_adjustment)
            
            for interval in intervals:
                from_date = interval['from']
                to_date = interval['to']
                
                # Get entry spot
                entry_spot = data_provider.get_spot_price('NIFTY', from_date)
                
                # Calculate call strike
                call_strike = round((entry_spot * (1 + call_sell_position/100)) / 100) * 100
                
                # Get options data
                call_entry = data_provider.get_option_price('NIFTY', from_date, curr_expiry, call_strike, 'CE')
                call_exit = data_provider.get_option_price('NIFTY', to_date, curr_expiry, call_strike, 'CE')
                
                # Get future data
                fut_expiry = data_provider.get_next_monthly_expiry('NIFTY', curr_expiry)
                fut_entry = data_provider.get_future_price('NIFTY', from_date, fut_expiry)
                fut_exit = data_provider.get_future_price('NIFTY', to_date, fut_expiry)
                
                # Calculate P&L
                call_pnl = call_entry - call_exit  # Sell call
                fut_pnl = fut_exit - fut_entry      # Buy future
                total_pnl = call_pnl + fut_pnl
                
                trades.append({
                    'entry_date': from_date,
                    'exit_date': to_date,
                    'entry_spot': entry_spot,
                    'call_strike': call_strike,
                    'call_pnl': call_pnl,
                    'fut_pnl': fut_pnl,
                    'total_pnl': total_pnl
                })
        
        # Calculate metrics
        metrics = self._calculate_metrics(trades)
        
        return {
            'trades': trades,
            'metrics': metrics,
            'metadata': {
                'strategy': self.name,
                'version': self.version,
                'parameters': parameters
            }
        }
    
    def _calculate_intervals(self, spot_data, adjustment_type, adjustment_pct):
        """Calculate re-entry intervals based on spot movement"""
        # Implementation from analyse_bhavcopy_02-01-2026.py
        pass
    
    def _calculate_metrics(self, trades):
        """Calculate performance metrics"""
        df = pd.DataFrame(trades)
        
        return {
            'total_trades': len(trades),
            'total_pnl': df['total_pnl'].sum(),
            'avg_pnl': df['total_pnl'].mean(),
            'win_rate': (df['total_pnl'] > 0).sum() / len(trades) * 100,
            'max_profit': df['total_pnl'].max(),
            'max_loss': df['total_pnl'].min(),
            'sharpe_ratio': df['total_pnl'].mean() / df['total_pnl'].std() if df['total_pnl'].std() > 0 else 0
        }
```

### 6.5 Strategy Registry Management

```python
class StrategyRegistry:
    """
    Manages strategy registration and discovery.
    Strategies are registered at application startup.
    """
    
    def __init__(self, db_session):
        self.session = db_session
        self._strategies = {}  # In-memory cache
    
    def register_strategy(self, strategy_class: type):
        """
        Register a strategy class.
        Creates/updates database record with metadata.
        """
        strategy = strategy_class()
        
        # Validate interface
        if not isinstance(strategy, StrategyInterface):
            raise ValueError(f"{strategy_class.__name__} must implement StrategyInterface")
        
        # Check if strategy exists in database
        db_strategy = self.session.query(StrategyRegistryModel).filter_by(
            strategy_name=strategy.name
        ).first()
        
        if db_strategy:
            # Update existing
            db_strategy.strategy_version = strategy.version
            db_strategy.parameter_schema = json.dumps(strategy.parameter_schema)
            db_strategy.description = strategy.description
            db_strategy.updated_at = datetime.now()
        else:
            # Create new
            db_strategy = StrategyRegistryModel(
                strategy_name=strategy.name,
                strategy_version=strategy.version,
                parameter_schema=json.dumps(strategy.parameter_schema),
                description=strategy.description,
                is_active=True
            )
            self.session.add(db_strategy)
        
        self.session.commit()
        
        # Cache in memory
        self._strategies[strategy.name] = strategy_class
        
        logger.info(f"Registered strategy: {strategy.name} v{strategy.version}")
    
    def get_strategy(self, strategy_name: str) -> StrategyInterface:
        """Get strategy instance by name"""
        if strategy_name not in self._strategies:
            raise ValueError(f"Strategy not found: {strategy_name}")
        
        return self._strategies[strategy_name]()
    
    def list_strategies(self) -> List[Dict]:
        """List all active strategies"""
        strategies = self.session.query(StrategyRegistryModel).filter_by(
            is_active=True
        ).all()
        
        return [{
            'name': s.strategy_name,
            'version': s.strategy_version,
            'description': s.description,
            'parameter_schema': json.loads(s.parameter_schema)
        } for s in strategies]

# Application startup
registry = StrategyRegistry(db_session)
registry.register_strategy(Main1V7Strategy)
registry.register_strategy(Main1V8Strategy)
# ... register all strategies
```

### 6.6 Adding New Strategies

**Steps to add a new strategy:**

1. **Create strategy class** implementing `StrategyInterface`
2. **Register at startup** in `registry.register_strategy()`
3. **Done!** API and UI automatically support it

**No changes needed to:**
- API endpoints (generic execution interface)
- Frontend code (auto-generates forms from schema)
- Database schema (strategies stored as data)

**Example: Adding "Iron Condor" strategy**

```python
class IronCondorStrategy(StrategyInterface):
    @property
    def name(self) -> str:
        return "iron_condor_v1"
    
    @property
    def parameter_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "wing_width": {"type": "integer", "minimum": 50, "maximum": 500},
                "days_to_expiry": {"type": "integer", "minimum": 1, "maximum": 45}
            }
        }
    
    def execute(self, parameters, data_provider):
        # Strategy logic here
        pass

# Register
registry.register_strategy(IronCondorStrategy)
```

**That's it!** Frontend will show new strategy in dropdown, auto-generate form with wing_width and days_to_expiry inputs.

---

## 7. LAYER 4: API LAYER (FastAPI + Flask)

### 7.1 Responsibility
Expose strategy execution and orchestration via REST APIs with authentication and validation.

### 7.2 Architecture: Dual API Approach

**Why FastAPI + Flask?**

| Aspect | FastAPI | Flask |
|--------|---------|-------|
| **Use Case** | Strategy execution | User management, auth |
| **Strength** | High performance, async | Mature ecosystem, sessions |
| **Endpoints** | `/api/v1/execute`, `/api/v1/strategies` | `/auth/*`, `/admin/*` |
| **Response Time** | <50ms (cached), <5s (new) | <100ms |

### 7.3 FastAPI Execution Gateway

```python
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, validator
from typing import Dict, Any, List
import hashlib
import json

app = FastAPI(title="Strategy Execution API", version="1.0.0")

class ExecutionRequest(BaseModel):
    strategy_name: str
    parameters: Dict[str, Any]
    user_id: int
    
    @validator('strategy_name')
    def validate_strategy_exists(cls, v):
        # Check if strategy exists in registry
        if not registry.get_strategy(v):
            raise ValueError(f"Strategy not found: {v}")
        return v

class ExecutionResponse(BaseModel):
    execution_id: str
    status: str
    cached: bool
    result: Dict[str, Any]
    cache_key: str

@app.post("/api/v1/execute", response_model=ExecutionResponse)
async def execute_strategy(request: ExecutionRequest):
    """
    Execute strategy with given parameters.
    Returns cached result if identical parameters were used before.
    """
    
    # Generate cache key
    cache_key = generate_cache_key(request.strategy_name, request.parameters)
    
    # Check cache
    cached_execution = check_cache(cache_key)
    
    if cached_execution:
        # Cache hit - return stored result
        result = get_execution_result(cached_execution.execution_id)
        
        return ExecutionResponse(
            execution_id=cached_execution.execution_id,
            status="COMPLETED",
            cached=True,
            result=result,ii
            cache_key=cache_key
        )
    
    # Cache miss - execute strategy
    execution_id = str(uuid.uuid4())
    
    # Create execution record
    execution = create_execution_run(
        execution_id=execution_id,
        strategy_name=request.strategy_name,
        parameters=request.parameters,
        parameter_hash=cache_key,
        user_id=request.user_id,
        status="PENDING"
    )
    
    try:
        # Update status to RUNNING
        update_execution_status(execution_id, "RUNNING")
        
        # Load strategy
        strategy = registry.get_strategy(request.strategy_name)
        
        # Validate parameters against schema
        validate_parameters(strategy.parameter_schema, request.parameters)
        
        # Execute strategy
        data_provider = DataProvider(db_session)
        result = strategy.execute(request.parameters, data_provider)
        
        # Store result
        store_execution_result(execution_id, result)
        
        # Update cache
        update_parameter_cache(cache_key, execution_id)
        
        # Update status to COMPLETED
        update_execution_status(execution_id, "COMPLETED")
        
        return ExecutionResponse(
            execution_id=execution_id,
            status="COMPLETED",
            cached=False,
            result=result,
            cache_key=cache_key
        )
        
    except Exception as e:
        # Update status to FAILED
        update_execution_status(execution_id, "FAILED", error_message=str(e))
        
        logger.error(f"Execution failed: {execution_id}", exc_info=True)
        
        raise HTTPException(status_code=500, detail=str(e))

def generate_cache_key(strategy_name: str, parameters: Dict[str, Any]) -> str:
    """Generate deterministic cache key from parameters"""
    sorted_params = json.dumps(parameters, sort_keys=True)
    cache_input = f"{strategy_name}:{sorted_params}"
    return hashlib.sha256(cache_input.encode()).hexdigest()
```

### 7.4 Additional FastAPI Endpoints

```python
@app.get("/api/v1/strategies", response_model=List[StrategyInfo])
async def list_strategies():
    """
    List all available strategies with their parameter schemas.
    Used by frontend to discover strategies and generate forms.
    """
    strategies = registry.list_strategies()
    return strategies

@app.get("/api/v1/strategies/{strategy_name}", response_model=StrategyInfo)
async def get_strategy_info(strategy_name: str):
    """Get detailed information about a specific strategy"""
    strategy = registry.get_strategy(strategy_name)
    
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    
    return {
        'name': strategy.name,
        'version': strategy.version,
        'description': strategy.description,
        'parameter_schema': strategy.parameter_schema
    }

@app.get("/api/v1/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(execution_id: str):
    """Get execution details and results"""
    execution = get_execution_run(execution_id)
    
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    result = get_execution_result(execution_id) if execution.status == "COMPLETED" else None
    
    return {
        'execution_id': execution.execution_id,
        'strategy_name': execution.strategy_name,
        'parameters': json.loads(execution.parameters),
        'status': execution.status,
        'started_at': execution.started_at,
        'completed_at': execution.completed_at,
        'duration_ms': execution.duration_ms,
        'result': result,
        'error_message': execution.error_message
    }

@app.get("/api/v1/executions", response_model=List[ExecutionSummary])
async def list_executions(
    user_id: int = None,
    strategy_name: str = None,
    status: str = None,
    limit: int = 50
):
    """List executions with optional filters"""
    query = db_session.query(ExecutionRun)
    
    if user_id:
        query = query.filter(ExecutionRun.user_id == user_id)
    if strategy_name:
        query = query.filter(ExecutionRun.strategy_name == strategy_name)
    if status:
        query = query.filter(ExecutionRun.status == status)
    
    executions = query.order_by(ExecutionRun.started_at.desc()).limit(limit).all()
    
    return [{
        'execution_id': e.execution_id,
        'strategy_name': e.strategy_name,
        'status': e.status,
        'started_at': e.started_at,
        'duration_ms': e.duration_ms
    } for e in executions]

@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0'
    }
```

### 7.5 Flask Orchestration Backend

```python
from flask import Flask, request, jsonify, session
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY')
jwt = JWTManager(app)

@app.route('/auth/register', methods=['POST'])
def register():
    """Register new user"""
    data = request.get_json()
    
    username = data.get('username')
    password = data.get('password')
    email = data.get('email')
    
    # Validate input
    if not username or not password or not email:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Check if user exists
    existing_user = db_session.query(User).filter_by(username=username).first()
    if existing_user:
        return jsonify({'error': 'Username already exists'}), 409
    
    # Create user
    hashed_password = generate_password_hash(password)
    user = User(
        username=username,
        password_hash=hashed_password,
        email=email,
        role='user'
    )
    
    db_session.add(user)
    db_session.commit()
    
    return jsonify({
        'message': 'User created successfully',
        'user_id': user.id
    }), 201

@app.route('/auth/login', methods=['POST'])
def login():
    """Authenticate user and return JWT token"""
    data = request.get_json()
    
    username = data.get('username')
    password = data.get('password')
    
    # Find user
    user = db_session.query(User).filter_by(username=username).first()
    
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    # Create JWT token
    access_token = create_access_token(
        identity=user.id,
        additional_claims={'role': user.role}
    )
    
    return jsonify({
        'access_token': access_token,
        'user_id': user.id,
        'username': user.username,
        'role': user.role
    }), 200

@app.route('/auth/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """Get current user info from JWT token"""
    user_id = get_jwt_identity()
    user = db_session.query(User).get(user_id)
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    return jsonify({
        'user_id': user.id,
        'username': user.username,
        'email': user.email,
        'role': user.role
    }), 200

@app.route('/admin/users', methods=['GET'])
@jwt_required()
def list_users():
    """List all users (admin only)"""
    current_user_id = get_jwt_identity()
    current_user = db_session.query(User).get(current_user_id)
    
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403
    
    users = db_session.query(User).all()
    
    return jsonify([{
        'user_id': u.id,
        'username': u.username,
        'email': u.email,
        'role': u.role,
        'created_at': u.created_at.isoformat()
    } for u in users]), 200
```

### 7.6 Parameter Validation

```python
from jsonschema import validate, ValidationError

def validate_parameters(schema: Dict[str, Any], parameters: Dict[str, Any]):
    """
    Validate parameters against JSON Schema.
    Raises ValidationError if invalid.
    """
    try:
        validate(instance=parameters, schema=schema)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Parameter validation failed: {e.message}"
        )

# Example validation
schema = {
    "type": "object",
    "properties": {
        "spot_adjustment_type": {"type": "integer", "enum": [0,1,2,3]},
        "spot_adjustment": {"type": "number", "minimum": 0, "maximum": 10}
    },
    "required": ["spot_adjustment_type", "spot_adjustment"]
}

parameters = {
    "spot_adjustment_type": 1,
    "spot_adjustment": 0.8
}

validate_parameters(schema, parameters)  # Passes

parameters_invalid = {
    "spot_adjustment_type": 5,  # Not in enum
    "spot_adjustment": 0.8
}

validate_parameters(schema, parameters_invalid)  # Raises ValidationError
```

### 7.7 API Error Handling

```python
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    """Handle parameter validation errors"""
    return JSONResponse(
        status_code=400,
        content={
            'error': 'Validation Error',
            'message': str(exc),
            'details': exc.errors() if hasattr(exc, 'errors') else None
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected errors"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred',
            'execution_id': request.state.execution_id if hasattr(request.state, 'execution_id') else None
        }
    )
```

---

## 8. LAYER 5: RESULT CACHING & IDEMPOTENCY

### 8.1 Responsibility
Ensure identical parameter combinations return cached results instantly without re-execution.

### 8.2 Cache Key Generation

```python
import hashlib
import json

def generate_cache_key(strategy_name: str, parameters: Dict[str, Any]) -> str:
    """
    Generate deterministic cache key from strategy name and parameters.
    
    Key properties:
    - Deterministic: Same inputs always produce same key
    - Collision-resistant: SHA256 ensures uniqueness
    - Order-independent: Parameters sorted before hashing
    
    Args:
        strategy_name: Name of strategy
        parameters: Strategy parameters
        
    Returns:
        64-character hex string (SHA256 hash)
    """
    # Sort parameters for consistency
    sorted_params = json.dumps(parameters, sort_keys=True)
    
    # Include strategy name and version
    cache_input = f"{strategy_name}:{sorted_params}"
    
    # SHA256 hash
    return hashlib.sha256(cache_input.encode()).hexdigest()

# Example
cache_key = generate_cache_key(
    "main1_V7",
    {"spot_adjustment_type": 1, "spot_adjustment": 0.8, "call_sell_position": 0}
)
# Result: "a3f5b2c1d4e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2"
```

### 8.3 Cache Lookup Logic

```python
def check_cache(cache_key: str) -> Optional[str]:
    """
    Check if execution result exists for cache key.
    
    Args:
        cache_key: SHA256 hash of strategy + parameters
        
    Returns:
        execution_id if cache hit, None if cache miss
    """
    cache_entry = db_session.query(ParameterCache).filter_by(
        cache_key=cache_key
    ).first()
    
    if cache_entry:
        # Update access statistics
        cache_entry.last_accessed = datetime.now()
        cache_entry.access_count += 1
        db_session.commit()
        
        logger.info(f"Cache hit: {cache_key} (accessed {cache_entry.access_count} times)")
        
        return cache_entry.execution_id
    
    logger.info(f"Cache miss: {cache_key}")
    return None

def update_parameter_cache(cache_key: str, execution_id: str):
    """
    Store cache key -> execution_id mapping after successful execution.
    
    Args:
        cache_key: SHA256 hash
        execution_id: UUID of execution
    """
    cache_entry = ParameterCache(
        cache_key=cache_key,
        execution_id=execution_id,
        last_accessed=datetime.now(),
        access_count=1
    )
    
    db_session.add(cache_entry)
    db_session.commit()
    
    logger.info(f"Cache updated: {cache_key} -> {execution_id}")
```

### 8.4 Cache Benefits

**Performance:**
- Cached results: <50ms response time
- New execution: 5-30s depending on strategy complexity
- **Speedup: 100-600x for cached results**

**Cost Savings:**
- Avoid redundant computation
- Reduce database load
- Lower infrastructure costs

**User Experience:**
- Instant results for repeated runs
- Predictable response times
- Encourages experimentation

**Example Scenario:**

User runs strategy with parameters:
```json
{
  "strategy_name": "main1_V7",
  "parameters": {
    "spot_adjustment_type": 1,
    "spot_adjustment": 0.8,
    "call_sell_position": 0
  }
}
```

**First execution:**
- Cache miss
- Execute strategy: 15 seconds
- Store result
- Update cache

**Second execution (same parameters):**
- Cache hit
- Return stored result: 45ms
- **330x faster!**

### 8.5 Cache Invalidation

**When to invalidate cache:**

1. **Data updates:** New CSV files ingested
2. **Strategy changes:** Strategy code updated
3. **Manual invalidation:** Admin action

```python
def invalidate_cache_for_strategy(strategy_name: str):
    """
    Invalidate all cached results for a strategy.
    Called when strategy code is updated.
    """
    # Find all executions for this strategy
    executions = db_session.query(ExecutionRun).filter_by(
        strategy_name=strategy_name
    ).all()
    
    execution_ids = [e.execution_id for e in executions]
    
    # Delete cache entries
    db_session.query(ParameterCache).filter(
        ParameterCache.execution_id.in_(execution_ids)
    ).delete(synchronize_session=False)
    
    db_session.commit()
    
    logger.info(f"Invalidated cache for strategy: {strategy_name}")

def invalidate_cache_after_date(date: str):
    """
    Invalidate cached results that depend on data after a specific date.
    Called when new CSV files are ingested.
    """
    # This is complex - need to track date ranges used by each execution
    # For MVP, can invalidate all cache when new data arrives
    
    db_session.query(ParameterCache).delete()
    db_session.commit()
    
    logger.warning(f"Invalidated all cache due to data update after {date}")
```

---

## 9. LAYER 6: FRONTEND UI (Next.js)

### 9.1 Responsibility
Provide intuitive UI that adapts to new strategies without code changes.

### 9.2 Technology Stack

- **Framework:** Next.js 14 (App Router)
- **Language:** TypeScript
- **UI Library:** React 18
- **Styling:** Tailwind CSS
- **Forms:** React Hook Form + Zod validation
- **State:** React Query (TanStack Query)
- **Charts:** Recharts / Chart.js

### 9.3 Dynamic Form Generation

**Core Concept:** Generate forms from JSON Schema automatically.

```typescript
// types/strategy.ts
export interface StrategyInfo {
  name: string;
  version: string;
  description: string;
  parameter_schema: JSONSchema;
}

export interface JSONSchema {
  type: string;
  properties: Record<string, SchemaProperty>;
  required: string[];
}

export interface SchemaProperty {
  type: string;
  enum?: any[];
  minimum?: number;
  maximum?: number;
  description?: string;
}
```

```typescript
// components/DynamicStrategyForm.tsx
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { jsonSchemaToZod } from '@/lib/schema-converter';

interface DynamicStrategyFormProps {
  strategy: StrategyInfo;
  onSubmit: (parameters: Record<string, any>) => void;
}

export function DynamicStrategyForm({ strategy, onSubmit }: DynamicStrategyFormProps) {
  // Convert JSON Schema to Zod schema for validation
  const zodSchema = jsonSchemaToZod(strategy.parameter_schema);
  
  const { register, handleSubmit, formState: { errors } } = useForm({
    resolver: zodResolver(zodSchema)
  });
  
  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <h2 className="text-2xl font-bold">{strategy.name}</h2>
      <p className="text-gray-600">{strategy.description}</p>
      
      {Object.entries(strategy.parameter_schema.properties).map(([key, prop]) => (
        <div key={key} className="form-field">
          <label htmlFor={key} className="block font-medium">
            {formatLabel(key)}
          </label>
          
          {prop.description && (
            <p className="text-sm text-gray-500">{prop.description}</p>
          )}
          
          {renderInput(key, prop, register, errors)}
        </div>
      ))}
      
      <button type="submit" className="btn-primary">
        Execute Strategy
      </button>
    </form>
  );
}

function renderInput(
  key: string,
  prop: SchemaProperty,
  register: any,
  errors: any
) {
  // Enum -> Select dropdown
  if (prop.enum) {
    return (
      <select {...register(key)} className="form-select">
        {prop.enum.map(value => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
    );
  }
  
  // Number -> Number input with min/max
  if (prop.type === 'number' || prop.type === 'integer') {
    return (
      <input
        type="number"
        step={prop.type === 'integer' ? '1' : '0.01'}
        min={prop.minimum}
        max={prop.maximum}
        {...register(key, { valueAsNumber: true })}
        className="form-input"
      />
    );
  }
  
  // String -> Text input
  return (
    <input
      type="text"
      {...register(key)}
      className="form-input"
    />
  );
}

function formatLabel(key: string): string {
  // Convert snake_case to Title Case
  return key
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}
```

### 9.4 Strategy Execution Flow

```typescript
// app/strategies/[strategyName]/page.tsx
'use client';

import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { DynamicStrategyForm } from '@/components/DynamicStrategyForm';
import { ExecutionResults } from '@/components/ExecutionResults';

export default function StrategyPage({ params }: { params: { strategyName: string } }) {
  const [executionResult, setExecutionResult] = useState(null);
  
  // Fetch strategy info
  const { data: strategy, isLoading } = useQuery({
    queryKey: ['strategy', params.strategyName],
    queryFn: () => fetch(`/api/v1/strategies/${params.strategyName}`).then(r => r.json())
  });
  
  // Execute strategy mutation
  const executeMutation = useMutation({
    mutationFn: async (parameters: Record<string, any>) => {
      const response = await fetch('/api/v1/execute', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${getAuthToken()}`
        },
        body: JSON.stringify({
          strategy_name: params.strategyName,
          parameters,
          user_id: getCurrentUserId()
        })
      });
      
      if (!response.ok) {
        throw new Error('Execution failed');
      }
      
      return response.json();
    },
    onSuccess: (data) => {
      setExecutionResult(data);
    }
  });
  
  if (isLoading) {
    return <div>Loading strategy...</div>;
  }
  
  return (
    <div className="container mx-auto p-6">
      <div className="grid grid-cols-2 gap-6">
        {/* Left: Parameter Form */}
        <div className="bg-white p-6 rounded-lg shadow">
          <DynamicStrategyForm
            strategy={strategy}
            onSubmit={(params) => executeMutation.mutate(params)}
          />
          
          {executeMutation.isPending && (
            <div className="mt-4 text-blue-600">
              Executing strategy...
            </div>
          )}
          
          {executeMutation.isError && (
            <div className="mt-4 text-red-600">
              Error: {executeMutation.error.message}
            </div>
          )}
        </div>
        
        {/* Right: Results */}
        <div className="bg-white p-6 rounded-lg shadow">
          {executionResult ? (
            <ExecutionResults result={executionResult} />
          ) : (
            <div className="text-gray-400 text-center py-12">
              Execute strategy to see results
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
```

### 9.5 Results Visualization

```typescript
// components/ExecutionResults.tsx
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';

interface ExecutionResultsProps {
  result: {
    execution_id: string;
    status: string;
    cached: boolean;
    result: {
      trades: Trade[];
      metrics: Metrics;
    };
    cache_key: string;
  };
}

export function ExecutionResults({ result }: ExecutionResultsProps) {
  const { trades, metrics } = result.result;
  
  // Calculate cumulative P&L for chart
  const cumulativePnL = trades.reduce((acc, trade, idx) => {
    const prevPnL = idx > 0 ? acc[idx - 1].cumulative : 0;
    acc.push({
      date: trade.exit_date,
      cumulative: prevPnL + trade.total_pnl,
      trade_pnl: trade.total_pnl
    });
    return acc;
  }, []);
  
  return (
    <div className="space-y-6">
      {/* Cache Status Badge */}
      {result.cached && (
        <div className="bg-green-100 text-green-800 px-3 py-1 rounded-full inline-block">
          ⚡ Cached Result (Instant)
        </div>
      )}
      
      {/* Metrics Summary */}
      <div className="grid grid-cols-2 gap-4">
        <MetricCard label="Total P&L" value={metrics.total_pnl} format="currency" />
        <MetricCard label="Total Trades" value={metrics.total_trades} />
        <MetricCard label="Win Rate" value={metrics.win_rate} format="percentage" />
        <MetricCard label="Sharpe Ratio" value={metrics.sharpe_ratio} format="decimal" />
        <MetricCard label="Max Profit" value={metrics.max_profit} format="currency" />
        <MetricCard label="Max Loss" value={metrics.max_loss} format="currency" />
      </div>
      
      {/* Cumulative P&L Chart */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Cumulative P&L</h3>
        <LineChart width={600} height={300} data={cumulativePnL}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis />
          <Tooltip />
          <Legend />
          <Line type="monotone" dataKey="cumulative" stroke="#8884d8" name="Cumulative P&L" />
        </LineChart>
      </div>
      
      {/* Trade Table */}
      <div>
        <h3 className="text-lg font-semibold mb-4">Trade History</h3>
        <table className="w-full">
          <thead>
            <tr className="bg-gray-100">
              <th className="p-2">Entry Date</th>
              <th className="p-2">Exit Date</th>
              <th className="p-2">Call P&L</th>
              <th className="p-2">Future P&L</th>
              <th className="p-2">Total P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((trade, idx) => (
              <tr key={idx} className="border-b">
                <td className="p-2">{trade.entry_date}</td>
                <td className="p-2">{trade.exit_date}</td>
                <td className="p-2">{formatCurrency(trade.call_pnl)}</td>
                <td className="p-2">{formatCurrency(trade.fut_pnl)}</td>
                <td className={`p-2 font-semibold ${trade.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                  {formatCurrency(trade.total_pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {/* Export Button */}
      <button
        onClick={() => exportToCSV(trades)}
        className="btn-secondary"
      >
        Export to CSV
      </button>
    </div>
  );
}
```

### 9.6 Execution History Page

```typescript
// app/executions/page.tsx
'use client';

import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';

export default function ExecutionsPage() {
  const { data: executions, isLoading } = useQuery({
    queryKey: ['executions'],
    queryFn: () => fetch('/api/v1/executions?limit=100').then(r => r.json())
  });
  
  if (isLoading) return <div>Loading...</div>;
  
  return (
    <div className="container mx-auto p-6">
      <h1 className="text-3xl font-bold mb-6">Execution History</h1>
      
      <table className="w-full bg-white shadow rounded-lg">
        <thead className="bg-gray-100">
          <tr>
            <th className="p-3 text-left">Execution ID</th>
            <th className="p-3 text-left">Strategy</th>
            <th className="p-3 text-left">Status</th>
            <th className="p-3 text-left">Started At</th>
            <th className="p-3 text-left">Duration</th>
            <th className="p-3 text-left">Actions</th>
          </tr>
        </thead>
        <tbody>
          {executions.map(exec => (
            <tr key={exec.execution_id} className="border-b hover:bg-gray-50">
              <td className="p-3 font-mono text-sm">{exec.execution_id.slice(0, 8)}...</td>
              <td className="p-3">{exec.strategy_name}</td>
              <td className="p-3">
                <StatusBadge status={exec.status} />
              </td>
              <td className="p-3">{formatDateTime(exec.started_at)}</td>
              <td className="p-3">{exec.duration_ms}ms</td>
              <td className="p-3">
                <Link href={`/executions/${exec.execution_id}`} className="text-blue-600 hover:underline">
                  View Details
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### 9.7 Key UI Features

**1. Strategy Discovery**
- List all available strategies
- Search and filter by name/description
- View parameter requirements before execution

**2. Dynamic Form Generation**
- Auto-generates forms from JSON Schema
- Type-appropriate inputs (number, select, text)
- Client-side validation with Zod
- Helpful descriptions and constraints

**3. Real-time Execution Status**
- Polling for execution status
- Progress indicators
- Error messages with details

**4. Result Visualization**
- Interactive charts (cumulative P&L, drawdown)
- Trade-by-trade breakdown
- Performance metrics dashboard
- Export to CSV/Excel

**5. Execution History**
- View past executions
- Re-run with same parameters (instant via cache)
- Compare multiple executions
- Filter by strategy/date/status

**6. Cache Indicators**
- Visual badge for cached results
- Shows cache hit rate
- Explains why result was instant

---

## 10. SCALABILITY & GROWTH ROADMAP

### 10.1 Phase 1: MVP (Weeks 1-4)

**Goal:** Working system with core functionality

**Deliverables:**
- SQLite database with existing 26GB data
- Data ingestion service (file-level deduplication)
- 2-3 strategies implemented (main1, main1_V7)
- FastAPI execution endpoint
- Basic Flask auth (JWT)
- Simple React frontend (manual forms)
- Parameter caching working

**Infrastructure:**
- Single server (4 CPU, 16GB RAM)
- SQLite database
- No load balancer

**Capacity:**
- 10 concurrent users
- 100 executions/day
- <5s response time (new execution)

### 10.2 Phase 2: Scale (Weeks 5-8)

**Goal:** Production-ready with all strategies

**Deliverables:**
- All 20+ strategies migrated
- Dynamic form generation from JSON Schema
- PostgreSQL migration
- Redis caching layer
- Structured logging (JSON)
- Monitoring dashboard (Grafana)
- CI/CD pipeline

**Infrastructure:**
- 2 API servers (load balanced)
- PostgreSQL (primary + replica)
- Redis cluster
- Nginx load balancer

**Capacity:**
- 50 concurrent users
- 1000 executions/day
- <3s response time (new execution)
- <50ms response time (cached)

### 10.3 Phase 3: Enterprise (Weeks 9-12)

**Goal:** Multi-tenant SaaS platform

**Deliverables:**
- Multi-tenancy support
- Role-based access control (RBAC)
- API rate limiting
- Webhook notifications
- Advanced analytics
- White-label UI options
- Audit logging

**Infrastructure:**
- Auto-scaling API servers (2-10 instances)
- PostgreSQL cluster (HA)
- Redis Sentinel
- CDN for frontend
- S3 for result storage

**Capacity:**
- 500+ concurrent users
- 10,000+ executions/day
- 99.9% uptime SLA

### 9.6 Execution History Page

**Purpose:** Show all past executions with filtering and search capabilities.

**Features:**
- Table view with columns: Execution ID, Strategy Name, Parameters (collapsed), Status, Started At, Duration, Actions
- Filters: Strategy dropdown, Status dropdown (All/Completed/Failed/Running), Date range picker
- Search: By execution ID or parameter values
- Actions per row: View Details, Re-run (same parameters), Export Results
- Pagination: 50 results per page
- Real-time status updates: WebSocket connection for running executions

**Re-run Capability:**
- Click "Re-run" button on any past execution
- Pre-fills form with exact same parameters
- Shows cache status: "This will return cached result (instant)" if parameters unchanged
- Allows parameter modification before execution

### 9.7 Strategy Discovery Page

**Purpose:** Browse all available strategies.

**Layout:**
- Grid of strategy cards
- Each card shows: Name, Version, Description, Last Used, Total Executions
- Click card to navigate to execution page
- Search bar: Filter strategies by name or description
- Sort options: Alphabetical, Most Used, Recently Added

**Auto-Discovery:**
- Frontend fetches strategy list from GET /api/v1/strategies
- No hardcoded strategy names in frontend code
- New strategies appear automatically after backend registration

### 9.8 User Dashboard

**Purpose:** Overview of user activity and quick actions.

**Widgets:**
- Recent Executions: Last 10 executions with status
- Favorite Strategies: Quick access to frequently used strategies
- Statistics: Total executions, Cache hit rate, Total compute time saved
- Quick Execute: Dropdown to select strategy and jump to execution page

---

## 10. SCALABILITY & GROWTH ROADMAP

### 10.1 Phase 1: MVP (Weeks 1-4)

**Goal:** Working system with core functionality.

**Deliverables:**
- Data ingestion service processing 6362 CSV files
- SQLite database with market data
- 2-3 strategies implemented (main1_V7, main1_V8)
- FastAPI execution endpoint
- Basic Flask auth (register/login)
- Simple Next.js UI with dynamic forms
- Parameter caching working

**Constraints:**
- Single server deployment
- SQLite database
- No horizontal scaling
- Basic error handling

**Success Criteria:**
- Process all CSV files in <30 minutes
- Execute strategy in <30 seconds
- Cached results in <50ms
- Support 5 concurrent users

### 10.2 Phase 2: Scale (Weeks 5-8)

**Goal:** Production-ready with monitoring and reliability.

**Enhancements:**
- Migrate SQLite → PostgreSQL
- Add structured logging (JSON format)
- Implement health checks and metrics
- Add rate limiting to APIs
- Implement RBAC (admin/user roles)
- Add execution queue for long-running strategies
- Implement WebSocket for real-time status updates
- Add comprehensive error handling
- Deploy to cloud (AWS/GCP/Azure)

**Infrastructure:**
- Load balancer for API servers
- Separate database server
- Redis for session storage and caching
- Background worker for strategy execution

**Success Criteria:**
- Support 50 concurrent users
- 99.9% uptime
- <100ms API response time (p95)
- Automated deployments

### 10.3 Phase 3: Enterprise (Weeks 9-12)

**Goal:** Multi-tenant SaaS with advanced features.

**Features:**
- Multi-tenancy: Isolated data per organization
- Advanced analytics: Strategy comparison, portfolio optimization
- Scheduled executions: Run strategies on cron schedule
- Alerts and notifications: Email/SMS on execution completion
- API rate limiting per user tier
- Audit logging: Track all user actions
- Data export: Bulk export to CSV/Excel
- Custom strategy upload: Users can upload their own strategies

**Infrastructure:**
- Kubernetes for container orchestration
- Horizontal pod autoscaling
- Database read replicas
- CDN for static assets
- Distributed tracing (Jaeger/Zipkin)
- Centralized logging (ELK stack)

**Success Criteria:**
- Support 500+ concurrent users
- 99.95% uptime
- <50ms API response time (p95)
- Multi-region deployment
