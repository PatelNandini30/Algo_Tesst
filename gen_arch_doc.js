const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, PageNumber, PageBreak, LevelFormat,
  TableOfContents, Bookmark
} = require('docx');
const fs = require('fs');

// ─── Colour palette ─────────────────────────────────────────────────────────
const C = {
  navy:    '1F3864',
  blue:    '2E75B6',
  lightBlue: 'D6E4F0',
  midBlue: 'BDD7EE',
  rust:    'C0504D',
  green:   '375623',
  greenBg: 'E2EFDA',
  gray:    'F2F2F2',
  darkGray:'595959',
  white:   'FFFFFF',
  black:   '000000',
  accent:  '4472C4',
};

// ─── Border helper ───────────────────────────────────────────────────────────
const bdr = (color = 'CCCCCC', size = 4) =>
  ({ style: BorderStyle.SINGLE, size, color });
const cellBorders = (c = 'CCCCCC') =>
  ({ top: bdr(c), bottom: bdr(c), left: bdr(c), right: bdr(c) });

// ─── Cell helper ─────────────────────────────────────────────────────────────
function cell(text, {
  bold = false, fill = C.white, color = C.black,
  w = 2000, align = AlignmentType.LEFT, size = 18, span = 1, vAlign
} = {}) {
  const opts = {
    borders: cellBorders(),
    width: { size: w, type: WidthType.DXA },
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [new Paragraph({
      alignment: align,
      children: [new TextRun({ text: String(text), bold, color, size, font: 'Arial' })],
    })],
  };
  if (fill !== C.white) opts.shading = { fill, type: ShadingType.CLEAR };
  if (span > 1) opts.columnSpan = span;
  if (vAlign) opts.verticalAlign = vAlign;
  return new TableCell(opts);
}

// ─── Paragraph helpers ───────────────────────────────────────────────────────
const h1 = (text, bookmark) => {
  const run = new TextRun({ text, bold: true, color: C.navy, size: 36, font: 'Arial' });
  const children = bookmark ? [new Bookmark({ id: bookmark, children: [run] })] : [run];
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 180 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.blue, space: 4 } },
    children,
  });
};

const h2 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_2,
  spacing: { before: 240, after: 120 },
  children: [new TextRun({ text, bold: true, color: C.blue, size: 26, font: 'Arial' })],
});

const h3 = (text) => new Paragraph({
  heading: HeadingLevel.HEADING_3,
  spacing: { before: 160, after: 80 },
  children: [new TextRun({ text, bold: true, color: C.darkGray, size: 22, font: 'Arial' })],
});

const p = (text, { bold = false, color = C.black, size = 20, italic = false } = {}) =>
  new Paragraph({
    spacing: { before: 60, after: 80 },
    children: [new TextRun({ text, bold, color, size, italic, font: 'Arial' })],
  });

const pb = () => new Paragraph({ children: [new PageBreak()] });

const spacer = () => new Paragraph({ spacing: { before: 80, after: 80 }, children: [new TextRun('')] });

// ─── Bullet helper (must use numbering refs) ─────────────────────────────────
const bullet = (text, { bold = false, level = 0, ref = 'bullets' } = {}) =>
  new Paragraph({
    numbering: { reference: ref, level },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text, bold, size: 20, font: 'Arial', color: C.black })],
  });

// ─── Table header row ────────────────────────────────────────────────────────
const hdrRow = (cols, widths) => new TableRow({
  tableHeader: true,
  children: cols.map((c, i) =>
    cell(c, { bold: true, fill: C.navy, color: C.white, w: widths[i], size: 18 })
  ),
});

const dataRow = (cols, widths, shaded = false) => new TableRow({
  children: cols.map((c, i) =>
    cell(c, { w: widths[i], fill: shaded ? C.gray : C.white, size: 18 })
  ),
});

// ─── Inline code style ───────────────────────────────────────────────────────
const code = (text) => new TextRun({ text, font: 'Courier New', size: 18, color: C.rust });

// ─── Section divider (thick blue paragraph border) ───────────────────────────
const divider = () => new Paragraph({
  spacing: { before: 120, after: 120 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.blue, space: 2 } },
  children: [new TextRun('')],
});

// ═══════════════════════════════════════════════════════════════════════════
// DOCUMENT CONTENT
// ═══════════════════════════════════════════════════════════════════════════
const children = [];

// ─── COVER PAGE ─────────────────────────────────────────────────────────────
children.push(
  new Paragraph({ spacing: { before: 2880, after: 0 }, children: [] }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 240 },
    children: [new TextRun({ text: 'ALGOTEST SOFTWARE', bold: true, size: 56, color: C.navy, font: 'Arial' })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 480 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: C.blue, space: 6 } },
    children: [new TextRun({ text: 'Complete System Architecture Document', size: 32, color: C.blue, font: 'Arial', italic: true })],
  }),
  spacer(),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text: 'End-to-End Technical Reference', size: 24, color: C.darkGray, font: 'Arial' })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 120 },
    children: [new TextRun({ text: 'Version 2.0  |  May 2026', size: 22, color: C.darkGray, font: 'Arial' })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 480, after: 0 },
    children: [new TextRun({ text: 'Covers: FastAPI • Celery • Redis • PostgreSQL • Rust Engine • React Frontend • Docker', size: 20, color: C.darkGray, font: 'Arial', italic: true })],
  }),
  pb()
);

// ─── TABLE OF CONTENTS ───────────────────────────────────────────────────────
children.push(
  h1('Table of Contents'),
  new TableOfContents('Table of Contents', {
    hyperlink: true,
    headingStyleRange: '1-3',
    stylesWithLevels: [{ styleName: 'Heading1', level: 1 }, { styleName: 'Heading2', level: 2 }],
  }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 1 — EXECUTIVE SUMMARY
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('1. Executive Summary', 'sec1'),
  p('AlgoTest is a production-grade, self-hosted options backtesting platform designed to run on commodity hardware (16 GB RAM, spinning HDD). It allows quantitative analysts and strategy researchers to define multi-leg F&O strategies, run historical backtests spanning up to seven years of NSE options data, and sweep thousands of parameter combinations in an automated optimization workflow.'),
  spacer(),
  p('The platform is architected as a multi-process pipeline: a React single-page application talks to a FastAPI gateway, which enqueues work onto Celery queues backed by Redis. Independent worker processes execute backtests using either a Python engine or a compiled Rust extension, store results in Redis, and stream them back to the browser as Apache Arrow IPC payloads.'),
  spacer(),
  h2('1.1 Design Goals'),
  bullet('Run on HDD hardware: all caches, indexes, and concurrency limits are tuned for sequential I/O.'),
  bullet('Sub-second warm-cache backtests: Rust AHashMap cache avoids Polars/Postgres on the hot path.'),
  bullet('Isolation between long and short jobs: two separate Celery queues prevent multi-year runs from blocking 1-month queries.'),
  bullet('Exact rupee-level accuracy: Rust simulator results must match the Python engine trade-for-trade.'),
  bullet('Full Docker deployment: a single docker compose up starts the entire stack.'),
  spacer(),
  h2('1.2 Supported Instruments'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2340, 1560, 2340, 3120],
    rows: [
      hdrRow(['Index', 'Lot Size', 'Strike Interval', 'Expiry Types'], [2340, 1560, 2340, 3120]),
      dataRow(['NIFTY', '50', '50', 'Weekly + Monthly'], [2340, 1560, 2340, 3120], false),
      dataRow(['BANKNIFTY', '15', '100', 'Weekly + Monthly'], [2340, 1560, 2340, 3120], true),
      dataRow(['FINNIFTY', '40', '50', 'Weekly + Monthly'], [2340, 1560, 2340, 3120], false),
      dataRow(['MIDCPNIFTY', '75', '25', 'Weekly + Monthly'], [2340, 1560, 2340, 3120], true),
    ],
  }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 2 — TECHNOLOGY STACK
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('2. Technology Stack', 'sec2'),
  h2('2.1 Backend'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2600, 2200, 4560],
    rows: [
      hdrRow(['Layer', 'Technology', 'Purpose'], [2600, 2200, 4560]),
      dataRow(['Web Framework', 'FastAPI 0.110 + uvloop', 'Async HTTP gateway; ORJSONResponse for speed'], [2600, 2200, 4560], false),
      dataRow(['Task Queue', 'Celery 5 + billiard', 'Background backtest / optimize jobs'], [2600, 2200, 4560], true),
      dataRow(['Message Broker', 'Redis 7', 'Celery broker + result backend + data cache'], [2600, 2200, 4560], false),
      dataRow(['Primary DB', 'PostgreSQL 15', 'Option data, spot prices, expiry calendars'], [2600, 2200, 4560], true),
      dataRow(['Data Processing', 'Polars + PyArrow', 'Vectorised bulk loads from Postgres'], [2600, 2200, 4560], false),
      dataRow(['Native Extension', 'Rust + PyO3 + AHash', 'O(1) market cache; trade simulation'], [2600, 2200, 4560], true),
      dataRow(['Serialisation', 'Apache Arrow IPC', 'Zero-copy tradesheet streaming'], [2600, 2200, 4560], false),
      dataRow(['Intraday API', 'Rust binary (axum)', 'Standalone sub-minute snapshot server'], [2600, 2200, 4560], true),
    ],
  }),
  spacer(),
  h2('2.2 Frontend'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2600, 2200, 4560],
    rows: [
      hdrRow(['Layer', 'Technology', 'Purpose'], [2600, 2200, 4560]),
      dataRow(['UI Framework', 'React 18', 'Component-based SPA'], [2600, 2200, 4560], false),
      dataRow(['Build Tool', 'Vite', 'Fast HMR dev server; production bundle'], [2600, 2200, 4560], true),
      dataRow(['Styling', 'Tailwind CSS', 'Utility-first design system'], [2600, 2200, 4560], false),
      dataRow(['Data Fetching', 'TanStack Query', 'Polling, caching, optimistic updates'], [2600, 2200, 4560], true),
      dataRow(['Arrow Parsing', 'apache-arrow JS', 'Deserialise IPC stream in browser'], [2600, 2200, 4560], false),
      dataRow(['Charts', 'Recharts', 'MAE/MFE histograms, equity curves'], [2600, 2200, 4560], true),
      dataRow(['Static Server', 'nginx (Docker)', 'Serves dist/ bundle on port 3000'], [2600, 2200, 4560], false),
    ],
  }),
  spacer(),
  h2('2.3 Infrastructure'),
  bullet('Container orchestration: Docker Compose (single-host, no Kubernetes).'),
  bullet('Hardware target: 6-core / 12-thread CPU, 16 GB DDR4, 1 TB spinning HDD.'),
  bullet('Monitoring: Prometheus scrapes /metrics; cron-warmup daemon runs nightly vmtouch page-cache pre-load.'),
  bullet('OS page cache exploitation: Arrow feather files are memory-mapped so forked workers share pages via Copy-on-Write.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 3 — HIGH-LEVEL SYSTEM DIAGRAM
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('3. High-Level Architecture', 'sec3'),
  p('The diagram below shows how a backtest request flows through the entire stack from browser to Rust engine and back.'),
  spacer(),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 120, after: 120 },
    children: [new TextRun({ text: '[ BROWSER ]', bold: true, size: 22, font: 'Courier New', color: C.navy })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '       |  HTTP POST /api/backtest', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '       v', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '[ FastAPI :8000 ]  →  Normalise →  Validate →  Enqueue Celery', bold: true, size: 20, font: 'Courier New', color: C.blue })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '                         |  Redis broker', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '           +-------------+-------------+', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '           |                           |', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '  [ worker-backtests ]       [ worker-backtests-fast ]', bold: true, size: 20, font: 'Courier New', color: C.rust })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '  (> 550 days)               (<= 550 days)', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '           |                           |', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '           +---------- algotest_job.py -+', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '                          |', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '         +--------+--------+--------+', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: ' [ Redis  ]  [ Postgres ]  [ Rust Cache ]', bold: true, size: 20, font: 'Courier New', color: C.green })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: '  result      bulk load    simulate.rs', size: 18, font: 'Courier New', color: C.darkGray })],
  }),
  spacer(),
  h2('3.1 Request Lifecycle Summary'),
  bullet('Step 1: Browser sends POST with strategy JSON (legs, dates, index, SL/Target params).'),
  bullet('Step 2: FastAPI normalises date formats, resolves STR filter if active, validates symbol/date range.'),
  bullet('Step 3: Job enqueued to Redis-backed Celery; job_id returned immediately to browser.'),
  bullet('Step 4: Worker dequeues job, checks Redis result cache (cache hit returns immediately).'),
  bullet('Step 5: On miss, worker bulk-loads the date range from Postgres via Polars into RAM.'),
  bullet('Step 6: Rust AHashMap cache built from Arrow feather (integer keys, ~172 MB for 4.3 M rows).'),
  bullet('Step 7: Rust simulate.rs iterates expiry dates, prices legs, applies SL/Target/Trail, computes P&L.'),
  bullet('Step 8: Result tradesheet stored in Redis as msgpack. Arrow IPC bytes returned to API.'),
  bullet('Step 9: Browser polls GET /api/backtest/{id} until COMPLETED, receives Arrow stream, renders table.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 4 — DOCKER SERVICES
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('4. Docker Services & Infrastructure', 'sec4'),
  p('The entire platform runs as a Docker Compose stack. All services share a bridge network. Memory limits and CPU quotas are set conservatively to fit within the 16 GB / 6-core hardware budget.'),
  spacer(),
  h2('4.1 Service Inventory'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2100, 900, 1560, 1560, 3240],
    rows: [
      hdrRow(['Service', 'Port', 'RAM Limit', 'CPU', 'Role'], [2100, 900, 1560, 1560, 3240]),
      dataRow(['postgres', '5432', '3 500 MB', '2.0', 'Primary data store — options, spot, expiry, holidays'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['redis', '6379', '700 MB', '0.5', 'Celery broker + result backend + data caches'], [2100, 900, 1560, 1560, 3240], true),
      dataRow(['backend', '8000', '2 500 MB', '2.0', 'FastAPI API gateway (1 uvicorn worker)'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['worker-backtests', '—', '5 000 MB', '4.0', 'Celery — long backtests (> 550 days)'], [2100, 900, 1560, 1560, 3240], true),
      dataRow(['worker-backtests-fast', '—', '5 000 MB', '2.0', 'Celery — short backtests (<= 550 days)'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['worker-optimize', '—', '6 144 MB', '8.0', 'Celery — parameter sweeps (profile-gated)'], [2100, 900, 1560, 1560, 3240], true),
      dataRow(['worker-uploads', '—', '500 MB', '0.5', 'Celery — CSV import and migrations'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['worker-backtests-intraday', '—', '2 500 MB', '3.0', 'Celery — intraday backtests (concurrency 3)'], [2100, 900, 1560, 1560, 3240], true),
      dataRow(['worker-backtests-intraday-slow', '—', '1 500 MB', '1.5', 'Celery — high-strike-offset intraday jobs'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['frontend', '3000', '200 MB', '0.5', 'nginx serving React SPA bundle'], [2100, 900, 1560, 1560, 3240], true),
      dataRow(['intraday-api', '8001', '512 MB', '4.0', 'Standalone Rust binary — intraday snapshot API'], [2100, 900, 1560, 1560, 3240], false),
      dataRow(['cron-warmup', '—', '50 MB', '0.1', 'Ofelia cron daemon — nightly vmtouch warmup'], [2100, 900, 1560, 1560, 3240], true),
    ],
  }),
  spacer(),
  h2('4.2 Key Environment Variables'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [3200, 2160, 4000],
    rows: [
      hdrRow(['Variable', 'Default', 'Effect'], [3200, 2160, 4000]),
      dataRow(['ENGINE_BACKEND', 'rust', 'Use Rust simulator; fallback to Python if unsupported'], [3200, 2160, 4000], false),
      dataRow(['FAST_LOOKUP_MODE', 'auto', 'auto/rust/python — controls Arrow cache path'], [3200, 2160, 4000], true),
      dataRow(['RUST_CACHE_MAX_MEMORY_MB', '4000', 'Hard cap on Rust AHashMap cache size'], [3200, 2160, 4000], false),
      dataRow(['BULK_LOAD_MAX_MEMORY_MB', '1500', 'Cap per-process Polars bulk load'], [3200, 2160, 4000], true),
      dataRow(['BULK_LOAD_CHUNK_YEARS', '10', 'Split long ranges into N-year slices'], [3200, 2160, 4000], false),
      dataRow(['OPTIMIZE_PARALLELISM', '2', 'billiard forks inside optimizer worker'], [3200, 2160, 4000], true),
      dataRow(['OPTIMIZE_SKIP_MAE_MFE', '1', 'Skip MAE/MFE during sweep; compute on demand'], [3200, 2160, 4000], false),
      dataRow(['BACKTEST_INCLUDE_MAE_MFE', '1', 'Include MAE/MFE columns in tradesheet'], [3200, 2160, 4000], true),
      dataRow(['BACKTEST_FAST_QUEUE_MAX_DAYS', '550', 'Threshold to route to fast vs normal queue'], [3200, 2160, 4000], false),
      dataRow(['ALLOW_CSV_FALLBACK', 'false', 'Fall back to CSV mounts if Postgres missing'], [3200, 2160, 4000], true),
    ],
  }),
  spacer(),
  h2('4.3 Volumes'),
  bullet('pgdata — PostgreSQL data directory (persistent across container restarts).'),
  bullet('redis_data — Redis RDB snapshots (prevents cache loss on restart).'),
  bullet('algo_cache — Shared cache directory mounted into all worker containers.'),
  bullet('  Sub-paths: /data/cache/parquet (Polars Parquet TTL cache), /data/cache/arrow (Rust Arrow feather).'),
  bullet('./cleaned_csvs, ./expiryData, ./strikeData, ./Filter — read-only source data mounts.'),
  spacer(),
  h2('4.4 Startup & Health-Check Sequence'),
  p('Docker Compose dependency order (using depends_on with condition: service_healthy):'),
  bullet('1. postgres + redis start first; health-checked via pg_isready and redis-cli ping.'),
  bullet('2. backend starts after both are healthy; exposes GET /health.'),
  bullet('3. All Celery workers start after backend is healthy.'),
  bullet('4. frontend starts after backend is healthy.'),
  bullet('5. intraday-api starts after redis is healthy (TCP port 8001).'),
  bullet('6. cron-warmup (Ofelia) starts last; schedules nightly vmtouch at 00:30 IST.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 5 — DATABASE DESIGN
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('5. Database Design', 'sec5'),
  p('PostgreSQL 15 is the source-of-truth for all market data, import metadata, backtest results, and optimization run metadata. The schema is defined across seven sequential migration files in backend/migrations/.'),
  spacer(),
  h2('5.1 Migration History'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2600, 6760],
    rows: [
      hdrRow(['Migration File', 'Purpose'], [2600, 6760]),
      dataRow(['001_add_execution_tables.sql', 'Legacy execution order tracking (pre-PostgreSQL era)'], [2600, 6760], false),
      dataRow(['002_create_data_tables.sql', 'Initial option/spot data schema — now superseded by 003'], [2600, 6760], true),
      dataRow(['003_postgres_csv_replacement_schema.sql', 'PRIMARY SCHEMA — complete rewrite replacing CSV-based storage'], [2600, 6760], false),
      dataRow(['004_add_performance_indexes.sql', 'Composite indexes for fast date/symbol/strike lookups'], [2600, 6760], true),
      dataRow(['005_add_performance_indexes.sql', 'Additional covering indexes for futures queries'], [2600, 6760], false),
      dataRow(['006_add_recent_data_index.sql', 'Partial index on recent dates — speeds up max_date queries'], [2600, 6760], true),
      dataRow(['007_intraday_imports.sql', 'Tables for intraday snapshot metadata and manifests'], [2600, 6760], false),
    ],
  }),
  spacer(),
  h2('5.2 Core Tables (Migration 003)'),
  h3('5.2.1 option_data — Primary Fact Table'),
  p('One row per trading day per instrument. Stores OHLC, OI, and turnover for both options and futures in the same table, differentiated by the instrument column.'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 1800, 5160],
    rows: [
      hdrRow(['Column', 'Type', 'Description'], [2400, 1800, 5160]),
      dataRow(['trade_date', 'DATE NOT NULL', 'NSE trading day'], [2400, 1800, 5160], false),
      dataRow(['expiry_date', 'DATE NOT NULL', 'Contract expiry date'], [2400, 1800, 5160], true),
      dataRow(['instrument', 'VARCHAR(10)', 'OPTIDX / OPTSTK / FUTIDX / FUTSTK'], [2400, 1800, 5160], false),
      dataRow(['symbol', 'VARCHAR(20)', 'NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY'], [2400, 1800, 5160], true),
      dataRow(['strike_price', 'NUMERIC(12,2)', 'NULL for futures rows'], [2400, 1800, 5160], false),
      dataRow(['option_type', 'CHAR(2)', 'CE / PE — NULL for futures rows'], [2400, 1800, 5160], true),
      dataRow(['open_price', 'NUMERIC(12,4)', 'Opening price of the session'], [2400, 1800, 5160], false),
      dataRow(['high_price', 'NUMERIC(12,4)', 'Intraday high'], [2400, 1800, 5160], true),
      dataRow(['low_price', 'NUMERIC(12,4)', 'Intraday low'], [2400, 1800, 5160], false),
      dataRow(['close_price', 'NUMERIC(12,4)', 'EOD settlement price (main lookup value)'], [2400, 1800, 5160], true),
      dataRow(['settled_price', 'NUMERIC(12,4)', 'NSE official settlement (expiry day)'], [2400, 1800, 5160], false),
      dataRow(['contracts', 'BIGINT', 'Number of contracts traded'], [2400, 1800, 5160], true),
      dataRow(['turnover', 'NUMERIC(18,2)', 'Turnover in INR — used to detect stale strikes'], [2400, 1800, 5160], false),
      dataRow(['open_interest', 'BIGINT', 'Open interest at end of day'], [2400, 1800, 5160], true),
    ],
  }),
  spacer(),
  p('Unique constraint: (trade_date, symbol, instrument, expiry_date, option_type, strike_price).', { bold: true }),
  spacer(),
  h3('5.2.2 spot_data'),
  p('End-of-day spot OHLC for each index. Also stores pre-computed SuperTrend indicator columns for filter support.'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 1800, 5160],
    rows: [
      hdrRow(['Column', 'Type', 'Description'], [2400, 1800, 5160]),
      dataRow(['trade_date', 'DATE NOT NULL', 'Trading day'], [2400, 1800, 5160], false),
      dataRow(['symbol', 'VARCHAR(20)', 'Index symbol'], [2400, 1800, 5160], true),
      dataRow(['open/high/low/close_price', 'NUMERIC(12,4)', 'OHLC spot prices'], [2400, 1800, 5160], false),
      dataRow(['volume', 'BIGINT', 'Cash market volume proxy'], [2400, 1800, 5160], true),
      dataRow(['supertrend_1/2/3', 'NUMERIC(12,4)', 'Pre-computed SuperTrend values (3 configs)'], [2400, 1800, 5160], false),
      dataRow(['trade_time', 'TIME', 'Snapshot time (used for intraday)'], [2400, 1800, 5160], true),
    ],
  }),
  spacer(),
  h3('5.2.3 expiry_calendar'),
  p('Maps each trading day to its surrounding expiry context: previous, current, and next expiry dates. One row per (symbol, expiry_type, current_expiry).'),
  h3('5.2.4 trading_holidays'),
  p('Date ranges for NSE market holidays. Used to build the trading calendar and skip non-trading days in the engine.'),
  h3('5.2.5 import_batches / import_files'),
  p('Audit tables for CSV data imports. Re-importing a file with the same SHA-256 hash is a no-op — idempotent by design. Tracks row counts, rejection counts, and import status per batch.'),
  spacer(),
  h2('5.3 Indexes'),
  p('All indexes are created in migrations 004-006. Key indexes for the hot backtest path:'),
  bullet('(trade_date, symbol, option_type, expiry_date, strike_price) — primary option lookup.'),
  bullet('(trade_date, symbol, expiry_date) — futures price lookup.'),
  bullet('(symbol, trade_date) — full symbol-range scans.'),
  bullet('Partial index on trade_date DESC for max-date queries (used to clamp the_to_date to available data).'),
  spacer(),
  h2('5.4 PostgreSQL Tuning (HDD-Optimised)'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [3200, 1760, 4400],
    rows: [
      hdrRow(['Parameter', 'Value', 'Rationale'], [3200, 1760, 4400]),
      dataRow(['shared_buffers', '512 MB', 'Conservative; HDD needs smaller shared buffer'], [3200, 1760, 4400], false),
      dataRow(['work_mem', '32 MB', 'Per-sort; avoid temp files on common queries'], [3200, 1760, 4400], true),
      dataRow(['random_page_cost', '4.0', 'Reflects HDD seek time vs SSD 1.1'], [3200, 1760, 4400], false),
      dataRow(['effective_io_concurrency', '2', 'HDD saturates above 2 parallel I/O'], [3200, 1760, 4400], true),
      dataRow(['max_parallel_workers', '2', 'More parallel workers = slower on HDD'], [3200, 1760, 4400], false),
      dataRow(['connection pool', '5 + 5 overflow', 'Managed by SQLAlchemy in database.py'], [3200, 1760, 4400], true),
    ],
  }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 6 — QUEUE & WORKER ARCHITECTURE
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('6. Queue & Worker Architecture', 'sec6'),
  p('AlgoTest uses Celery 5 with Redis as both the message broker and the result backend. The design uses dedicated queues per workload type to prevent priority inversion — a 7-year backtest must not block a 1-week backtest.'),
  spacer(),
  h2('6.1 Queue Topology'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 2400, 4560],
    rows: [
      hdrRow(['Queue Name', 'Consumer Worker', 'Job Types'], [2400, 2400, 4560]),
      dataRow(['backtests', 'worker-backtests', 'Long backtests > 550 calendar days'], [2400, 2400, 4560], false),
      dataRow(['backtests_fast', 'worker-backtests-fast', 'Short backtests <= 550 calendar days'], [2400, 2400, 4560], true),
      dataRow(['optimize', 'worker-optimize', 'Parameter sweep jobs (all sizes)'], [2400, 2400, 4560], false),
      dataRow(['uploads', 'worker-uploads', 'CSV migration, data import'], [2400, 2400, 4560], true),
      dataRow(['backtests_intraday', 'worker-backtests-intraday', 'Intraday jobs with strike offset <= 5'], [2400, 2400, 4560], false),
      dataRow(['backtests_intraday_slow', 'worker-backtests-intraday-slow', 'Intraday jobs with strike offset > 5'], [2400, 2400, 4560], true),
    ],
  }),
  spacer(),
  h2('6.2 Celery Configuration (worker/celery.py)'),
  bullet('Broker and result backend: Redis (redis://redis:6379/0).'),
  bullet('Serialisation: JSON for tasks; msgpack for result payloads (via custom backend).'),
  bullet('Result expiry: 86 400 seconds (24 hours).'),
  bullet('task_time_limit: 1 800 s hard kill; task_soft_time_limit: 1 500 s graceful warning.'),
  bullet('worker_prefetch_multiplier: 1 — prevents workers from hoarding tasks.'),
  bullet('worker_max_tasks_per_child: 200 — respawn after 200 tasks to prevent memory bloat.'),
  bullet('worker_max_memory_per_child: 5 000 000 KB (5 GB) — hard respawn on memory overrun.'),
  bullet('Timezone: Asia/Kolkata — all task timestamps in IST.'),
  spacer(),
  h2('6.3 Task Definitions (worker/tasks.py)'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2800, 1560, 5000],
    rows: [
      hdrRow(['Task', 'Queue', 'Behaviour'], [2800, 1560, 5000]),
      dataRow(['run_algotest_job', 'backtests / fast', 'Main backtest — calls execute_algotest_job(); stores result in Redis'], [2800, 1560, 5000], false),
      dataRow(['run_optimize_job', 'optimize', 'Sweep driver — calls runner.run_optimization(); progress via Redis keys'], [2800, 1560, 5000], true),
      dataRow(['warm_backtest_cache_task', 'backtests', 'Pre-loads bulk data + builds Rust AHashMap cache for next run'], [2800, 1560, 5000], false),
      dataRow(['load_data_task', 'uploads', 'Async data import — inserts CSV rows into Postgres'], [2800, 1560, 5000], true),
      dataRow(['migrate_csv_task', 'uploads', 'Full CSV-to-DB migration with SHA-256 idempotency check'], [2800, 1560, 5000], false),
      dataRow(['execute_intraday_backtest', 'intraday queues', 'Calls Rust native.run_intraday_backtest(); caches Arrow bytes in Redis'], [2800, 1560, 5000], true),
    ],
  }),
  spacer(),
  h2('6.4 Redis Usage Patterns'),
  p('Redis serves five distinct roles in this system:'),
  bullet('1. Celery broker — task queue messages (JSON-encoded task signatures).'),
  bullet('2. Celery result backend — task state (PENDING / STARTED / SUCCESS / FAILURE) + result payload.'),
  bullet('3. Backtest result cache — msgpack-encoded tradesheet; key = Blake2b hash of normalised request.'),
  bullet('4. Bulk-load fragment cache — Polars parquet fragments; key = bulk:{SYMBOL}:{date_range}.'),
  bullet('5. Optimisation progress keys — job_id → {progress: N, total: M, best: {...}} updated live.'),
  spacer(),
  p('Redis eviction policy: maxmemory-policy allkeys-lru. The 700 MB limit means old results are automatically evicted under pressure. TTLs: backtest results 24 h, bulk fragments 1 h.'),
  spacer(),
  h2('6.5 Queue Routing Logic'),
  p('The routing decision happens in routers/backtest.py before Celery enqueue:'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [
      new TextRun({ text: 'days = (to_date − from_date).days', font: 'Courier New', size: 18, color: C.rust }),
    ],
  }),
  new Paragraph({
    spacing: { before: 40, after: 40 },
    children: [
      new TextRun({ text: 'queue = "backtests_fast" if days <= BACKTEST_FAST_QUEUE_MAX_DAYS else "backtests"', font: 'Courier New', size: 18, color: C.rust }),
    ],
  }),
  spacer(),
  p('BACKTEST_FAST_QUEUE_MAX_DAYS defaults to 550 (roughly 1.5 years of calendar days). This ensures the fast worker is never blocked by multi-year runs.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 7 — FASTAPI BACKEND
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('7. FastAPI Backend (backend/)', 'sec7'),
  h2('7.1 Application Entry Point (main.py)'),
  p('The FastAPI application is created in main.py with a lifespan context manager that runs on startup and shutdown.'),
  bullet('Response class: ORJSONResponse — faster JSON serialisation than stdlib.'),
  bullet('Middleware: GZipMiddleware (minimum 1 000 bytes), CORSMiddleware (allow all origins for LAN access).'),
  bullet('Single uvicorn worker with uvloop event loop — not multi-worker; concurrency comes from Celery workers.'),
  bullet('Startup lifespan: calls prebuild_cache.start_background_warmup() to pre-load Rust cache asynchronously.'),
  spacer(),
  h2('7.2 Router Summary'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2000, 1200, 1200, 4960],
    rows: [
      hdrRow(['Endpoint', 'Method', 'Auth', 'Purpose'], [2000, 1200, 1200, 4960]),
      dataRow(['/api/backtest', 'POST', 'None', 'Enqueue backtest; return job_id'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/backtest/{id}', 'GET', 'None', 'Poll job status; return tradesheet on completion'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/backtest/{id}/results', 'GET', 'None', 'Stream Arrow IPC bytes for large results'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/optimize/preview', 'POST', 'None', 'Estimate combo count without running'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/optimize/jobs', 'POST', 'None', 'Enqueue parameter sweep'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/optimize/jobs/{id}', 'GET', 'None', 'Get sweep status + metadata'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/optimize/jobs/{id}/results', 'GET', 'None', 'Paginated/sortable result rows'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/optimize/jobs/{id}', 'DELETE', 'None', 'Cancel sweep + delete results'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/intraday/backtest', 'POST', 'None', 'Run intraday backtest; return Arrow bytes'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/intraday/health', 'GET', 'None', 'Snapshot count, symbols ready, date range'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/data/upload', 'POST', 'None', 'Queue CSV import task'], [2000, 1200, 1200, 4960], false),
      dataRow(['/api/expiry', 'GET', 'None', 'Get expiry dates for index/type'], [2000, 1200, 1200, 4960], true),
      dataRow(['/api/strategies', 'GET', 'None', 'List pre-built strategy templates'], [2000, 1200, 1200, 4960], false),
      dataRow(['/health', 'GET', 'None', 'Liveness check (always 200)'], [2000, 1200, 1200, 4960], true),
      dataRow(['/health/db', 'GET', 'None', 'Postgres pool stats + connectivity'], [2000, 1200, 1200, 4960], false),
    ],
  }),
  spacer(),
  h2('7.3 Request Normalisation Pipeline (routers/backtest.py)'),
  p('Before a backtest request is enqueued it goes through a strict normalisation pipeline:'),
  bullet('Date format detection: accepts dd/MM/yyyy, ddmmyyyy, dd-mm-yyyy, and ISO 8601.'),
  bullet('Date clamping: to_date is clamped to the maximum available date in the database (per-symbol, cached in memory).'),
  bullet('STR filter resolution: if a SuperTrend filter is active, the effective from_date/to_date are narrowed to the matching segment dates.'),
  bullet('Payload deduplication: a Blake2b hash of the normalised payload is computed; if a matching result exists in Redis, the job is satisfied immediately without enqueuing.'),
  spacer(),
  h2('7.4 Backtest Orchestration (services/algotest_job.py)'),
  p('execute_algotest_job() is the central function called by every backtest Celery task. It owns the complete lifecycle:'),
  bullet('1. Check Redis result cache — cache hit returns msgpack bytes without touching DB.'),
  bullet('2. Resolve effective date range (STR filter applied here too for consistency).'),
  bullet('3. Bulk-load options and spot data for the range from Postgres via Polars (services/data_loader.py).'),
  bullet('4. Build fast_lookup cache — converts Polars DataFrame to Rust AHashMap via Arrow feather.'),
  bullet('5. Determine engine path — checks strategy flags against check_strategy_blockers() list; use Rust if all supported else fall back to Python.'),
  bullet('6. Execute simulation — calls engine_rust.run_rust_engine() or generic_algotest_engine.run_algotest_backtest().'),
  bullet('7. Post-process — compute cumulative P&L, NAV series, CAGR (compounded 100-base), Sharpe, Max DD, Win Rate.'),
  bullet('8. Store result — serialise tradesheet + summary to msgpack; write to Redis with 24 h TTL.'),
  bullet('9. Return Arrow IPC bytes to Celery result backend.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 8 — BACKTEST ENGINES
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('8. Backtest Engines', 'sec8'),
  h2('8.1 Python Engine (engines/generic_algotest_engine.py)'),
  p('The Python engine is the reference implementation and the fallback path. It is invoked when the Rust engine does not yet support a strategy feature. It iterates expiry dates sequentially and processes legs one by one.'),
  spacer(),
  h3('8.1.1 Strike Selection Modes'),
  bullet('ATM ± N: strike = round(spot / interval) * interval ± N * interval.'),
  bullet('Percentage of ATM: strike = ATM * (1 + pct_offset / 100).'),
  bullet('Fixed strike: exact value passed by user.'),
  bullet('ITM / OTM depth: N strikes in-the-money or out-of-the-money from ATM.'),
  spacer(),
  h3('8.1.2 Entry / Exit Logic'),
  bullet('Entry DTE: enter N calendar days before expiry.'),
  bullet('Exit DTE: exit M calendar days before expiry (if not triggered by SL/Target first).'),
  bullet('Entry time: configurable HH:MM; price is the day open if entry_time = 09:15.'),
  bullet('Re-entry: after SL hit, optionally re-enter the same leg on the same day at next available price.'),
  spacer(),
  h3('8.1.3 Exit Conditions'),
  bullet('Stop-Loss (SL): exit when leg P&L drops below -SL% of premium received.'),
  bullet('SL with Buffer: SL check uses (low/high) of the option bar, not just close — more realistic gap fill.'),
  bullet('Target: exit when leg P&L exceeds +Target% of premium received.'),
  bullet('Trailing SL: trail the SL upward as P&L improves.'),
  bullet('Overall SL: portfolio-level stop — exit all legs if combined P&L falls below threshold.'),
  spacer(),
  h3('8.1.4 Charges'),
  p('Brokerage follows the Zerodha F&O schedule:'),
  bullet('Brokerage: flat Rs 20 per executed order.'),
  bullet('STT: 0.0625% of premium on sell side.'),
  bullet('NSE transaction charges: 0.053% of turnover.'),
  bullet('GST: 18% of brokerage + exchange charges.'),
  bullet('SEBI charges: Rs 10 per crore of turnover.'),
  bullet('Stamp duty: 0.003% of premium on buy side.'),
  spacer(),
  h2('8.2 Rust Engine (backend/native/)'),
  p('The Rust extension is compiled as a Python wheel (PyO3) and provides two capabilities: a market data cache and a trade simulator. It is loaded at worker startup via import native.'),
  spacer(),
  h3('8.2.1 Market Cache (lib.rs)'),
  p('MarketCache is the central Rust struct. It is populated once per backtest run from an Arrow feather file and lives in process memory.'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2800, 6560],
    rows: [
      hdrRow(['Field', 'Type & Purpose'], [2800, 6560]),
      dataRow(['options', 'AHashMap<(i32,u16,i64,u8,i32), f64> — close prices keyed by (date, sym_id, strike*100, opttype, expiry)'], [2800, 6560], false),
      dataRow(['options_high/low/open', 'Separate AHashMaps — same key, H/L/O prices for SL-with-buffer'], [2800, 6560], true),
      dataRow(['options_settled', 'AHashMap — NSE settlement prices (expiry day only)'], [2800, 6560], false),
      dataRow(['spot', 'AHashMap<(i32,u16), f64> — spot close by (date, sym_id)'], [2800, 6560], true),
      dataRow(['strikes', 'AHashMap<(i32,u16,i32,u8), Vec<(f64,f64)>> — available (strike, close) pairs by date/expiry/opttype'], [2800, 6560], false),
      dataRow(['untradeable', 'AHashSet — keys with zero turnover; excluded from simulation'], [2800, 6560], true),
      dataRow(['symbol_ids', 'AHashMap<String, u16> — intern symbols to 16-bit ID for compact keys'], [2800, 6560], false),
    ],
  }),
  spacer(),
  p('Why integer keys? A (String, String, String, String, i32, i32) key takes ~104 bytes; a (i32,u16,i64,u8,i32) key takes ~20 bytes. For 4.3 M rows this saves ~360 MB of RSS — the difference between fitting in RAM and triggering HDD swap.', { italic: true }),
  spacer(),
  h3('8.2.2 Trade Simulator (simulate.rs)'),
  p('resolve_trade_specs() generates all trade entry points (one per expiry date in the range). simulate_trades_batch() prices each trade to completion. Key steps inside simulate_trades_batch:'),
  bullet('Entry pricing: close_price(entry_date, strike) adjusted by apply_slippage(side, position_type).'),
  bullet('Daily P&L scan: iterate trading days from entry_date to expiry_date.'),
  bullet('SL/Target check: check_leg_stop_loss_target() compares running P&L against thresholds; returns early on trigger.'),
  bullet('SL with Buffer: uses high/low prices to detect intraday threshold breach more accurately.'),
  bullet('Exit pricing: close on exit_dte or expiry day settled price.'),
  bullet('Rollover: after expiry/SL, if rollover flag set — insert a replacement leg for next expiry.'),
  bullet('MAE/MFE: track maximum adverse / maximum favourable excursion per leg.'),
  spacer(),
  h3('8.2.3 Python-Rust Bridge (engine_rust.py)'),
  p('engine_rust.py orchestrates the Python-Rust handoff:'),
  bullet('check_strategy_blockers(payload) — returns list of unsupported flags; non-empty = Python fallback.'),
  bullet('build_rust_cache(symbol, from_date, to_date) — calls native.load_feather() with the Arrow file path.'),
  bullet('run_rust_engine(payload, cache) — calls native.resolve_trade_specs() then native.simulate_trades_batch().'),
  bullet('If native raises RuntimeError, logs and falls back to Python engine transparently.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 9 — CACHING ARCHITECTURE
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('9. Caching Architecture', 'sec9'),
  p('The system uses a four-tier cache hierarchy, each tier serving a different latency/capacity tradeoff.'),
  spacer(),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [1560, 2000, 2000, 3800],
    rows: [
      hdrRow(['Tier', 'Store', 'TTL / Size', 'What Is Stored'], [1560, 2000, 2000, 3800]),
      dataRow(['L1', 'Rust AHashMap (process RAM)', 'Lifetime of worker process', 'Full option/spot OHLC for date range; O(1) lookup'], [1560, 2000, 2000, 3800], false),
      dataRow(['L2', 'Arrow Feather on disk', 'Persistent; invalidated by mtime/size', 'Compressed columnar OHLC (~275 MB for 2019-2026)'], [1560, 2000, 2000, 3800], true),
      dataRow(['L3', 'Parquet on disk (algo_cache)', '30-day TTL', 'Chunked Polars DataFrames per symbol/year'], [1560, 2000, 2000, 3800], false),
      dataRow(['L4', 'Redis', '24 h for results; 1 h for bulk fragments', 'Tradesheet results (msgpack), bulk data fragments'], [1560, 2000, 2000, 3800], true),
    ],
  }),
  spacer(),
  h2('9.1 Rust AHashMap Cache (L1)'),
  p('Built from the Arrow feather file on the first backtest of a worker process lifetime. Subsequent backtests within the same process reuse it. The cache is valid as long as the feather file mtime and size match the values recorded at build time — if the file is replaced (e.g., after a data import), the cache is rebuilt automatically.'),
  spacer(),
  p('Memory guard: RUST_CACHE_MAX_MEMORY_MB (default 4 000 MB) limits how much of the feather is loaded. If the feather exceeds this, only the most recent N years are loaded.'),
  spacer(),
  h2('9.2 Arrow Feather Cache (L2)'),
  p('Stored at ALGO_RUST_CACHE_DIR=/data/cache/arrow. Written by rust_fast_path.py after a bulk Postgres load. Memory-mapped at runtime — the OS page cache is shared across forked worker processes (CoW semantics), so 4 Celery workers sharing the same feather use far less total RAM than 4x the individual file size.'),
  spacer(),
  h2('9.3 Parquet Cache (L3)'),
  p('Stored at PARQUET_CACHE_DIR=/data/cache/parquet. Polars DataFrames are serialised to Parquet after being loaded from Postgres for the first time. Subsequent loads for the same symbol/date-range read from Parquet (much faster than Postgres on HDD). TTL: 30 days.'),
  spacer(),
  h2('9.4 Redis Result Cache (L4)'),
  p('Backtest results are stored in Redis with a key derived from the Blake2b hash of the normalised request payload. This means identical requests (same strategy, same dates, same params) are served directly from Redis without touching any compute path. The hash covers all fields that affect the result, so changing any parameter generates a new cache key.'),
  spacer(),
  h2('9.5 Cache Invalidation Rules'),
  bullet('Feather file: invalidated when file mtime or size changes after a data import.'),
  bullet('Parquet: TTL-based expiry (30 days). Also invalidated by data imports for the affected symbol/date range.'),
  bullet('Redis results: TTL 24 h. Also cleared explicitly by DELETE /api/optimize/jobs/{id} for optimization results.'),
  bullet('Bulk fragment (Redis): TTL 1 h; used only during a multi-chunk bulk load session.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 10 — OPTIMISER
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('10. Parameter Optimiser', 'sec10'),
  p('The optimiser allows users to define ranges for strategy parameters (e.g., SL from 20% to 60% in steps of 5%) and exhaustively or heuristically search the combination space ranked by a chosen objective metric.'),
  spacer(),
  h2('10.1 Optimiser Workflow'),
  bullet('1. User defines base_payload (full backtest params) + param_specs (list of {key, range, step}).'),
  bullet('2. POST /api/optimize/jobs enqueues run_optimize_job Celery task.'),
  bullet('3. param_expander.py expands specs into full combo grid (or samples N combos for evolutionary methods).'),
  bullet('4. runner.py loops combos; each combo merges into base_payload and calls execute_algotest_job().'),
  bullet('5. metrics.py computes objective value (Sharpe, total_pnl, win_rate, etc.) for each result.'),
  bullet('6. result_store.py persists ranked results to Redis + disk as the job progresses.'),
  bullet('7. Frontend polls GET /api/optimize/jobs/{id} for progress; displays live leaderboard.'),
  bullet('8. On completion, user downloads Excel report via excel_builder.py.'),
  spacer(),
  h2('10.2 Sampling Methods'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2000, 7360],
    rows: [
      hdrRow(['Method', 'Description'], [2000, 7360]),
      dataRow(['exhaustive', 'Try every combination in the Cartesian product of param ranges'], [2000, 7360], false),
      dataRow(['random', 'Randomly sample N combinations without replacement'], [2000, 7360], true),
      dataRow(['PSO', 'Particle Swarm Optimisation — swarm-based search with momentum'], [2000, 7360], false),
      dataRow(['GA', 'Genetic Algorithm — selection, crossover, mutation over generations'], [2000, 7360], true),
      dataRow(['CMA-ES', 'Covariance Matrix Adaptation ES — gradient-free Gaussian search'], [2000, 7360], false),
    ],
  }),
  spacer(),
  h2('10.3 Objective Metrics (optimizer/metrics.py)'),
  bullet('total_pnl — Sum of all trade P&Ls (₹).'),
  bullet('win_rate — Percentage of winning trades.'),
  bullet('sharpe — Annualised Sharpe ratio (daily P&L series, risk-free = 6%).'),
  bullet('max_drawdown — Maximum peak-to-trough NAV decline (%).'),
  bullet('avg_pnl_per_trade — Mean P&L per expiry.'),
  bullet('mae — Mean Maximum Adverse Excursion across all trades.'),
  bullet('mfe — Mean Maximum Favourable Excursion across all trades.'),
  bullet('profit_factor — Gross profit / gross loss.'),
  spacer(),
  h2('10.4 Parallelism Inside Optimiser'),
  p('The worker-optimize container runs a single Celery slot (concurrency=1) but uses billiard (the Celery-safe fork of multiprocessing) to run OPTIMIZE_PARALLELISM independent backtest processes in parallel. Each forked process inherits the parent\'s memory-mapped feather file (CoW) and performs its own simulate_trades_batch() call. Results are collected via a shared result queue and merged by the parent.'),
  spacer(),
  h2('10.5 Result Store (optimizer/result_store.py)'),
  bullet('Persists top-K results to Redis hash: optimize:{job_id}:results.'),
  bullet('Stores full combos to disk as gzip-compressed JSON for large sweeps.'),
  bullet('Pagination: GET /api/optimize/jobs/{id}/results?page=N&sort=objective_value.'),
  bullet('Excel export: excel_builder.py generates a multi-sheet workbook (results, pivot, summary stats, per-combo tradesheet if requested).'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 11 — DATA LOADING PIPELINE
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('11. Data Loading Pipeline', 'sec11'),
  p('Data enters the system either through the CSV import pipeline (historical bulk loads) or real-time updates. Once in Postgres it flows through a hierarchy of caches before reaching the Rust simulator.'),
  spacer(),
  h2('11.1 CSV Import Pipeline'),
  bullet('1. User uploads CSV via POST /api/data/upload with data_type (option_data, spot_data, etc.).'),
  bullet('2. Backend enqueues migrate_csv_task on the uploads queue.'),
  bullet('3. Worker: compute SHA-256 of file; check import_files table — if hash exists, skip (idempotent).'),
  bullet('4. Parse CSV with Polars (detect column format via scripts/csv_schema_discovery.py).'),
  bullet('5. Validate rows: reject rows with NULL trade_date, invalid instrument codes, or illogical prices.'),
  bullet('6. Bulk insert to Postgres via COPY protocol (psycopg2 copy_from).'),
  bullet('7. Update import_batches / import_files audit tables.'),
  bullet('8. Invalidate related Parquet and Arrow caches for the affected symbol/date range.'),
  spacer(),
  h2('11.2 Bulk Load Path (services/data_loader.py)'),
  p('bulk_load_options() is the primary data access function called at the start of every backtest. It uses Polars for vectorised loading:'),
  bullet('1. Build Polars SQL query for the symbol/date range.'),
  bullet('2. Check Parquet cache (L3) — if hit, read directly from disk.'),
  bullet('3. On cache miss, execute Polars read_database() against Postgres.'),
  bullet('4. If range > BULK_LOAD_CHUNK_YEARS years, split into N chunks and load sequentially.'),
  bullet('5. Serialise loaded DataFrame to Parquet for future cache hits.'),
  bullet('6. Return Polars DataFrame to caller for Rust feather conversion.'),
  spacer(),
  h2('11.3 Fast Lookup Conversion (services/fast_lookup.py)'),
  p('Once a Polars DataFrame is loaded, it must be converted to a Rust-accessible format:'),
  bullet('1. Estimate if dataset is large enough to justify Rust cache (size threshold check).'),
  bullet('2. Convert Polars DataFrame to Arrow Table via PyArrow bridge.'),
  bullet('3. Write Arrow Table to feather file at ALGO_RUST_CACHE_DIR.'),
  bullet('4. Call native.load_feather(path) — Rust memory-maps the file and populates MarketCache AHashMap.'),
  bullet('5. Record feather file mtime + size for cache invalidation tracking.'),
  spacer(),
  h2('11.4 Data Source Fallback Logic'),
  p('get_data_source() checks the USE_POSTGRESQL environment variable. If false (or Postgres is unavailable) and ALLOW_CSV_FALLBACK is true, the system reads from the mounted CSV directories:'),
  bullet('cleaned_csvs/ — option and futures OHLC (YYYY-MM-DD.csv format, one file per trading day).'),
  bullet('strikeData/ — spot price CSV files per symbol.'),
  bullet('expiryData/ — expiry date calendar files.'),
  bullet('Filter/ — trading holiday files.'),
  p('CSV fallback is read-only. Imports always target Postgres.', { italic: true, bold: true }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 12 — INTRADAY ARCHITECTURE
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('12. Intraday Architecture', 'sec12'),
  p('Intraday backtests operate on sub-minute option chain snapshots rather than end-of-day data. This requires a separate data format, a dedicated Rust binary, and separate Celery queues.'),
  spacer(),
  h2('12.1 Intraday Data Format'),
  bullet('Snapshots stored as Apache Arrow IPC files, one per (symbol, date).'),
  bullet('Each snapshot contains: timestamp, strike, option_type, open/high/low/close/oi columns.'),
  bullet('Manifest files (intraday_manifest.py) track which symbol/date combinations are available.'),
  bullet('Stored under a configured snapshot directory (mounted via Docker volume).'),
  spacer(),
  h2('12.2 Intraday API Server (port 8001)'),
  p('A standalone Rust binary (built from backend/intraday_server/) runs as the intraday-api service. It is independent of the Python FastAPI process:'),
  bullet('Uses axum web framework with tokio async runtime.'),
  bullet('Reads Arrow snapshot files directly from disk (no Postgres dependency).'),
  bullet('Exposes health endpoint and snapshot query endpoints.'),
  bullet('Communicates results back to Python via shared Arrow IPC bytes over HTTP.'),
  spacer(),
  h2('12.3 Intraday Backtest Flow'),
  bullet('1. Browser POST /api/intraday/backtest with IntradayBacktestRequest (symbol, dates, legs, entry_time, square_off_time).'),
  bullet('2. FastAPI computes Blake2b hash of request; checks Redis cache.'),
  bullet('3. Routing: requires_slow_path() checks |ATM_offset| > 5 — slow path uses wider strike chain lookups.'),
  bullet('4. Task enqueued to backtests_intraday or backtests_intraday_slow queue.'),
  bullet('5. Worker calls native.run_intraday_backtest(config_json, data_dir).'),
  bullet('6. Rust intraday/engine.rs: load snapshot Arrow files for date range; iterate timestamps; resolve strikes at entry_time; simulate SL/Target/Trail; square off at square_off_time.'),
  bullet('7. Result returned as Arrow IPC bytes; stored in Redis; returned to browser.'),
  spacer(),
  h2('12.4 Slow Path vs Fast Path'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 3480, 3480],
    rows: [
      hdrRow(['Dimension', 'Fast Path', 'Slow Path'], [2400, 3480, 3480]),
      dataRow(['Strike Offset', '|ATM ± N| <= 5', '|ATM ± N| > 5'], [2400, 3480, 3480], false),
      dataRow(['Queue', 'backtests_intraday', 'backtests_intraday_slow'], [2400, 3480, 3480], true),
      dataRow(['Worker Memory', '2 500 MB', '1 500 MB (lower parallelism)'], [2400, 3480, 3480], false),
      dataRow(['Chain Width', 'Narrow (fewer strikes)', 'Wide (many strikes scanned)'], [2400, 3480, 3480], true),
      dataRow(['Concurrency', '3 parallel jobs', '1 parallel job'], [2400, 3480, 3480], false),
    ],
  }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 13 — FRONTEND ARCHITECTURE
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('13. Frontend Architecture', 'sec13'),
  p('The frontend is a React 18 SPA built with Vite. In production it is compiled to a static bundle in frontend/dist/ and served by nginx on port 3000. The nginx container proxies /api/* requests to the FastAPI backend on port 8000.'),
  spacer(),
  h2('13.1 Component Tree'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2800, 6560],
    rows: [
      hdrRow(['Component', 'Purpose'], [2800, 6560]),
      dataRow(['App.jsx', 'Root — mode switch (backtest / optimize / intraday / upload)'], [2800, 6560], false),
      dataRow(['StrategyBuilder.jsx', 'Main form: index, date range, leg builder, SL/Target/Trail config'], [2800, 6560], true),
      dataRow(['ResultsPanel.jsx', 'Tradesheet display, summary stats, MAE/MFE charts, downloads'], [2800, 6560], false),
      dataRow(['OptimizePanel.jsx', 'Sweep param specs, method selector, objective, preview combo count'], [2800, 6560], true),
      dataRow(['OptimizationResults.jsx', 'Paginated result table, sortable columns, Excel download'], [2800, 6560], false),
      dataRow(['IntradayFields.jsx', 'Intraday-specific inputs: entry_time, square_off_time'], [2800, 6560], true),
      dataRow(['IntradaySlowPathWarning.jsx', 'Alert banner when strike offset triggers slow path'], [2800, 6560], false),
      dataRow(['CsvUpload.jsx', 'File upload form for data imports'], [2800, 6560], true),
      dataRow(['SuperTrendFilter.jsx', 'STR filter configuration dropdown'], [2800, 6560], false),
      dataRow(['ui/CalendarPicker.jsx', 'Custom date range picker (dd/MM/yyyy format)'], [2800, 6560], true),
      dataRow(['ui/TimeInput.jsx', 'HH:MM time picker for entry/exit times'], [2800, 6560], false),
      dataRow(['ui/Toggle.jsx', 'Binary toggle for boolean strategy flags'], [2800, 6560], true),
    ],
  }),
  spacer(),
  h2('13.2 State Management'),
  bullet('Local React state (useState) for form fields and UI flags — no Redux.'),
  bullet('TanStack Query (useQuery / useMutation) for all async server state.'),
  bullet('Polling: GET /api/backtest/{id} polled every 1 second while status == PENDING or STARTED.'),
  bullet('localStorage: strategy builder state persisted so refreshing the page restores the last strategy.'),
  spacer(),
  h2('13.3 Arrow IPC Rendering'),
  p('Large tradesheet results are streamed as Apache Arrow IPC bytes from the backend. The frontend uses the apache-arrow JS library to deserialise the binary stream into an in-memory Table object, which is then mapped to React table rows. This avoids the overhead of JSON serialisation for thousands of rows.'),
  spacer(),
  h2('13.4 nginx Reverse Proxy'),
  p('The nginx configuration in frontend/Dockerfile:'),
  bullet('Serves frontend/dist/ as the static root at /.'),
  bullet('Proxies /api/* to http://backend:8000.'),
  bullet('Enables gzip compression for all text content types.'),
  bullet('Sets cache-control headers: HTML files no-cache; JS/CSS assets with content hashes get 1-year max-age.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 14 — MEMORY MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('14. Memory Management', 'sec14'),
  p('The 16 GB constraint is a hard engineering boundary. Every architectural decision in the worker/cache layer traces back to keeping total RSS within this budget.'),
  spacer(),
  h2('14.1 Memory Budget (Default Profile)'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [3200, 1760, 4400],
    rows: [
      hdrRow(['Component', 'RAM Limit', 'Notes'], [3200, 1760, 4400]),
      dataRow(['postgres', '3 500 MB', 'shared_buffers 512 MB; rest is OS page cache'], [3200, 1760, 4400], false),
      dataRow(['redis', '700 MB', 'maxmemory 500 MB + OS overhead'], [3200, 1760, 4400], true),
      dataRow(['backend (FastAPI)', '2 500 MB', 'Includes Rust cache for warm-path pre-load'], [3200, 1760, 4400], false),
      dataRow(['worker-backtests', '5 000 MB', 'Bulk Polars load + Rust AHashMap + tradesheet'], [3200, 1760, 4400], true),
      dataRow(['worker-backtests-fast', '5 000 MB', 'Same as above; runs in parallel'], [3200, 1760, 4400], false),
      dataRow(['worker-uploads', '500 MB', 'CSV parse + insert batches'], [3200, 1760, 4400], true),
      dataRow(['worker-intraday', '2 500 MB', 'Arrow snapshots + simulation'], [3200, 1760, 4400], false),
      dataRow(['frontend + intraday-api', '712 MB', 'nginx + Rust binary'], [3200, 1760, 4400], true),
      dataRow(['OS + kernel', '~1 500 MB', 'HDD page cache buffer headroom'], [3200, 1760, 4400], false),
      dataRow(['TOTAL', '~22 GB limits', 'Overcommit is safe; limits are ceilings not allocations'], [3200, 1760, 4400], true),
    ],
  }),
  spacer(),
  p('Note: Docker memory limits are soft ceilings, not reservations. Actual RSS for idle workers is much lower. The key constraint is that two concurrent long backtests must not exceed 16 GB combined — hence the 5 GB limit per worker and CoW feather sharing.', { italic: true }),
  spacer(),
  h2('14.2 CoW Feather Sharing'),
  p('When a Celery worker processes fork (billiard), each child inherits the parent\'s memory maps via Linux Copy-on-Write. The Arrow feather (~275 MB for 2019-2026) is mapped once in the parent and shared across all forked children. Pages are only copied when a child writes to them — simulation is read-only with respect to the feather, so in practice zero pages are copied. This makes parallelism within the optimizer essentially free in terms of feather memory.'),
  spacer(),
  h2('14.3 Bulk Load Chunking'),
  p('A 7-year backtest would require loading ~2 GB of Polars data in a single Postgres query. BULK_LOAD_CHUNK_YEARS=10 splits the load into sequential chunks of at most 10 years, each capped at BULK_LOAD_MAX_MEMORY_MB=1500 MB. The Rust cache is rebuilt incrementally as each chunk is processed and then discarded before the next chunk loads.'),
  spacer(),
  h2('14.4 Rust Cache Memory Guard'),
  p('RUST_CACHE_MAX_MEMORY_MB=4000 acts as a hard cap. Before loading a feather file, the Rust layer estimates the required memory from the file size. If estimated_mb > limit, the oldest N years are excluded until the estimate fits. This prevents OOM kills on very long date ranges.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 15 — ANALYTICS & METRICS
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('15. Analytics & Metrics Computation', 'sec15'),
  p('After the engine returns the raw tradesheet, a post-processing pass computes all summary statistics. This runs in the worker process and is serialised into the Redis result alongside the tradesheet rows.'),
  spacer(),
  h2('15.1 Tradesheet Columns'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 6960],
    rows: [
      hdrRow(['Column', 'Description'], [2400, 6960]),
      dataRow(['entry_date / exit_date', 'Calendar dates of trade entry and exit'], [2400, 6960], false),
      dataRow(['entry_time / exit_time', 'HH:MM (relevant for intraday)'], [2400, 6960], true),
      dataRow(['symbol / expiry', 'Index and expiry date for the trade'], [2400, 6960], false),
      dataRow(['strike / option_type', 'Strike price (CE/PE) or FUTURES'], [2400, 6960], true),
      dataRow(['leg_action', 'BUY or SELL'], [2400, 6960], false),
      dataRow(['entry_price / exit_price', 'Slippage-adjusted prices (₹ per lot)'], [2400, 6960], true),
      dataRow(['lots', 'Number of lots traded'], [2400, 6960], false),
      dataRow(['pnl', 'Realised P&L for this leg (₹)'], [2400, 6960], true),
      dataRow(['charges', 'Brokerage + STT + GST + SEBI (₹)'], [2400, 6960], false),
      dataRow(['net_pnl', 'pnl minus charges (₹)'], [2400, 6960], true),
      dataRow(['exit_reason', 'SL / TARGET / EXPIRY / TRAIL / OVERALL_SL'], [2400, 6960], false),
      dataRow(['mae / mfe', 'Maximum adverse / favourable excursion (₹) if BACKTEST_INCLUDE_MAE_MFE=1'], [2400, 6960], true),
      dataRow(['cumulative_pnl', 'Running sum of net_pnl up to this trade'], [2400, 6960], false),
    ],
  }),
  spacer(),
  h2('15.2 Summary Metrics'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 6960],
    rows: [
      hdrRow(['Metric', 'Formula / Notes'], [2400, 6960]),
      dataRow(['Total P&L', 'Sum of all net_pnl values'], [2400, 6960], false),
      dataRow(['CAGR', 'Compounded: (NAV_final / 100) ^ (1 / years) - 1; 100-base NAV'], [2400, 6960], true),
      dataRow(['Win Rate', '(winning expiries / total expiries) x 100 %'], [2400, 6960], false),
      dataRow(['Sharpe Ratio', 'mean(daily_pnl) / std(daily_pnl) x sqrt(252) — annualised'], [2400, 6960], true),
      dataRow(['Max Drawdown', 'Peak-to-trough NAV decline (%) over entire history'], [2400, 6960], false),
      dataRow(['Average Trade P&L', 'Total P&L / number of expiry cycles'], [2400, 6960], true),
      dataRow(['Profit Factor', 'Sum(winning trades) / abs(Sum(losing trades))'], [2400, 6960], false),
      dataRow(['Live Max DD', 'Max DD on the live (post-sample) portion of the data'], [2400, 6960], true),
    ],
  }),
  spacer(),
  h2('15.3 CAGR Formula'),
  p('The research-team-approved CAGR formula uses a 100-base NAV (starts at 100, grows proportionally with cumulative P&L). The formula is:'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: 'CAGR = (NAV_final / 100) ^ (1 / years) - 1', font: 'Courier New', size: 22, bold: true, color: C.navy })],
  }),
  p('Where years is the actual calendar duration of the backtest (not trading days). This matches the Summary Sample.xlsx produced by the research team. The ₹-base CAGR formula previously used in base.py was incorrect and has been replaced.', { italic: true }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 16 — SECURITY & ACCESS CONTROL
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('16. Security & Access Control', 'sec16'),
  p('AlgoTest is a LAN-only tool with no external-facing endpoints. There is no user authentication layer by design — all users on the internal network have full access.'),
  spacer(),
  h2('16.1 Network Boundaries'),
  bullet('Docker Compose bridge network: all inter-service communication is internal (no external exposure except ports 3000 and 8000/8001 on the host NIC).'),
  bullet('Postgres and Redis ports (5432, 6379) are not published to the host interface — only reachable within the Docker network.'),
  bullet('Frontend nginx handles all user-facing traffic on port 3000; backend is not directly accessible except via nginx proxy.'),
  spacer(),
  h2('16.2 Input Validation'),
  bullet('FastAPI Pydantic models validate all request payloads — type coercion, range checks, enum validation.'),
  bullet('Date normalisation pipeline rejects invalid dates and clamps future dates to DB max.'),
  bullet('File uploads: data_type is validated against a whitelist (option_data, spot_data, expiry_calendar, trading_holidays).'),
  bullet('CSV import: rows with NULL trade_date, invalid instrument codes, or negative prices are rejected and logged.'),
  spacer(),
  h2('16.3 Resource Limits'),
  bullet('Docker memory limits prevent any single service from consuming the full 16 GB.'),
  bullet('Celery task_time_limit: 1 800 s — no infinite-running tasks.'),
  bullet('Redis maxmemory: 500 MB — prevents Redis from consuming all RAM.'),
  bullet('PostgreSQL statement_timeout: 1 800 000 ms — no infinite-running queries.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 17 — PERFORMANCE BENCHMARKS
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('17. Performance Characteristics', 'sec17'),
  p('Measured on the HP 280 Pro G6 hardware target (6C/12T, 16 GB DDR4, 1 TB HDD).'),
  spacer(),
  h2('17.1 Backtest Latency'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [3200, 2080, 2080, 2000],
    rows: [
      hdrRow(['Scenario', 'Cache State', 'Engine', 'Latency'], [3200, 2080, 2080, 2000]),
      dataRow(['1-month NIFTY, 1 leg', 'Redis hit', 'N/A (cache)', '< 100 ms'], [3200, 2080, 2080, 2000], false),
      dataRow(['1-month NIFTY, 1 leg', 'Rust cache warm', 'Rust', '~300 ms'], [3200, 2080, 2080, 2000], true),
      dataRow(['1-year NIFTY, 2 legs', 'Rust cache warm', 'Rust', '~1-2 s'], [3200, 2080, 2080, 2000], false),
      dataRow(['5-year NIFTY, 2 legs', 'Cold (Polars + DB)', 'Rust', '~15-30 s'], [3200, 2080, 2080, 2000], true),
      dataRow(['7-year NIFTY, 2 legs', 'Cold (chunked)', 'Rust', '~40-60 s'], [3200, 2080, 2080, 2000], false),
      dataRow(['1-year NIFTY, 2 legs', 'Cold', 'Python fallback', '~8-15 s'], [3200, 2080, 2080, 2000], true),
    ],
  }),
  spacer(),
  h2('17.2 Optimiser Throughput'),
  bullet('Rust engine + Parquet cache warm: ~80-120 combos/minute for 1-year NIFTY 2-leg strategies.'),
  bullet('OPTIMIZE_PARALLELISM=2 approximately doubles throughput via billiard CoW fork.'),
  bullet('MAE/MFE disabled (OPTIMIZE_SKIP_MAE_MFE=1) saves ~15% computation time per combo.'),
  spacer(),
  h2('17.3 Cache Hit Rates (Steady State)'),
  bullet('Redis result cache: > 90% hit rate for repeated research workflows (same strategy, date range).'),
  bullet('Parquet cache: > 95% hit rate after first cold load of a symbol/year range.'),
  bullet('Rust AHashMap: 100% after first backtest of a worker process (feather persists across tasks).'),
  spacer(),
  h2('17.4 HDD I/O Optimisations'),
  bullet('vmtouch nightly warmup (00:30 IST): pre-loads Arrow feather and frequently-accessed Parquet files into OS page cache.'),
  bullet('Arrow feather is sequentially read in a single mmap() call — optimal for spinning disk (no random seeks).'),
  bullet('Polars read_database() with Postgres uses a single sequential scan per query (no random index seeks if range is full-symbol).'),
  bullet('Parquet files are written with snappy compression — good compression ratio with fast decompression.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 18 — DEPLOYMENT OPERATIONS
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('18. Deployment & Operations', 'sec18'),
  h2('18.1 Starting the Stack'),
  p('The recommended startup procedure uses start.sh which handles port conflicts and system service interference:'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: './start.sh', font: 'Courier New', size: 20, bold: true, color: C.rust })],
  }),
  p('Alternatively, use Docker Compose directly:'),
  new Paragraph({
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text: 'docker compose up -d --build', font: 'Courier New', size: 18, color: C.rust })],
  }),
  spacer(),
  p('start.sh additionally: kills system postgres/redis if running on conflicting ports; waits for health checks before returning; tails logs from backend and worker-backtests.'),
  spacer(),
  h2('18.2 Running Migrations'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: 'docker compose exec -T postgres psql -U algotest -d algotest < backend/migrations/003_postgres_csv_replacement_schema.sql', font: 'Courier New', size: 16, color: C.rust })],
  }),
  p('Migrations are plain SQL and idempotent (CREATE TABLE IF NOT EXISTS). Run them in numeric order (001 → 007).'),
  spacer(),
  h2('18.3 Running Tests'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: 'python -m unittest discover backend/tests', font: 'Courier New', size: 18, color: C.rust })],
  }),
  p('Tests use Python unittest (not pytest). Key test files:'),
  bullet('test_fast_lookup_golden.py — verifies Rust and Python engines return identical trade P&Ls.'),
  bullet('test_resolve_leg_exit.py — unit tests for SL/Target/Trail exit logic.'),
  bullet('test_optim_metrics.py — unit tests for optimiser objective calculations.'),
  spacer(),
  h2('18.4 Enabling the Optimizer Profile'),
  p('The worker-optimize service is gated behind a Docker Compose profile to avoid consuming its 6 GB RAM by default:'),
  new Paragraph({
    spacing: { before: 80, after: 80 },
    children: [new TextRun({ text: 'docker compose --profile optimize up -d', font: 'Courier New', size: 18, color: C.rust })],
  }),
  spacer(),
  h2('18.5 Cache Warming'),
  bullet('Manual: POST /api/backtest with warm=true flag — triggers warm_backtest_cache_task.'),
  bullet('Automatic nightly: Ofelia cron (cron-warmup container) runs vmtouch warmup script at 00:30 IST.'),
  bullet('Docker volume algo_cache persists feather and Parquet files across container restarts — do NOT run docker compose down -v without intending to lose the cache.'),
  spacer(),
  h2('18.6 Monitoring Endpoints'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2800, 6560],
    rows: [
      hdrRow(['Endpoint', 'What It Returns'], [2800, 6560]),
      dataRow(['/health', 'Simple liveness {"status": "ok"}'], [2800, 6560], false),
      dataRow(['/health/db', 'Postgres pool size, checked-in/out connections, query latency'], [2800, 6560], true),
      dataRow(['/health/workers', 'Celery worker ping response times per queue'], [2800, 6560], false),
      dataRow(['/health/stats', 'Bulk cache hit/miss counters'], [2800, 6560], true),
      dataRow(['/cache/stats', 'Redis memory usage, backtest result count, hit rate'], [2800, 6560], false),
      dataRow(['/metrics', 'Prometheus text format (scraped by prometheus.yml)'], [2800, 6560], true),
    ],
  }),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 19 — FUTURE ROADMAP
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('19. Rust Port Roadmap', 'sec19'),
  p('The Rust engine port is an ongoing multi-phase project. The goal is to move all backtest computation into Rust to achieve 15-50 concurrent backtest capacity on the existing 16 GB HDD hardware.'),
  spacer(),
  h2('19.1 Completed Phases (Slices 1-5)'),
  bullet('Slice 1: Market data cache (AHashMap, integer keys, mmap feather).'),
  bullet('Slice 2: Basic option price lookup + slippage.'),
  bullet('Slice 3: Single-leg simulation (entry/exit by DTE, expiry settlement).'),
  bullet('Slice 4: Multi-leg simulation with per-leg SL/Target/Trail.'),
  bullet('Slice 5: FUTURES legs support + rollover logic.'),
  spacer(),
  h2('19.2 Remaining Phases (Slices 6-11)'),
  bullet('Slice 6: Re-entry after SL with rollover same-day chain.'),
  bullet('Slice 7: Overall portfolio SL across all legs.'),
  bullet('Slice 8: SuperTrend filter integration.'),
  bullet('Slice 9: Intraday sub-minute engine parity with Python.'),
  bullet('Slice 10: Optimizer native batch evaluation (skip Python bridge per combo).'),
  bullet('Slice 11: Streaming results back via Arrow IPC directly from Rust (remove Python serialisation step).'),
  spacer(),
  h2('19.3 Python Fallback Strategy'),
  p('Slices not yet ported are detected by check_strategy_blockers() in engine_rust.py. If any blocker is present, the full Python engine (generic_algotest_engine.py) is used transparently. The switch is invisible to the user and does not affect result correctness — Rust parity is verified trade-by-trade against Python reference tradesheets.'),
  pb()
);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION 20 — GLOSSARY
// ═══════════════════════════════════════════════════════════════════════════
children.push(
  h1('20. Glossary', 'sec20'),
  new Table({
    width: { size: 9360, type: WidthType.DXA },
    columnWidths: [2400, 6960],
    rows: [
      hdrRow(['Term', 'Definition'], [2400, 6960]),
      dataRow(['ATM', 'At-the-Money — strike price equal to current spot price'], [2400, 6960], false),
      dataRow(['DTE', 'Days to Expiry — calendar days remaining until contract expiry'], [2400, 6960], true),
      dataRow(['AHashMap', 'Rust hash map with AES-based hashing — 2-3x faster than std HashMap'], [2400, 6960], false),
      dataRow(['Arrow IPC', 'Apache Arrow Inter-Process Communication format — columnar binary'], [2400, 6960], true),
      dataRow(['billiard', 'Celery-safe fork of Python multiprocessing; enables CoW process pools'], [2400, 6960], false),
      dataRow(['CoW', 'Copy-on-Write — Linux forked processes share physical pages until one writes'], [2400, 6960], true),
      dataRow(['CE / PE', 'Call/Put European — NSE option instrument codes'], [2400, 6960], false),
      dataRow(['FUTIDX / OPTIDX', 'NSE instrument type codes for index futures and options'], [2400, 6960], true),
      dataRow(['Feather', 'Arrow-format binary file (v2 = IPC file format); used for fast mmap access'], [2400, 6960], false),
      dataRow(['MAE', 'Maximum Adverse Excursion — worst unrealised loss during trade lifetime'], [2400, 6960], true),
      dataRow(['MFE', 'Maximum Favourable Excursion — best unrealised profit during trade lifetime'], [2400, 6960], false),
      dataRow(['msgpack', 'MessagePack — binary serialisation format; faster/smaller than JSON'], [2400, 6960], true),
      dataRow(['NAV', 'Net Asset Value — portfolio value expressed as index (starts at 100)'], [2400, 6960], false),
      dataRow(['Polars', 'Rust-backed DataFrame library; faster than pandas for bulk DB loads'], [2400, 6960], true),
      dataRow(['PyO3', 'Rust library for writing Python extension modules'], [2400, 6960], false),
      dataRow(['STR / SuperTrend', 'SuperTrend technical indicator — used as a date-range filter'], [2400, 6960], true),
      dataRow(['SL with Buffer', 'Stop-loss checked against intraday H/L prices, not just EOD close'], [2400, 6960], false),
      dataRow(['uvloop', 'Ultra-fast asyncio event loop based on libuv; used by uvicorn'], [2400, 6960], true),
      dataRow(['vmtouch', 'Linux tool to lock files into OS page cache; used for HDD warmup'], [2400, 6960], false),
    ],
  }),
  spacer(),
  divider(),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240, after: 120 },
    children: [new TextRun({ text: 'AlgoTest Software — Architecture Document', bold: true, size: 20, color: C.darkGray, font: 'Arial' })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 0, after: 0 },
    children: [new TextRun({ text: 'Version 2.0  |  May 2026  |  Internal Use Only', size: 18, color: C.darkGray, font: 'Arial', italic: true })],
  }),
);

// ═══════════════════════════════════════════════════════════════════════════
// BUILD DOCUMENT
// ═══════════════════════════════════════════════════════════════════════════
const doc = new Document({
  numbering: {
    config: [
      {
        reference: 'bullets',
        levels: [{
          level: 0,
          format: LevelFormat.BULLET,
          text: '•',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
      {
        reference: 'numbers',
        levels: [{
          level: 0,
          format: LevelFormat.DECIMAL,
          text: '%1.',
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } },
        }],
      },
    ],
  },
  styles: {
    default: {
      document: { run: { font: 'Arial', size: 20, color: C.black } },
    },
    paragraphStyles: [
      {
        id: 'Heading1', name: 'Heading 1',
        basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 36, bold: true, color: C.navy, font: 'Arial' },
        paragraph: { spacing: { before: 360, after: 180 }, outlineLevel: 0 },
      },
      {
        id: 'Heading2', name: 'Heading 2',
        basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 26, bold: true, color: C.blue, font: 'Arial' },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
      },
      {
        id: 'Heading3', name: 'Heading 3',
        basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 22, bold: true, color: C.darkGray, font: 'Arial' },
        paragraph: { spacing: { before: 160, after: 80 }, outlineLevel: 2 },
      },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: C.blue, space: 4 } },
          children: [
            new TextRun({ text: 'AlgoTest Software', bold: true, size: 18, color: C.navy, font: 'Arial' }),
            new TextRun({ text: '   |   System Architecture Document', size: 18, color: C.darkGray, font: 'Arial' }),
          ],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          border: { top: { style: BorderStyle.SINGLE, size: 4, color: C.blue, space: 4 } },
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: 'Page ', size: 16, color: C.darkGray, font: 'Arial' }),
            new TextRun({ children: [PageNumber.CURRENT], size: 16, color: C.darkGray, font: 'Arial' }),
            new TextRun({ text: ' of ', size: 16, color: C.darkGray, font: 'Arial' }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: C.darkGray, font: 'Arial' }),
            new TextRun({ text: '   |   Internal Use Only   |   May 2026', size: 16, color: C.darkGray, font: 'Arial' }),
          ],
        })],
      }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync('architecture.docx', buf);
  console.log('Done: architecture.docx written (' + (buf.length / 1024).toFixed(0) + ' KB)');
}).catch(e => { console.error(e); process.exit(1); });
