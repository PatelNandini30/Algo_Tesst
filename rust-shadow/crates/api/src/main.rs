use std::collections::{BTreeMap, HashMap};
use std::io::Write;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::path::{Path as FsPath, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::{
    body::Body,
    extract::{Path, Query, State},
    http::{header, Response},
    response::Html,
    routing::{get, post},
    Json, Router,
};
use chrono::{Datelike, NaiveDate};
use rust_xlsxwriter::{Color, Format, Workbook};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio_util::io::ReaderStream;
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use zip::write::SimpleFileOptions;

use algotest_domain::ComboOverride;
use algotest_domain::StrategyConfig;
use algotest_engine::market_data::{CsvMarketDataSet, MarketDataError};
use algotest_engine::native::NativeEngine;
use algotest_engine::{EngineResult, StrategyEngine, SummaryMetrics, TradeRow};
use algotest_optimizer::{
    effective_combination_count, effective_strategy, raw_combination_count,
    run_optimization_iterator_streaming, run_smart_optimization, BatchLimits, CombinationStream,
    ComboResult, ParameterSpec, RandomCombinationStream,
};
use algotest_storage::{JobStore, StoredJob};

const ENGINE: &str = "rust-shadow";

struct AppState {
    compute_slot: Arc<tokio::sync::Semaphore>,
    repository_root: PathBuf,
    jobs: RwLock<HashMap<String, Arc<Mutex<JobRecord>>>>,
    next_job_id: AtomicU64,
    reserved_result_bytes: AtomicU64,
    store: Arc<JobStore>,
}

#[derive(Debug, Deserialize)]
struct PreviewRequest {
    base_payload: StrategyConfig,
    #[serde(default)]
    param_specs: Vec<ParameterSpec>,
    #[serde(default = "default_method")]
    method: String,
    sample_n: Option<u64>,
    algorithm: Option<String>,
}

#[derive(Debug, Deserialize)]
struct BacktestWorkbookRequest {
    #[serde(default)]
    trades: Vec<TradeRow>,
    #[serde(default)]
    summary: SummaryMetrics,
    combo_label: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OptimizeJobRequest {
    base_payload: StrategyConfig,
    #[serde(default)]
    param_specs: Vec<ParameterSpec>,
    #[serde(default = "default_method")]
    method: String,
    sample_n: Option<u64>,
    seed: Option<u64>,
    algorithm: Option<String>,
    #[serde(default = "default_objective")]
    objective: String,
    // Frontend request metadata retained for replay/export compatibility. The
    // shadow engine does not use these fields to alter pricing semantics.
    #[serde(default)]
    parallelism: Option<u32>,
    #[serde(default)]
    zip_naming: Option<Value>,
    #[serde(default)]
    node_id: Option<String>,
    #[serde(default)]
    auto_download: Option<bool>,
}

fn default_objective() -> String {
    "total_pnl".into()
}

#[derive(Debug, Clone, Serialize)]
struct JobRecord {
    job_id: String,
    status: String,
    phase: String,
    total: u64,
    done: u64,
    failed: u64,
    objective: String,
    error: Option<String>,
    created_at_ms: u64,
    started_at_ms: Option<u64>,
    finished_at_ms: Option<u64>,
    duration_ms: Option<u64>,
    #[serde(skip)]
    reserved_bytes: usize,
    #[serde(skip)]
    cancel_requested: bool,
    #[serde(skip)]
    deleted: bool,
    #[serde(skip)]
    request_json: Option<String>,
    #[serde(skip)]
    resolved_seed: Option<u64>,
}

#[derive(Debug, Serialize)]
struct JobAccepted {
    status: &'static str,
    job_id: String,
    total_combos: u64,
    objective: String,
    method: String,
    engine: &'static str,
    authoritative: bool,
}

#[derive(Debug, Deserialize)]
struct ResultsQuery {
    #[serde(default)]
    offset: usize,
    #[serde(default = "default_result_limit")]
    limit: usize,
    sort_by: Option<String>,
    order: Option<String>,
}

#[derive(Debug, Deserialize)]
struct PatchwiseQuery {
    #[serde(default = "default_true")]
    patchwise: bool,
}

#[derive(Debug, Default, Deserialize)]
struct SummaryExportRequest {
    sort_by: Option<String>,
    order: Option<String>,
}

fn default_true() -> bool {
    true
}

fn default_result_limit() -> usize {
    100
}

fn default_method() -> String {
    "exhaustive".into()
}

#[derive(Debug, Serialize)]
struct PreviewResponse {
    grid_size: u64,
    planned_runs: u64,
    estimated_seconds: u64,
    method: String,
    engine: &'static str,
    authoritative: bool,
}

#[tokio::main]
async fn main() {
    let state_directory = std::env::var("RUST_SHADOW_STATE_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("./state"));
    let store = Arc::new(
        JobStore::open(state_directory.join("jobs.sqlite3"))
            .expect("open isolated Rust shadow state store"),
    );
    store
        .mark_interrupted_jobs()
        .expect("mark interrupted shadow jobs");
    let restored_jobs = store
        .load_recent_jobs(env_u64("RUST_SHADOW_MAX_RESTORED_JOBS", 10_000) as usize)
        .expect("restore isolated Rust shadow jobs")
        .into_iter()
        .map(|job| {
            let job_id = job.job_id.clone();
            (
                job_id,
                Arc::new(Mutex::new(JobRecord {
                    job_id: job.job_id,
                    status: if job.status == "completed" {
                        "success".into()
                    } else {
                        job.status
                    },
                    phase: job.phase,
                    total: job.total,
                    done: job.done,
                    failed: job.failed,
                    objective: job.objective,
                    error: job.error,
                    created_at_ms: job.created_at_ms,
                    started_at_ms: job.started_at_ms,
                    finished_at_ms: job.finished_at_ms,
                    duration_ms: job.finished_at_ms.zip(job.started_at_ms).map(|(end, start)| end.saturating_sub(start)),
                    reserved_bytes: 0,
                    cancel_requested: false,
                    deleted: false,
                    request_json: job.request_json,
                    resolved_seed: job.resolved_seed,
                })),
            )
        })
        .collect();
    let state = Arc::new(AppState {
        // Until the cache manager can prove disjoint resident budgets, only
        // one cache-building request is admitted per process.
        compute_slot: Arc::new(tokio::sync::Semaphore::new(1)),
        repository_root: std::env::var("RUST_SHADOW_REPOSITORY_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("..")),
        jobs: RwLock::new(restored_jobs),
        next_job_id: AtomicU64::new(1),
        reserved_result_bytes: AtomicU64::new(0),
        store,
    });
    let app = Router::new()
        .route("/", get(dashboard))
        .route("/health", get(health))
        .route("/version", get(version))
        .route("/api/backtest", post(backtest))
        .route(
            "/api/backtest/tradesheet.xlsx",
            post(backtest_tradesheet_xlsx),
        )
        .route("/api/optimize/preview", post(preview))
        .route(
            "/api/optimize/jobs",
            get(list_jobs).post(enqueue_optimization),
        )
        .route(
            "/api/optimize/jobs/:job_id",
            get(get_job).delete(delete_job),
        )
        .route("/api/optimize/jobs/:job_id/results", get(get_job_results))
        .route("/api/optimize/jobs/:job_id/failures", get(get_job_failures))
        .route(
            "/api/optimize/jobs/:job_id/download-base",
            get(download_base),
        )
        .route(
            "/api/optimize/jobs/:job_id/combo/:combo_id/tradesheet.xlsx",
            get(download_combo_xlsx),
        )
        .route(
            "/api/optimize/jobs/:job_id/summary.xlsx",
            post(download_summary_xlsx),
        )
        .route(
            "/api/optimize/jobs/:job_id/tradesheets.zip",
            get(download_tradesheets_zip),
        )
        .route(
            "/api/optimize/jobs/:job_id/wow_mom.xlsx",
            get(download_wow_mom),
        )
        .route("/api/optimize/jobs/:job_id/resume", post(resume_job))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let port: u16 = std::env::var("RUST_SHADOW_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(18200);
    let host: IpAddr = std::env::var("RUST_SHADOW_HOST")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(IpAddr::V4(Ipv4Addr::LOCALHOST));
    let address = SocketAddr::from((host, port));
    let listener = tokio::net::TcpListener::bind(address)
        .await
        .expect("bind isolated Rust shadow API");
    axum::serve(listener, app)
        .await
        .expect("serve Rust shadow API");
}

#[derive(Serialize)]
struct BacktestResponse {
    engine: &'static str,
    authoritative: bool,
    python_runtime: bool,
    /// Title-Case tradesheet rows (Leg 1 carries the equity-curve columns) —
    /// the exact shape the frontend `ResultsPanel` consumes as `results.trades`.
    trades: Vec<Value>,
    /// Flattened summary (`extra` merged up) read as `results.summary`.
    summary: Value,
}

async fn backtest(
    State(state): State<Arc<AppState>>,
    Json(strategy): Json<StrategyConfig>,
) -> Result<Json<BacktestResponse>, (axum::http::StatusCode, Json<Value>)> {
    strategy.validate().map_err(bad_request)?;
    let from = parse_date(strategy.from_date.as_deref(), "from_date").map_err(bad_request)?;
    let to = parse_date(strategy.to_date.as_deref(), "to_date").map_err(bad_request)?;
    let budget = admitted_memory_bytes().map_err(service_unavailable)?;
    let permit = state
        .compute_slot
        .clone()
        .try_acquire_owned()
        .map_err(|_| service_unavailable("Rust shadow compute slot is busy"))?;
    let root = state.repository_root.clone();
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let market = load_strategy_market(root, &strategy, from, to, budget)
            .map_err(|error| error.to_string())?;
        let engine = NativeEngine::new(market);
        engine
            .run(
                &strategy,
                &ComboOverride {
                    combo_id: 1,
                    values: Default::default(),
                },
            )
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| service_unavailable(format!("compute task failed: {error}")))?
    .map_err(service_unavailable)?;
    Ok(Json(BacktestResponse {
        engine: ENGINE,
        authoritative: false,
        python_runtime: false,
        trades: algotest_engine::legacy_tradesheet(&result.trades),
        summary: algotest_engine::summary_flat(&result.summary),
    }))
}

async fn backtest_tradesheet_xlsx(
    Json(request): Json<BacktestWorkbookRequest>,
) -> Result<Response<Body>, (axum::http::StatusCode, Json<Value>)> {
    let maximum_rows = env_u64("RUST_SHADOW_MAX_TRADE_EXPORT_ROWS", 1_000_000) as usize;
    if request.trades.len() > maximum_rows {
        return Err(service_unavailable(format!(
            "tradesheet has {} rows; bounded export limit is {maximum_rows}",
            request.trades.len()
        )));
    }
    let label = request.combo_label.unwrap_or_else(|| "backtest".into());
    let safe_label: String = label
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .take(120)
        .collect();
    let result = EngineResult {
        trades: request.trades,
        summary: request.summary,
    };
    let bytes = tokio::task::spawn_blocking(move || {
        build_combo_workbook(&StrategyConfig::default(), &result)
    })
    .await
    .map_err(|error| service_unavailable(format!("backtest export task failed: {error}")))?
    .map_err(service_unavailable)?;
    Response::builder()
        .header(
            header::CONTENT_TYPE,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename={safe_label}.xlsx"),
        )
        .header("X-Filename", format!("{safe_label}.xlsx"))
        .body(Body::from(bytes))
        .map_err(service_unavailable)
}

async fn health() -> Json<Value> {
    Json(json!({
        "status":"ok",
        "engine":ENGINE,
        "authoritative":false,
        "python_runtime":false,
        "memory_limit_mb":env_u64("RUST_SHADOW_MEMORY_LIMIT_MB", 2048),
    }))
}

async fn dashboard() -> Html<&'static str> {
    Html(DASHBOARD_HTML)
}

const DASHBOARD_HTML: &str = r#"<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AlgoTest Rust Shadow</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#07111f;color:#dbeafe}body{margin:0;background:radial-gradient(circle at top,#12315a,#07111f 55%);min-height:100vh}.wrap{max-width:1100px;margin:auto;padding:32px}.head{display:flex;justify-content:space-between;gap:20px;align-items:center}.tag{padding:8px 12px;border:1px solid #22d3ee;border-radius:999px;color:#67e8f9}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px;margin-top:24px}.card{background:#0b1b30e8;border:1px solid #1e3a5f;border-radius:16px;padding:20px;box-shadow:0 18px 50px #0005}h1{margin:0;font-size:clamp(28px,5vw,48px)}h2{font-size:18px;margin-top:0}button{background:#06b6d4;color:#00131b;border:0;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer}button.secondary{background:#1e3a5f;color:#dbeafe}textarea,input,select{box-sizing:border-box;width:100%;margin:7px 0 12px;padding:10px;background:#06101d;color:#dbeafe;border:1px solid #31547d;border-radius:8px}textarea{height:230px;font-family:ui-monospace,monospace;font-size:12px}pre{white-space:pre-wrap;word-break:break-word;background:#050b13;padding:12px;border-radius:9px;max-height:340px;overflow:auto}.ok{color:#4ade80}.warn{color:#fbbf24}.small{color:#93aaca;font-size:13px}.actions{display:flex;gap:8px;flex-wrap:wrap}</style></head>
<body><main class="wrap"><div class="head"><div><h1>Rust Shadow</h1><p class="small">Pure-Rust, non-authoritative parity environment</p></div><span id="badge" class="tag">checking…</span></div>
<section class="grid"><article class="card"><h2>Runtime</h2><pre id="runtime">Loading…</pre><div class="actions"><button onclick="refresh()">Refresh</button></div></article>
<article class="card"><h2>Safety gates</h2><p class="ok">✓ Python runtime disabled</p><p class="ok">✓ Localhost-only shadow port</p><p class="ok">✓ Strict complete-or-failed jobs</p><p class="ok">✓ Bounded market, chunk, and result memory</p><p class="warn">Non-authoritative until every parity gate is clean</p></article></section>
<section class="grid"><article class="card"><h2>Strategy JSON</h2><textarea id="payload">{"index":"NIFTY","from_date":"2024-01-01","to_date":"2024-03-31","expiry_type":"WEEKLY","entry_dte":1,"exit_dte":0,"legs":[{"segment":"OPTIONS","position":"SELL","option_type":"CE","expiry":"WEEKLY","strike_selection":{"type":"ATM"}}]}</textarea><div class="actions"><button onclick="backtest()">Run backtest</button><button class="secondary" onclick="preview()">Preview grid</button></div></article>
<article class="card"><h2>Response</h2><pre id="output">Choose an action.</pre></article></section></main>
<script>
const out=document.querySelector('#output');const show=x=>out.textContent=typeof x==='string'?x:JSON.stringify(x,null,2);
async function request(url,body){show('Running…');const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});const x=await r.json();show(x);return x}
async function refresh(){try{const [h,v]=await Promise.all([fetch('/health').then(r=>r.json()),fetch('/version').then(r=>r.json())]);document.querySelector('#runtime').textContent=JSON.stringify({health:h,version:v},null,2);document.querySelector('#badge').textContent=h.status==='ok'?'HEALTHY':'UNHEALTHY'}catch(e){document.querySelector('#badge').textContent='OFFLINE';show(String(e))}}
function strategy(){return JSON.parse(document.querySelector('#payload').value)}
async function backtest(){try{await request('/api/backtest',strategy())}catch(e){show(String(e))}}
async function preview(){try{await request('/api/optimize/preview',{base_payload:strategy(),method:'exhaustive',param_specs:[{kind:'range',path:'entry_dte',min:1,max:20,step:1},{kind:'range',path:'exit_dte',min:0,max:9,step:1},{kind:'values',path:'legs[0].strike_selection.value',values:Array.from({length:15},(_,i)=>i)}]})}catch(e){show(String(e))}}
refresh();
</script></body></html>"#;

async fn version() -> Json<Value> {
    Json(
        json!({"engine":ENGINE, "version":env!("CARGO_PKG_VERSION"), "authoritative":false, "python_runtime":false}),
    )
}

async fn preview(
    Json(request): Json<PreviewRequest>,
) -> Result<Json<PreviewResponse>, (axum::http::StatusCode, Json<Value>)> {
    request.base_payload.validate().map_err(bad_request)?;
    let grid_size = raw_combination_count(&request.param_specs).map_err(bad_request)?;
    let maximum = env_u64(
        "RUST_SHADOW_MAX_COMBINATIONS",
        BatchLimits::default().max_combinations,
    );
    let planned_runs = match request.method.as_str() {
        "exhaustive" => {
            effective_combination_count(&request.param_specs, maximum).map_err(bad_request)?
        }
        "random" => {
            let wanted = request
                .sample_n
                .filter(|value| *value > 0)
                .ok_or_else(|| bad_request("random sampling requires sample_n > 0"))?;
            wanted.min(grid_size)
        }
        "smart" => {
            let algorithm = request.algorithm.as_deref().unwrap_or("cma-es");
            if !matches!(algorithm, "cma-es" | "cmaes" | "pso" | "ga" | "de") {
                return Err(bad_request(format!("unknown smart algorithm: {algorithm}")));
            }
            request.sample_n.unwrap_or(200)
        }
        other => return Err(bad_request(format!("unknown optimization method: {other}"))),
    };
    Ok(Json(PreviewResponse {
        grid_size,
        planned_runs,
        estimated_seconds: 0,
        method: request.method,
        engine: ENGINE,
        authoritative: false,
    }))
}

async fn enqueue_optimization(
    State(state): State<Arc<AppState>>,
    Json(mut request): Json<OptimizeJobRequest>,
) -> Result<Json<JobAccepted>, (axum::http::StatusCode, Json<Value>)> {
    request.base_payload.validate().map_err(bad_request)?;
    if !matches!(request.method.as_str(), "exhaustive" | "random" | "smart") {
        return Err(bad_request("unknown optimization method"));
    }
    if request.method == "exhaustive" && request.sample_n.is_some() {
        return Err(bad_request("sample_n is not valid for exhaustive jobs"));
    }
    let maximum = env_u64(
        "RUST_SHADOW_MAX_COMBINATIONS",
        BatchLimits::default().max_combinations,
    );
    let total = match request.method.as_str() {
        "exhaustive" => {
            effective_combination_count(&request.param_specs, maximum).map_err(bad_request)?
        }
        "random" => {
            let wanted = request
                .sample_n
                .filter(|value| *value > 0)
                .ok_or_else(|| bad_request("random sampling requires sample_n > 0"))?;
            wanted.min(raw_combination_count(&request.param_specs).map_err(bad_request)?)
        }
        "smart" => {
            let wanted = request.sample_n.unwrap_or(200);
            if wanted == 0 || wanted > maximum {
                return Err(bad_request(format!(
                    "smart budget must be between 1 and {maximum}"
                )));
            }
            wanted
        }
        _ => unreachable!(),
    };
    let memory = admitted_memory_bytes().map_err(service_unavailable)?;
    let retained_result_count = if request.method == "smart" {
        total
    } else {
        total.min(env_u64("RUST_SHADOW_OPTIMIZER_CHUNK", 256))
    };
    let result_bytes = usize::try_from(retained_result_count)
        .ok()
        .and_then(|count| count.checked_mul(2048))
        .ok_or_else(|| bad_request("result memory estimate overflow"))?;
    if result_bytes > memory / 4 {
        return Err(service_unavailable(format!(
            "job working buffer requires an estimated {result_bytes} bytes; bounded in-memory allowance is {}",
            memory / 4
        )));
    }
    reserve_result_memory(&state, result_bytes, memory / 4)?;
    let resolved_seed = request.seed.unwrap_or_else(random_seed);
    request.seed = Some(resolved_seed);
    let request_json = serde_json::to_string(&request).map_err(bad_request)?;
    let sequence = state.next_job_id.fetch_add(1, Ordering::Relaxed);
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let created_at_ms = unix_time_ms();
    let job_id = format!("rust-{epoch}-{sequence}");
    let record = Arc::new(Mutex::new(JobRecord {
        job_id: job_id.clone(),
        status: "queued".into(),
        phase: "waiting_for_memory_slot".into(),
        total,
        done: 0,
        failed: 0,
        objective: request.objective.clone(),
        error: None,
        created_at_ms,
        started_at_ms: None,
        finished_at_ms: None,
        duration_ms: None,
        reserved_bytes: result_bytes,
        cancel_requested: false,
        deleted: false,
        request_json: Some(request_json),
        resolved_seed: Some(resolved_seed),
    }));
    {
        let mut jobs = match state.jobs.write().map_err(lock_error) {
            Ok(jobs) => jobs,
            Err(error) => {
                release_result_memory(&state, result_bytes);
                return Err(error);
            }
        };
        let active_jobs = jobs
            .values()
            .filter(|record| {
                record.lock().is_ok_and(|job| {
                    matches!(job.status.as_str(), "queued" | "running" | "cancelling")
                })
            })
            .count();
        if active_jobs >= env_u64("RUST_SHADOW_MAX_ACTIVE_JOBS", 8) as usize {
            release_result_memory(&state, result_bytes);
            return Err(service_unavailable(
                "shadow active-job queue is full; wait for or cancel an active job",
            ));
        }
        jobs.insert(job_id.clone(), record.clone());
    }
    if let Err(error) = persist_job(&state.store, &record) {
        state.jobs.write().map_err(lock_error)?.remove(&job_id);
        release_result_memory(&state, result_bytes);
        return Err(service_unavailable(error));
    }

    let accepted_objective = request.objective.clone();
    let accepted_method = request.method.clone();
    launch_job(state.clone(), record, request, memory, maximum, 0);

    Ok(Json(JobAccepted {
        status: "queued",
        job_id,
        total_combos: total,
        objective: accepted_objective,
        method: accepted_method,
        engine: ENGINE,
        authoritative: false,
    }))
}

fn launch_job(
    state_for_task: Arc<AppState>,
    record: Arc<Mutex<JobRecord>>,
    request: OptimizeJobRequest,
    memory: usize,
    maximum: u64,
    skip: u64,
) {
    tokio::spawn(async move {
        let permit = match state_for_task.compute_slot.clone().acquire_owned().await {
            Ok(permit) => permit,
            Err(error) => {
                fail_job(
                    &state_for_task.store,
                    &record,
                    format!("compute admission closed: {error}"),
                );
                release_job_reservation(&state_for_task, &record);
                return;
            }
        };
        if is_cancelled(&record) {
            release_job_reservation(&state_for_task, &record);
            return;
        }
        update_job(&record, |job| {
            job.status = "running".into();
            job.phase = "loading_data".into();
            job.started_at_ms = Some(unix_time_ms());
        });
        let root = state_for_task.repository_root.clone();
        let record_for_work = record.clone();
        let store_for_work = state_for_task.store.clone();
        let outcome = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            execute_optimization_job(
                root,
                request,
                memory,
                maximum,
                record_for_work,
                store_for_work,
                skip,
            )
        })
        .await;
        match outcome {
            Ok(Ok(())) => {}
            Ok(Err(error)) => fail_job(&state_for_task.store, &record, error),
            Err(error) => fail_job(
                &state_for_task.store,
                &record,
                format!("optimization task failed: {error}"),
            ),
        }
        release_job_reservation(&state_for_task, &record);
    });
}

async fn resume_job(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> Result<Json<JobAccepted>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let (request, done, total, objective) = {
        let job = record.lock().map_err(lock_error)?;
        if matches!(job.status.as_str(), "queued" | "running" | "cancelling") {
            return Err(bad_request(
                "only an interrupted or failed inactive job can be resumed",
            ));
        }
        if job.failed > 0 {
            return Err(bad_request(
                "jobs with failed combinations cannot be resumed as successful",
            ));
        }
        if job.done >= job.total {
            return Err(bad_request("job has no missing combinations"));
        }
        let encoded = job
            .request_json
            .as_deref()
            .ok_or_else(|| bad_request("legacy job has no replayable request metadata"))?;
        let request: OptimizeJobRequest = serde_json::from_str(encoded).map_err(bad_request)?;
        if request.method == "smart" && job.done > 0 {
            return Err(bad_request(
                "smart resume requires zero published results so adaptive search can replay exactly",
            ));
        }
        (request, job.done, job.total, job.objective.clone())
    };
    let stored_results = state
        .store
        .result_count(&job_id)
        .map_err(service_unavailable)?;
    if stored_results != done {
        return Err(service_unavailable(format!(
            "resume refused: durable result count {stored_results} does not match progress {done}"
        )));
    }
    let memory = admitted_memory_bytes().map_err(service_unavailable)?;
    let maximum = env_u64(
        "RUST_SHADOW_MAX_COMBINATIONS",
        BatchLimits::default().max_combinations,
    );
    let remaining = total - done;
    let retained = remaining.min(env_u64("RUST_SHADOW_OPTIMIZER_CHUNK", 256));
    let result_bytes = usize::try_from(retained)
        .ok()
        .and_then(|count| count.checked_mul(2048))
        .ok_or_else(|| bad_request("resume memory estimate overflow"))?;
    reserve_result_memory(&state, result_bytes, memory / 4)?;
    {
        let mut job = record.lock().map_err(lock_error)?;
        job.status = "queued".into();
        job.phase = "waiting_for_memory_slot".into();
        job.error = None;
        job.reserved_bytes = result_bytes;
        job.cancel_requested = false;
        job.deleted = false;
    }
    if let Err(error) = persist_job(&state.store, &record) {
        release_job_reservation(&state, &record);
        return Err(service_unavailable(error));
    }
    let method = request.method.clone();
    launch_job(state, record, request, memory, maximum, done);
    Ok(Json(JobAccepted {
        status: "queued",
        job_id,
        total_combos: total,
        objective,
        method,
        engine: ENGINE,
        authoritative: false,
    }))
}

fn execute_optimization_job(
    root: PathBuf,
    request: OptimizeJobRequest,
    memory: usize,
    maximum: u64,
    record: Arc<Mutex<JobRecord>>,
    store: Arc<JobStore>,
    skip: u64,
) -> Result<(), String> {
    if is_cancelled(&record) {
        return Err("optimization cancelled before data loading".into());
    }
    let from = parse_date(request.base_payload.from_date.as_deref(), "from_date")?;
    let to = parse_date(request.base_payload.to_date.as_deref(), "to_date")?;
    let market = load_strategy_market(
        root,
        &request.base_payload,
        from,
        to,
        memory.saturating_mul(3) / 4,
    )
    .map_err(|error| error.to_string())?;
    let engine = Arc::new(NativeEngine::new(market));
    update_job(&record, |job| {
        job.phase = "validating_all_combinations".into()
    });
    persist_job(&store, &record)?;
    let random_seed = request.seed.unwrap_or_else(random_seed);
    if request.method == "exhaustive" {
        preflight_combinations(
            engine.clone(),
            &request.base_payload,
            &request.objective,
            CombinationStream::new(&request.param_specs, maximum)
                .map_err(|error| error.to_string())?
                .skip(skip as usize),
            &record,
            &store,
            memory,
            maximum,
        )?;
    } else if request.method == "random" {
        preflight_combinations(
            engine.clone(),
            &request.base_payload,
            &request.objective,
            RandomCombinationStream::new(
                &request.param_specs,
                request.sample_n.unwrap_or(0),
                random_seed,
                maximum,
            )
            .map_err(|error| error.to_string())?
            .skip(skip as usize),
            &record,
            &store,
            memory,
            maximum,
        )?;
    }
    update_job(&record, |job| {
        job.phase = "optimizing".into();
    });
    persist_job(&store, &record)?;
    let chunk_size = env_u64("RUST_SHADOW_OPTIMIZER_CHUNK", 256) as usize;
    let limits = BatchLimits {
        max_combinations: maximum,
        chunk_size,
        memory_budget_bytes: memory / 4,
        estimated_bytes_per_combo: 32 * 1024,
    };
    let collect = |results: &[ComboResult]| {
        if is_cancelled(&record) {
            return Err(algotest_optimizer::OptimizerError::Serialization(
                "optimization cancelled".into(),
            ));
        }
        let values = results
            .iter()
            .map(serde_json::to_value)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| {
                algotest_optimizer::OptimizerError::Serialization(error.to_string())
            })?;
        store
            .append_results(
                &record
                    .lock()
                    .map_err(|_| {
                        algotest_optimizer::OptimizerError::Serialization(
                            "job state lock poisoned".into(),
                        )
                    })?
                    .job_id,
                &values,
            )
            .map_err(|error| {
                algotest_optimizer::OptimizerError::Serialization(error.to_string())
            })?;
        update_job(&record, |job| {
            job.failed += results.iter().filter(|result| !result.is_success()).count() as u64;
            job.done += results.len() as u64;
        });
        persist_job(&store, &record).map_err(algotest_optimizer::OptimizerError::Serialization)?;
        Ok(())
    };
    let processed = if request.method == "exhaustive" {
        let stream = CombinationStream::new(&request.param_specs, maximum)
            .map_err(|error| error.to_string())?
            .skip(skip as usize);
        run_optimization_iterator_streaming(
            engine,
            &request.base_payload,
            stream,
            &request.objective,
            limits,
            collect,
        )
    } else if request.method == "random" {
        let stream = RandomCombinationStream::new(
            &request.param_specs,
            request.sample_n.unwrap_or(0),
            random_seed,
            maximum,
        )
        .map_err(|error| error.to_string())?
        .skip(skip as usize);
        run_optimization_iterator_streaming(
            engine,
            &request.base_payload,
            stream,
            &request.objective,
            limits,
            collect,
        )
    } else {
        if skip > 0 {
            return Err(
                "smart optimization resume requires replayable adaptive state and is not enabled"
                    .into(),
            );
        }
        // Smart suggestions depend on prior scores, so their full validation
        // cannot be known upfront. Stage the bounded budget atomically: a late
        // invalid suggestion publishes zero partial results.
        let mut staged = Vec::new();
        staged
            .try_reserve(request.sample_n.unwrap_or(200) as usize)
            .map_err(|error| format!("smart result allocation refused: {error}"))?;
        let processed = run_smart_optimization(
            engine,
            &request.base_payload,
            &request.param_specs,
            &request.objective,
            request.algorithm.as_deref().unwrap_or("cma-es"),
            request.sample_n.unwrap_or(200),
            random_seed,
            limits,
            |results| {
                if is_cancelled(&record) {
                    return Err(algotest_optimizer::OptimizerError::Serialization(
                        "optimization cancelled during smart search".into(),
                    ));
                }
                staged.extend_from_slice(results);
                Ok(())
            },
        )
        .map_err(|error| error.to_string())?;
        let job_id = record
            .lock()
            .map_err(|_| "job state lock poisoned".to_string())?
            .job_id
            .clone();
        store.clear_failures(&job_id).map_err(|error| error.to_string())?;
        let failures = staged
            .iter()
            .filter(|result| !result.is_success())
            .map(serde_json::to_value)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| error.to_string())?;
        if !failures.is_empty() {
            store
                .append_failures(&job_id, &failures)
                .map_err(|error| error.to_string())?;
            update_job(&record, |job| job.failed = failures.len() as u64);
            persist_job(&store, &record)?;
            return Err(format!(
                "smart execution rejected {} combination(s); no success results were published; inspect /api/optimize/jobs/{job_id}/failures",
                failures.len()
            ));
        }
        collect(&staged).map_err(|error: algotest_optimizer::OptimizerError| error.to_string())?;
        Ok(processed)
    }
    .map_err(|error| error.to_string())?;
    update_job(&record, |job| {
        if processed.saturating_add(skip) == job.total && job.failed == 0 {
            job.status = "success".into();
            job.phase = "complete".into();
        } else {
            job.status = "failed".into();
            job.phase = "incomplete".into();
            job.error = Some(format!(
                "strict completion failed: planned {}, processed {}, failed {}",
                job.total,
                processed.saturating_add(skip),
                job.failed
            ));
        }
        job.finished_at_ms = Some(unix_time_ms());
        job.duration_ms = job
            .finished_at_ms
            .zip(job.started_at_ms)
            .map(|(end, start)| end.saturating_sub(start));
    });
    persist_job(&store, &record)?;
    Ok(())
}

async fn get_job(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let snapshot = record.lock().map_err(lock_error)?.clone();
    let request = snapshot
        .request_json
        .as_deref()
        .and_then(|encoded| serde_json::from_str::<OptimizeJobRequest>(encoded).ok());
    let meta = json!({
        "job_id": snapshot.job_id.clone(),
        "status": snapshot.status.clone(),
        "phase": snapshot.phase.clone(),
        "total": snapshot.total,
        "done": snapshot.done,
        "failed": snapshot.failed,
        "objective": snapshot.objective.clone(),
        "error": snapshot.error.clone(),
        "base_payload": request.as_ref().map(|value| &value.base_payload),
        "param_specs": request.as_ref().map(|value| &value.param_specs),
        "method": request.as_ref().map(|value| &value.method),
        "sample_n": request.as_ref().and_then(|value| value.sample_n),
        "algorithm": request.as_ref().and_then(|value| value.algorithm.as_deref()),
        "seed": snapshot.resolved_seed,
        "created_at_ms": snapshot.created_at_ms,
        "started_at_ms": snapshot.started_at_ms,
        "finished_at_ms": snapshot.finished_at_ms,
        "duration_ms": snapshot.duration_ms,
        "engine": ENGINE,
        "authoritative": false,
    });
    Ok(Json(json!({
        "job_id": snapshot.job_id,
        "status": snapshot.status,
        "phase": snapshot.phase,
        "total": snapshot.total,
        "done": snapshot.done,
        "failed": snapshot.failed,
        "objective": snapshot.objective,
        "error": snapshot.error,
        "created_at_ms": snapshot.created_at_ms,
        "started_at_ms": snapshot.started_at_ms,
        "finished_at_ms": snapshot.finished_at_ms,
        "duration_ms": snapshot.duration_ms,
        "meta": meta,
        "engine": ENGINE,
        "authoritative": false,
    })))
}

async fn list_jobs(
    State(state): State<Arc<AppState>>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let jobs = state.jobs.read().map_err(lock_error)?;
    let mut snapshots = jobs
        .values()
        .map(|record| record.lock().map(|job| job.clone()).map_err(lock_error))
        .collect::<Result<Vec<_>, _>>()?;
    snapshots.sort_by(|left, right| right.job_id.cmp(&left.job_id));
    Ok(Json(json!({
        "jobs": snapshots,
        "count": snapshots.len(),
        "engine": ENGINE,
        "authoritative": false,
    })))
}

async fn get_job_results(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(query): Query<ResultsQuery>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let job = record.lock().map_err(lock_error)?;
    let limit = query.limit.clamp(1, 1000);
    let raw_results = if let Some(metric) = query.sort_by.as_deref() {
        let metric = if metric == "avg_profit_per_trade" {
            "average_pnl"
        } else {
            metric
        };
        state.store.results_sorted(
            &job_id,
            query.offset,
            limit,
            metric,
            !query
                .order
                .as_deref()
                .is_some_and(|order| order.eq_ignore_ascii_case("asc")),
        )
    } else {
        state.store.results(&job_id, query.offset, limit)
    };
    let results = raw_results
        .map_err(service_unavailable)?
        .into_iter()
        .map(compatibility_result)
        .collect::<Vec<_>>();
    Ok(Json(json!({
        "job_id":job_id,
        "status":job.status,
        "total":job.total,
        "done":job.done,
        "failed":job.failed,
        "offset":query.offset,
        "limit":limit,
        "results":results.clone(),
        "rows":results,
        "meta": {
            "job_id": job_id,
            "status": job.status,
            "phase": job.phase,
            "total": job.total,
            "done": job.done,
            "failed": job.failed,
            "objective": job.objective,
            "error": job.error,
        },
        "engine":ENGINE,
        "authoritative":false,
    })))
}

async fn get_job_failures(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(query): Query<ResultsQuery>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let job = record.lock().map_err(lock_error)?;
    let limit = query.limit.clamp(1, 1000);
    let failures = state
        .store
        .failures(&job_id, query.offset, limit)
        .map_err(service_unavailable)?;
    Ok(Json(json!({
        "job_id": job_id,
        "status": job.status,
        "failed": job.failed,
        "offset": query.offset,
        "limit": limit,
        "failures": failures,
        "engine": ENGINE,
        "authoritative": false,
    })))
}

fn compatibility_result(mut value: Value) -> Value {
    let Some(row) = value.as_object_mut() else {
        return value;
    };
    if let Some(parameters) = row.get("parameter_values").cloned() {
        row.insert("combo".into(), parameters.clone());
        row.insert("combo_columns".into(), parameters);
    }
    if let Some(summary) = row.get_mut("summary").and_then(Value::as_object_mut) {
        if let Some(average) = summary.get("average_pnl").cloned() {
            summary.insert("avg_profit_per_trade".into(), average);
        }
        if let Some(extra) = summary
            .remove("extra")
            .and_then(|item| item.as_object().cloned())
        {
            for (key, metric) in extra {
                summary.entry(key).or_insert(metric);
            }
        }
    }
    value
}

async fn download_base(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let _ = find_job(&state, &job_id)?;
    Ok(Json(json!({"download_base":""})))
}

async fn download_combo_xlsx(
    State(state): State<Arc<AppState>>,
    Path((job_id, combo_id)): Path<(String, u64)>,
) -> Result<Response<Body>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let request_json = {
        let job = record.lock().map_err(lock_error)?;
        if job.status != "success" {
            return Err(bad_request("tradesheet export requires a completed job"));
        }
        job.request_json
            .clone()
            .ok_or_else(|| bad_request("legacy job has no replayable request metadata"))?
    };
    let stored = state
        .store
        .result_by_combo_id(&job_id, combo_id)
        .map_err(service_unavailable)?
        .ok_or_else(|| not_found(&format!("{job_id}/combo/{combo_id}")))?;
    let combo_result: ComboResult = serde_json::from_value(stored).map_err(service_unavailable)?;
    if !combo_result.is_success() {
        return Err(bad_request("failed combinations do not have a tradesheet"));
    }
    let request: OptimizeJobRequest =
        serde_json::from_str(&request_json).map_err(service_unavailable)?;
    let strategy = request.base_payload.clone();
    let combo = ComboOverride {
        combo_id,
        values: combo_result.parameter_values,
    };
    let (effective, _) = effective_strategy(&strategy, &combo).map_err(bad_request)?;
    effective.validate().map_err(bad_request)?;
    let from = parse_date(effective.from_date.as_deref(), "from_date").map_err(bad_request)?;
    let to = parse_date(effective.to_date.as_deref(), "to_date").map_err(bad_request)?;
    let export_strategy = effective.clone();
    let memory = admitted_memory_bytes().map_err(service_unavailable)?;
    let permit = state
        .compute_slot
        .clone()
        .try_acquire_owned()
        .map_err(|_| service_unavailable("Rust shadow compute slot is busy"))?;
    let root = state.repository_root.clone();
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let market = load_strategy_market(
            root,
            &effective,
            from,
            to,
            memory.saturating_mul(3) / 4,
        )
        .map_err(|error| error.to_string())?;
        NativeEngine::new(market)
            .run(&effective, &combo)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|error| service_unavailable(format!("tradesheet task failed: {error}")))?
    .map_err(service_unavailable)?;
    let bytes = build_combo_workbook(&export_strategy, &result).map_err(service_unavailable)?;
    Response::builder()
        .header(
            header::CONTENT_TYPE,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=combo_{combo_id}_tradesheet.xlsx"),
        )
        .header("X-Filename", format!("combo_{combo_id}_tradesheet.xlsx"))
        .body(Body::from(bytes))
        .map_err(service_unavailable)
}

fn build_combo_workbook(
    strategy: &StrategyConfig,
    result: &EngineResult,
) -> Result<Vec<u8>, String> {
    let mut workbook = Workbook::new();
    let rules = workbook.add_worksheet();
    rules.set_name("Rules").map_err(|error| error.to_string())?;
    rules
        .write_string(0, 0, "STRATEGY RULES")
        .map_err(|error| error.to_string())?;
    rules
        .write_string(1, 0, "Strategy JSON")
        .map_err(|error| error.to_string())?;
    rules
        .write_string(
            1,
            1,
            serde_json::to_string(strategy).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;

    let sheet = workbook.add_worksheet();
    sheet
        .set_name("Trade Sheet")
        .map_err(|error| error.to_string())?;
    // Dynamic column set + chronological cleaned rows, an exact port of the live
    // excel_builder (Index, Spot P&L/%, Type, B/S, Qty, CE/PE/FUT P&L/%, Net MAE
    // 1/2/Final, Net P&L, % P&L, Cumulative/Peak/DD/%DD, Lowest NAV, Actual Live
    // DD, Exit Reason). Presence is driven by the actual leg types/filter, never
    // a fixed layout. Backtest sheet is patchwise by default (resets on
    // FILTER_END); with no filter that is identical to overall.
    let has_filter = strategy.filter_key().is_some()
        || result
            .trades
            .iter()
            .any(|t| t.annotations.contains_key("filter_segment"));
    let has_spot_adj = result
        .trades
        .iter()
        .any(|t| t.annotations.contains_key("raw_entry_price"));
    let (cleaned, key_order) =
        algotest_engine::workbook::workbook_tradesheet(&result.trades, has_filter, has_spot_adj, true);
    let pct_cols: std::collections::HashSet<&str> = algotest_engine::workbook::TRUE_PCT_COLS
        .iter()
        .copied()
        .collect();
    let pct_fmt = Format::new().set_num_format("0.00%");
    for (column, name) in key_order.iter().enumerate() {
        sheet
            .write_string(0, column as u16, name.as_str())
            .map_err(|error| error.to_string())?;
    }
    for (index, cleaned_row) in cleaned.iter().enumerate() {
        let row = (index + 1) as u32;
        for (column, key) in key_order.iter().enumerate() {
            let col = column as u16;
            let is_pct = pct_cols.contains(key.as_str());
            match cleaned_row.get(key) {
                Some(serde_json::Value::Number(number)) => {
                    let value = number.as_f64().unwrap_or(0.0);
                    if is_pct {
                        sheet
                            .write_number_with_format(row, col, value, &pct_fmt)
                            .map_err(|error| error.to_string())?;
                    } else {
                        sheet
                            .write_number(row, col, value)
                            .map_err(|error| error.to_string())?;
                    }
                }
                Some(serde_json::Value::String(text)) if !text.is_empty() => {
                    sheet
                        .write_string(row, col, text.as_str())
                        .map_err(|error| error.to_string())?;
                }
                _ => {}
            }
        }
    }
    let summary = workbook.add_worksheet();
    summary
        .set_name("Summary")
        .map_err(|error| error.to_string())?;
    let summary_flat = algotest_engine::summary_flat(&result.summary);
    let cells = algotest_engine::workbook::build_summary_cells(
        &cleaned,
        &summary_flat,
        &strategy.index,
        strategy.from_date.as_deref().unwrap_or(""),
        strategy.to_date.as_deref().unwrap_or(""),
    );
    let fmt_for = |style: algotest_engine::workbook::CellStyle| -> Format {
        use algotest_engine::workbook::CellStyle::*;
        match style {
            Title => Format::new().set_bold().set_font_size(13).set_font_color(Color::White).set_background_color(Color::RGB(0x1F_3864)),
            Subtitle => Format::new().set_font_color(Color::RGB(0x55_5555)),
            Section => Format::new().set_bold().set_font_color(Color::White).set_background_color(Color::RGB(0x2F_5496)),
            Header => Format::new().set_bold().set_font_color(Color::White).set_background_color(Color::RGB(0x44_72C4)),
            Label => Format::new().set_bold().set_background_color(Color::RGB(0xE7_E6E6)),
            ValuePos => Format::new().set_bold().set_font_color(Color::RGB(0x00_7A33)),
            ValueNeg => Format::new().set_bold().set_font_color(Color::RGB(0xC0_0000)),
            ValueNeutral => Format::new().set_bold(),
            Plain => Format::new(),
        }
    };
    write_cells(summary, &cells, &fmt_for)?;

    // Patch wise sheet — one "Nifty {…}" phase block, patches split on 30-day gap.
    let patch_cells = algotest_engine::workbook::build_patchwise_cells(&result.trades);
    if !patch_cells.is_empty() {
        let patch = workbook.add_worksheet();
        patch.set_name("Patch wise").map_err(|error| error.to_string())?;
        write_cells(patch, &patch_cells, &fmt_for)?;
    }

    // WOW & MOM Summary — weekly (by Expiry) + monthly (by Exit) return grids.
    let wow_cells = algotest_engine::workbook::build_wow_mom_cells(&cleaned);
    if !wow_cells.is_empty() {
        let wm = workbook.add_worksheet();
        wm.set_name("WOW & MOM Summary").map_err(|error| error.to_string())?;
        write_cells(wm, &wow_cells, &fmt_for)?;
    }

    workbook.save_to_buffer().map_err(|error| error.to_string())
}

/// Render a flat `SummaryCell` list to a worksheet, applying per-cell styles,
/// merges and number formats. Shared by the Summary / Patch wise / WOW&MOM tabs.
fn write_cells(
    sheet: &mut rust_xlsxwriter::Worksheet,
    cells: &[algotest_engine::workbook::SummaryCell],
    fmt_for: &impl Fn(algotest_engine::workbook::CellStyle) -> Format,
) -> Result<(), String> {
    for cell in cells {
        let fmt = fmt_for(cell.style);
        if let Some(to) = cell.merge_to {
            if to > cell.col {
                let text = cell.text.clone();
                sheet
                    .merge_range(cell.row, cell.col, cell.row, to, &text, &fmt)
                    .map_err(|error| error.to_string())?;
                continue;
            }
        }
        match cell.number {
            Some(number) => {
                let nf = cell
                    .num_fmt
                    .map(|f| fmt.clone().set_num_format(f))
                    .unwrap_or_else(|| fmt.clone());
                sheet
                    .write_number_with_format(cell.row, cell.col, number, &nf)
                    .map_err(|error| error.to_string())?;
            }
            None => {
                sheet
                    .write_string_with_format(cell.row, cell.col, cell.text.as_str(), &fmt)
                    .map_err(|error| error.to_string())?;
            }
        }
    }
    Ok(())
}

async fn download_summary_xlsx(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    payload: Option<Json<SummaryExportRequest>>,
) -> Result<Response<Body>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let snapshot = record.lock().map_err(lock_error)?.clone();
    if snapshot.status != "success" {
        return Err(bad_request("summary export requires a completed job"));
    }
    let request: OptimizeJobRequest = snapshot
        .request_json
        .as_deref()
        .ok_or_else(|| bad_request("legacy job has no replayable request metadata"))
        .and_then(|encoded| serde_json::from_str(encoded).map_err(bad_request))?;
    let maximum_rows = env_u64("RUST_SHADOW_MAX_EXPORT_ROWS", 50_000);
    let count = state
        .store
        .result_count(&job_id)
        .map_err(service_unavailable)?;
    if count > maximum_rows {
        return Err(service_unavailable(format!(
            "export has {count} rows; bounded XLSX limit is {maximum_rows}"
        )));
    }
    let memory = admitted_memory_bytes().map_err(service_unavailable)?;
    let estimated = usize::try_from(count)
        .ok()
        .and_then(|rows| rows.checked_mul(4096))
        .and_then(|bytes| bytes.checked_add(8 * 1024 * 1024))
        .ok_or_else(|| service_unavailable("XLSX memory estimate overflow"))?;
    if estimated > memory / 4 {
        return Err(service_unavailable(format!(
            "XLSX requires an estimated {estimated} bytes; bounded export allowance is {}",
            memory / 4
        )));
    }
    let permit = state
        .compute_slot
        .clone()
        .try_acquire_owned()
        .map_err(|_| service_unavailable("Rust shadow compute/export slot is busy"))?;
    let store = state.store.clone();
    let export_job_id = job_id.clone();
    let sort_by = payload.as_ref().and_then(|value| value.sort_by.clone());
    let descending = !payload
        .as_ref()
        .and_then(|value| value.order.as_deref())
        .is_some_and(|order| order.eq_ignore_ascii_case("asc"));
    let bytes = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        build_summary_workbook(
            &store,
            &export_job_id,
            count,
            &request,
            sort_by.as_deref(),
            descending,
        )
    })
    .await
    .map_err(|error| service_unavailable(format!("summary export task failed: {error}")))?
    .map_err(service_unavailable)?;
    Response::builder()
        .header(
            header::CONTENT_TYPE,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=rust-shadow-{job_id}-summary.xlsx"),
        )
        .body(Body::from(bytes))
        .map_err(service_unavailable)
}

fn build_summary_workbook(
    store: &JobStore,
    job_id: &str,
    count: u64,
    request: &OptimizeJobRequest,
    sort_by: Option<&str>,
    descending: bool,
) -> Result<Vec<u8>, String> {
    let mut workbook = Workbook::new();
    let rules = workbook.add_worksheet();
    rules.set_name("Rules").map_err(|error| error.to_string())?;
    rules
        .write_string(0, 0, "STRATEGY RULES")
        .map_err(|error| error.to_string())?;
    rules
        .write_string(1, 0, "Strategy JSON")
        .map_err(|error| error.to_string())?;
    rules
        .write_string(
            1,
            1,
            serde_json::to_string(&request.base_payload).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
    rules
        .write_string(2, 0, "Optimized Parameters")
        .map_err(|error| error.to_string())?;
    rules
        .write_string(
            2,
            1,
            serde_json::to_string(&request.param_specs).map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())?;
    let worksheet = workbook.add_worksheet();
    worksheet
        .set_name("Optimization Summary")
        .map_err(|error| error.to_string())?;
    worksheet
        .set_freeze_panes(1, 1)
        .map_err(|error| error.to_string())?;
    // Production summaries put each optimized axis in its own column. Keeping
    // the order from the incoming specs makes the workbook useful to the
    // existing results UI and avoids packing the whole combination into JSON.
    let parameter_paths: Vec<String> = request
        .param_specs
        .iter()
        .map(|spec| spec.path().to_string())
        .collect();
    let mut headers = vec!["Sr. No.".to_string()];
    headers.extend(parameter_paths.iter().cloned());
    headers.extend([
        "Objective".to_string(),
        "Trades Count".to_string(),
        "Net P/L Sum".to_string(),
        "Avg Profit/Trade".to_string(),
        "Win %".to_string(),
        "Max DD %".to_string(),
        "CAGR Options".to_string(),
        "CAR/MDD".to_string(),
        "Error".to_string(),
    ]);
    let header_format = Format::new()
        .set_bold()
        .set_background_color(Color::RGB(0x1F4E78))
        .set_font_color(Color::White);
    for (column, name) in headers.iter().enumerate() {
        worksheet
            .write_string_with_format(0, column as u16, name, &header_format)
            .map_err(|error| error.to_string())?;
        worksheet
            .set_column_width(column as u16, if column == 0 { 10.0 } else { 18.0 })
            .map_err(|error| error.to_string())?;
    }
    let mut offset = 0usize;
    while (offset as u64) < count {
        let page = if let Some(metric) = sort_by {
            let metric = if metric == "avg_profit_per_trade" {
                "average_pnl"
            } else {
                metric
            };
            store.results_sorted(job_id, offset, 1000, metric, descending)
        } else {
            store.results(job_id, offset, 1000)
        }
        .map_err(|error| error.to_string())?;
        if page.is_empty() {
            return Err("result export encountered a storage gap".into());
        }
        for value in page {
            let result: ComboResult =
                serde_json::from_value(value).map_err(|error| error.to_string())?;
            let row = (offset + 1) as u32;
            worksheet
                .write_number(row, 0, result.combo_id as f64)
                .map_err(|error| error.to_string())?;
            for (column, path) in parameter_paths.iter().enumerate() {
                if let Some(parameter) = result.parameter_values.get(path) {
                    write_export_value(&mut *worksheet, row, (column + 1) as u16, parameter)?;
                }
            }
            let metric_start = parameter_paths.len() + 1;
            if let Some(number) = result.objective_value {
                worksheet
                    .write_number(row, metric_start as u16, number)
                    .map_err(|error| error.to_string())?;
            }
            worksheet
                .write_number(row, (metric_start + 1) as u16, result.trade_count as f64)
                .map_err(|error| error.to_string())?;
            if let Some(summary) = result.summary {
                for (column, number) in [
                    summary.total_pnl,
                    summary.average_pnl,
                    summary.win_pct,
                    summary.max_dd_pct,
                    summary.cagr_options,
                    summary.car_mdd,
                ]
                .into_iter()
                .enumerate()
                {
                    worksheet
                        .write_number(row, (metric_start + 2 + column) as u16, number)
                        .map_err(|error| error.to_string())?;
                }
            }
            if let Some(error) = result.error {
                worksheet
                    .write_string(row, (metric_start + 8) as u16, error)
                    .map_err(|error| error.to_string())?;
            }
            offset += 1;
        }
    }
    workbook.save_to_buffer().map_err(|error| error.to_string())
}

fn write_export_value(
    worksheet: &mut rust_xlsxwriter::Worksheet,
    row: u32,
    column: u16,
    value: &Value,
) -> Result<(), String> {
    match value {
        Value::Number(number) => worksheet
            .write_number(row, column, number.as_f64().unwrap_or_default())
            .map(|_| ())
            .map_err(|error| error.to_string()),
        Value::Bool(value) => worksheet
            .write_boolean(row, column, *value)
            .map(|_| ())
            .map_err(|error| error.to_string()),
        Value::String(value) => worksheet
            .write_string(row, column, value)
            .map(|_| ())
            .map_err(|error| error.to_string()),
        Value::Null => Ok(()),
        other => worksheet
            .write_string(row, column, serde_json::to_string(other).map_err(|error| error.to_string())?)
            .map(|_| ())
            .map_err(|error| error.to_string()),
    }
}

async fn download_tradesheets_zip(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(query): Query<PatchwiseQuery>,
) -> Result<Response<Body>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let request_json = {
        let job = record.lock().map_err(lock_error)?;
        if job.status != "success" {
            return Err(bad_request("tradesheet ZIP requires a successful job"));
        }
        job.request_json
            .clone()
            .ok_or_else(|| bad_request("legacy job has no replayable request metadata"))?
    };
    let request: OptimizeJobRequest = serde_json::from_str(&request_json).map_err(bad_request)?;
    let has_filter = !request.base_payload.filter_segments.is_empty()
        || request.base_payload.filter_key().is_some();
    if query.patchwise && has_filter {
        return Err(bad_request(
            "patchwise filtered ZIP is not emitted until its reset-equity workbook parity gate passes",
        ));
    }
    let state_root = state
        .store
        .path()
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."));
    let artifact_directory = state_root.join("artifacts").join(&job_id);
    let suffix = if query.patchwise {
        "patchwise"
    } else {
        "overall"
    };
    let archive_path = artifact_directory.join(format!("tradesheets-{suffix}.zip"));
    if !archive_path.is_file() {
        let memory = admitted_memory_bytes().map_err(service_unavailable)?;
        let permit = state
            .compute_slot
            .clone()
            .try_acquire_owned()
            .map_err(|_| service_unavailable("Rust shadow compute/export slot is busy"))?;
        let store = state.store.clone();
        let root = state.repository_root.clone();
        let build_job_id = job_id.clone();
        let build_path = archive_path.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            build_tradesheets_archive(&store, &build_job_id, &request, root, memory, &build_path)
        })
        .await
        .map_err(|error| service_unavailable(format!("ZIP export task failed: {error}")))?
        .map_err(service_unavailable)?;
    }
    let file = tokio::fs::File::open(&archive_path)
        .await
        .map_err(service_unavailable)?;
    let size = file.metadata().await.map_err(service_unavailable)?.len();
    let stream = ReaderStream::new(file);
    Response::builder()
        .header(header::CONTENT_TYPE, "application/zip")
        .header(header::CONTENT_LENGTH, size)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename=rust-shadow-{job_id}-tradesheets-{suffix}.zip"),
        )
        .header(
            "X-Filename",
            format!("rust-shadow-{job_id}-tradesheets-{suffix}.zip"),
        )
        .body(Body::from_stream(stream))
        .map_err(service_unavailable)
}

fn build_tradesheets_archive(
    store: &JobStore,
    job_id: &str,
    request: &OptimizeJobRequest,
    repository_root: PathBuf,
    memory: usize,
    archive_path: &std::path::Path,
) -> Result<(), String> {
    let parent = archive_path
        .parent()
        .ok_or_else(|| "artifact path has no parent".to_string())?;
    std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = archive_path.with_extension("zip.building");
    if temporary.exists() {
        std::fs::remove_file(&temporary).map_err(|error| error.to_string())?;
    }
    let from = parse_date(request.base_payload.from_date.as_deref(), "from_date")?;
    let to = parse_date(request.base_payload.to_date.as_deref(), "to_date")?;
    let market = load_strategy_market(
        repository_root,
        &request.base_payload,
        from,
        to,
        memory.saturating_mul(3) / 4,
    )
    .map_err(|error| error.to_string())?;
    let engine = NativeEngine::new(market);
    let file = std::fs::File::create(&temporary).map_err(|error| error.to_string())?;
    let mut archive = zip::ZipWriter::new(file);
    let options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .compression_level(Some(3));
    let tradesheets_root = zip_naming_root(request.zip_naming.as_ref());
    let maximum_bytes = env_u64("RUST_SHADOW_MAX_ARTIFACT_MB", 8192)
        .checked_mul(1024 * 1024)
        .ok_or_else(|| "artifact byte limit overflow".to_string())?;
    let count = store
        .result_count(job_id)
        .map_err(|error| error.to_string())?;
    let mut written_bytes = 0u64;
    let result = (|| {
        let mut offset = 0usize;
        while (offset as u64) < count {
            let page = store
                .results(job_id, offset, 100)
                .map_err(|error| error.to_string())?;
            if page.is_empty() {
                return Err("tradesheet ZIP encountered a durable result gap".into());
            }
            for value in page {
                let combo_result: ComboResult =
                    serde_json::from_value(value).map_err(|error| error.to_string())?;
                if !combo_result.is_success() {
                    return Err(format!(
                        "combo {} has no successful tradesheet",
                        combo_result.combo_id
                    ));
                }
                let combo = ComboOverride {
                    combo_id: combo_result.combo_id,
                    values: combo_result.parameter_values,
                };
                let (strategy, _) = effective_strategy(&request.base_payload, &combo)
                    .map_err(|error| error.to_string())?;
                let engine_result = engine
                    .run(&strategy, &combo)
                    .map_err(|error| error.to_string())?;
                let workbook = build_combo_workbook(&strategy, &engine_result)?;
                written_bytes = written_bytes
                    .checked_add(workbook.len() as u64)
                    .ok_or_else(|| "artifact byte counter overflow".to_string())?;
                if written_bytes > maximum_bytes {
                    return Err(format!(
                        "artifact exceeded bounded limit of {maximum_bytes} uncompressed bytes"
                    ));
                }
                archive
                    .start_file(
                        format!("{tradesheets_root}/{}.xlsx", combo_archive_label(&combo)),
                        options,
                    )
                    .map_err(|error| error.to_string())?;
                archive
                    .write_all(&workbook)
                    .map_err(|error| error.to_string())?;
                offset += 1;
            }
        }
        Ok(())
    })();
    if let Err(error) = result {
        drop(archive);
        let _ = std::fs::remove_file(&temporary);
        return Err(error);
    }
    archive.finish().map_err(|error| error.to_string())?;
    std::fs::rename(&temporary, archive_path).map_err(|error| error.to_string())?;
    Ok(())
}

fn combo_archive_label(combo: &ComboOverride) -> String {
    let mut label = combo.combo_id.to_string();
    for (path, value) in &combo.values {
        let value = match value {
            Value::String(value) => value.clone(),
            Value::Number(value) => value.to_string(),
            Value::Bool(value) => value.to_string(),
            _ => continue,
        };
        let token = format!("{}_{}", path.replace(['[', ']', '.'], "_"), value);
        let token = safe_archive_component(&token);
        if label.len().saturating_add(token.len()).saturating_add(1) > 180 {
            break;
        }
        label.push('_');
        label.push_str(&token);
    }
    label
}

fn safe_archive_component(value: &str) -> String {
    value
        .chars()
        .map(|ch| match ch {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect()
}

fn zip_naming_root(value: Option<&Value>) -> String {
    let Some(object) = value.and_then(Value::as_object) else {
        return "tradesheets".into();
    };
    let parts = ["level1", "level2", "level3"]
        .into_iter()
        .filter_map(|key| object.get(key).and_then(Value::as_str))
        .map(|part| {
            safe_archive_component(part)
        })
        .filter(|part| !part.trim().is_empty() && part != "." && part != "..")
        .collect::<Vec<_>>();
    if parts.is_empty() {
        "tradesheets".into()
    } else {
        parts.join("/")
    }
}

#[derive(Default)]
struct WowMomBlock {
    combo_id: u64,
    wow: BTreeMap<i32, BTreeMap<u32, f64>>,
    mom: BTreeMap<i32, BTreeMap<u32, f64>>,
}

async fn download_wow_mom(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
    Query(query): Query<PatchwiseQuery>,
) -> Result<Response<Body>, (axum::http::StatusCode, Json<Value>)> {
    let record = find_job(&state, &job_id)?;
    let request_json = {
        let job = record.lock().map_err(lock_error)?;
        if job.status != "success" {
            return Err(bad_request("WOW/MOM export requires a successful job"));
        }
        job.request_json
            .clone()
            .ok_or_else(|| bad_request("legacy job has no replayable request metadata"))?
    };
    let request: OptimizeJobRequest = serde_json::from_str(&request_json).map_err(bad_request)?;
    let has_filter = !request.base_payload.filter_segments.is_empty()
        || request.base_payload.filter_key().is_some();
    if query.patchwise && has_filter {
        return Err(bad_request(
            "patchwise filtered WOW/MOM is not emitted until reset-equity parity passes",
        ));
    }
    let state_root = state
        .store
        .path()
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."));
    let directory = state_root.join("artifacts").join(&job_id);
    let suffix = if query.patchwise {
        "patchwise"
    } else {
        "overall"
    };
    let count = state
        .store
        .result_count(&job_id)
        .map_err(service_unavailable)?;
    let part_limit = env_u64("RUST_SHADOW_WOW_MOM_PART_COMBOS", 2500);
    let is_parts = count > part_limit;
    let artifact_path = if is_parts {
        directory.join(format!("wow-mom-{suffix}-parts.zip"))
    } else {
        directory.join(format!("wow-mom-{suffix}.xlsx"))
    };
    if !artifact_path.is_file() {
        let memory = admitted_memory_bytes().map_err(service_unavailable)?;
        let permit = state
            .compute_slot
            .clone()
            .try_acquire_owned()
            .map_err(|_| service_unavailable("Rust shadow compute/export slot is busy"))?;
        let store = state.store.clone();
        let root = state.repository_root.clone();
        let build_job_id = job_id.clone();
        let build_path = artifact_path.clone();
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            build_wow_mom_artifact(
                &store,
                &build_job_id,
                &request,
                root,
                memory,
                &build_path,
                part_limit as usize,
                is_parts,
            )
        })
        .await
        .map_err(|error| service_unavailable(format!("WOW/MOM task failed: {error}")))?
        .map_err(service_unavailable)?;
    }
    let file = tokio::fs::File::open(&artifact_path)
        .await
        .map_err(service_unavailable)?;
    let size = file.metadata().await.map_err(service_unavailable)?.len();
    let (media_type, extension) = if is_parts {
        ("application/zip", "zip")
    } else {
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        )
    };
    let filename = format!("rust-shadow-{job_id}-wow-mom-{suffix}.{extension}");
    Response::builder()
        .header(header::CONTENT_TYPE, media_type)
        .header(header::CONTENT_LENGTH, size)
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename={filename}"),
        )
        .header("X-Filename", filename)
        .body(Body::from_stream(ReaderStream::new(file)))
        .map_err(service_unavailable)
}

#[allow(clippy::too_many_arguments)]
fn build_wow_mom_artifact(
    store: &JobStore,
    job_id: &str,
    request: &OptimizeJobRequest,
    repository_root: PathBuf,
    memory: usize,
    artifact_path: &std::path::Path,
    part_limit: usize,
    is_parts: bool,
) -> Result<(), String> {
    let parent = artifact_path
        .parent()
        .ok_or_else(|| "WOW/MOM artifact has no parent".to_string())?;
    std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = artifact_path.with_extension("building");
    if temporary.exists() {
        std::fs::remove_file(&temporary).map_err(|error| error.to_string())?;
    }
    let from = parse_date(request.base_payload.from_date.as_deref(), "from_date")?;
    let to = parse_date(request.base_payload.to_date.as_deref(), "to_date")?;
    let market = load_strategy_market(
        repository_root,
        &request.base_payload,
        from,
        to,
        memory.saturating_mul(3) / 4,
    )
    .map_err(|error| error.to_string())?;
    let engine = NativeEngine::new(market);
    let count = store
        .result_count(job_id)
        .map_err(|error| error.to_string())?;
    let maximum_bytes = env_u64("RUST_SHADOW_MAX_ARTIFACT_MB", 8192)
        .checked_mul(1024 * 1024)
        .ok_or_else(|| "artifact byte limit overflow".to_string())?;
    let mut zip_writer = if is_parts {
        Some(zip::ZipWriter::new(
            std::fs::File::create(&temporary).map_err(|error| error.to_string())?,
        ))
    } else {
        None
    };
    let zip_options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .compression_level(Some(3));
    let yearly = request
        .base_payload
        .expiry_type
        .as_deref()
        .is_some_and(|value| value.eq_ignore_ascii_case("YEARLY"));
    let mut blocks = Vec::with_capacity(part_limit.min(count as usize));
    let mut offset = 0usize;
    let mut part = 0usize;
    let mut uncompressed_bytes = 0u64;
    while (offset as u64) < count {
        let page = store
            .results(job_id, offset, 100)
            .map_err(|error| error.to_string())?;
        if page.is_empty() {
            return Err("WOW/MOM encountered a durable result gap".into());
        }
        for value in page {
            let combo_result: ComboResult =
                serde_json::from_value(value).map_err(|error| error.to_string())?;
            let combo = ComboOverride {
                combo_id: combo_result.combo_id,
                values: combo_result.parameter_values,
            };
            let (strategy, _) = effective_strategy(&request.base_payload, &combo)
                .map_err(|error| error.to_string())?;
            let result = engine
                .run(&strategy, &combo)
                .map_err(|error| error.to_string())?;
            blocks.push(wow_mom_block(combo.combo_id, &result, yearly)?);
            offset += 1;
            if blocks.len() == part_limit || (offset as u64) == count {
                let bytes = render_wow_mom_workbook(&blocks)?;
                uncompressed_bytes = uncompressed_bytes
                    .checked_add(bytes.len() as u64)
                    .ok_or_else(|| "WOW/MOM byte counter overflow".to_string())?;
                if uncompressed_bytes > maximum_bytes {
                    return Err(format!(
                        "WOW/MOM exceeded bounded limit of {maximum_bytes} uncompressed bytes"
                    ));
                }
                if let Some(archive) = zip_writer.as_mut() {
                    part += 1;
                    archive
                        .start_file(format!("wow_mom_part_{part:03}.xlsx"), zip_options)
                        .map_err(|error| error.to_string())?;
                    archive
                        .write_all(&bytes)
                        .map_err(|error| error.to_string())?;
                } else {
                    std::fs::write(&temporary, bytes).map_err(|error| error.to_string())?;
                }
                blocks.clear();
            }
        }
    }
    if let Some(archive) = zip_writer {
        archive.finish().map_err(|error| error.to_string())?;
    }
    std::fs::rename(&temporary, artifact_path).map_err(|error| error.to_string())?;
    Ok(())
}

fn wow_mom_block(
    combo_id: u64,
    result: &EngineResult,
    yearly: bool,
) -> Result<WowMomBlock, String> {
    let mut block = WowMomBlock {
        combo_id,
        ..Default::default()
    };
    for trade in algotest_engine::canonical_parent_rows(&result.trades) {
        let return_decimal = if trade.entry_spot == 0.0 {
            0.0
        } else {
            trade.net_pnl / trade.entry_spot
        };
        let weekly_date = parse_iso_date(if yearly {
            &trade.exit_date
        } else {
            &trade.expiry
        })?;
        let iso = weekly_date.iso_week();
        *block
            .wow
            .entry(iso.year())
            .or_default()
            .entry(iso.week())
            .or_default() += return_decimal;
        let exit = parse_iso_date(&trade.exit_date)?;
        *block
            .mom
            .entry(exit.year())
            .or_default()
            .entry(exit.month())
            .or_default() += return_decimal;
    }
    Ok(block)
}

fn parse_iso_date(value: &str) -> Result<NaiveDate, String> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|_| format!("invalid engine date {value}"))
}

fn period_max_drawdown(values: impl Iterator<Item = f64>) -> f64 {
    let mut running = 0.0f64;
    let mut worst = 0.0f64;
    for value in values {
        if running == 0.0 && value >= 0.0 {
            continue;
        }
        running += value;
        worst = worst.min(running);
        if running >= 0.0 {
            running = 0.0;
        }
    }
    worst
}

fn render_wow_mom_workbook(blocks: &[WowMomBlock]) -> Result<Vec<u8>, String> {
    let mut workbook = Workbook::new();
    {
        let sheet = workbook.add_worksheet();
        sheet
            .set_name("WOW Summary")
            .map_err(|error| error.to_string())?;
        let mut row = 0u32;
        for block in blocks {
            sheet
                .write_string(row, 0, format!("Combo {}", block.combo_id))
                .map_err(|error| error.to_string())?;
            row += 1;
            sheet
                .write_string(row, 0, "Year")
                .map_err(|error| error.to_string())?;
            for week in 1..=53u16 {
                sheet
                    .write_string(row, week, format!("W{week}"))
                    .map_err(|error| error.to_string())?;
            }
            sheet
                .write_string(row, 54, "Total")
                .map_err(|error| error.to_string())?;
            sheet
                .write_string(row, 55, "Max DD")
                .map_err(|error| error.to_string())?;
            row += 1;
            for (year, weeks) in &block.wow {
                sheet
                    .write_number(row, 0, *year as f64)
                    .map_err(|error| error.to_string())?;
                for (week, value) in weeks {
                    sheet
                        .write_number(row, *week as u16, *value)
                        .map_err(|error| error.to_string())?;
                }
                sheet
                    .write_number(row, 54, weeks.values().sum::<f64>())
                    .map_err(|error| error.to_string())?;
                sheet
                    .write_number(row, 55, period_max_drawdown(weeks.values().copied()))
                    .map_err(|error| error.to_string())?;
                row += 1;
            }
            row += 2;
        }
    }
    {
        let sheet = workbook.add_worksheet();
        sheet
            .set_name("MOM Summary")
            .map_err(|error| error.to_string())?;
        let months = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ];
        let mut row = 0u32;
        for block in blocks {
            sheet
                .write_string(row, 0, format!("Combo {}", block.combo_id))
                .map_err(|error| error.to_string())?;
            row += 1;
            sheet
                .write_string(row, 0, "Year")
                .map_err(|error| error.to_string())?;
            for (index, month) in months.iter().enumerate() {
                sheet
                    .write_string(row, index as u16 + 1, *month)
                    .map_err(|error| error.to_string())?;
            }
            sheet
                .write_string(row, 13, "Total")
                .map_err(|error| error.to_string())?;
            sheet
                .write_string(row, 14, "Max DD")
                .map_err(|error| error.to_string())?;
            row += 1;
            for (year, monthly) in &block.mom {
                sheet
                    .write_number(row, 0, *year as f64)
                    .map_err(|error| error.to_string())?;
                for (month, value) in monthly {
                    sheet
                        .write_number(row, *month as u16, *value)
                        .map_err(|error| error.to_string())?;
                }
                sheet
                    .write_number(row, 13, monthly.values().sum::<f64>())
                    .map_err(|error| error.to_string())?;
                sheet
                    .write_number(row, 14, period_max_drawdown(monthly.values().copied()))
                    .map_err(|error| error.to_string())?;
                row += 1;
            }
            row += 2;
        }
    }
    // Keep the four-sheet production workbook contract. These pivot sheets
    // expose the same native period returns in a compact, one-row-per-combo
    // layout; they do not recompute or mutate any engine statistics.
    {
        let sheet = workbook.add_worksheet();
        sheet
            .set_name("WOW Min Pivots")
            .map_err(|error| error.to_string())?;
        sheet.write_string(0, 0, "Combo ID").map_err(|error| error.to_string())?;
        for week in 1..=53u16 {
            sheet.write_string(0, week, format!("W{week}")).map_err(|error| error.to_string())?;
        }
        for (row, block) in blocks.iter().enumerate() {
            sheet.write_number((row + 1) as u32, 0, block.combo_id as f64).map_err(|error| error.to_string())?;
            for week in 1..=53u32 {
                let minimum = block.wow.values().filter_map(|periods| periods.get(&week)).copied().reduce(f64::min);
                if let Some(value) = minimum {
                    sheet.write_number((row + 1) as u32, week as u16, value).map_err(|error| error.to_string())?;
                }
            }
        }
    }
    {
        let sheet = workbook.add_worksheet();
        sheet
            .set_name("MOM Min Pivots")
            .map_err(|error| error.to_string())?;
        sheet.write_string(0, 0, "Combo ID").map_err(|error| error.to_string())?;
        let months = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ];
        for (index, month) in months.iter().enumerate() {
            sheet.write_string(0, (index + 1) as u16, *month).map_err(|error| error.to_string())?;
        }
        for (row, block) in blocks.iter().enumerate() {
            sheet.write_number((row + 1) as u32, 0, block.combo_id as f64).map_err(|error| error.to_string())?;
            for month in 1..=12u32 {
                let minimum = block.mom.values().filter_map(|periods| periods.get(&month)).copied().reduce(f64::min);
                if let Some(value) = minimum {
                    sheet.write_number((row + 1) as u32, month as u16, value).map_err(|error| error.to_string())?;
                }
            }
        }
    }
    workbook.save_to_buffer().map_err(|error| error.to_string())
}

async fn delete_job(
    State(state): State<Arc<AppState>>,
    Path(job_id): Path<String>,
) -> Result<Json<Value>, (axum::http::StatusCode, Json<Value>)> {
    let mut jobs = state.jobs.write().map_err(lock_error)?;
    let Some(record) = jobs.get(&job_id) else {
        return Err(not_found(&job_id));
    };
    let mut record = record.lock().map_err(lock_error)?;
    let active = matches!(record.status.as_str(), "queued" | "running" | "cancelling");
    if active {
        record.cancel_requested = true;
        record.deleted = true;
        record.status = "cancelling".into();
        record.phase = "cancelling".into();
    }
    let reserved = record.reserved_bytes;
    drop(record);
    jobs.remove(&job_id).expect("job existence checked");
    state
        .store
        .delete_job(&job_id)
        .map_err(service_unavailable)?;
    if !active {
        release_result_memory(&state, reserved);
    }
    Ok(Json(
        json!({"deleted":true,"job_id":job_id,"engine":ENGINE}),
    ))
}

fn find_job(
    state: &AppState,
    job_id: &str,
) -> Result<Arc<Mutex<JobRecord>>, (axum::http::StatusCode, Json<Value>)> {
    state
        .jobs
        .read()
        .map_err(lock_error)?
        .get(job_id)
        .cloned()
        .ok_or_else(|| not_found(job_id))
}

fn update_job(record: &Arc<Mutex<JobRecord>>, update: impl FnOnce(&mut JobRecord)) {
    if let Ok(mut job) = record.lock() {
        update(&mut job);
    }
}

fn fail_job(store: &JobStore, record: &Arc<Mutex<JobRecord>>, error: String) {
    update_job(record, |job| {
        job.status = "failed".into();
        job.phase = "failed".into();
        job.error = Some(error);
        job.finished_at_ms = Some(unix_time_ms());
        job.duration_ms = job
            .finished_at_ms
            .zip(job.started_at_ms)
            .map(|(end, start)| end.saturating_sub(start));
    });
    let _ = persist_job(store, record);
}

fn persist_job(store: &JobStore, record: &Arc<Mutex<JobRecord>>) -> Result<(), String> {
    let job = record
        .lock()
        .map_err(|_| "job state lock poisoned".to_string())?;
    if job.deleted {
        return Ok(());
    }
    store
        .upsert_job(&StoredJob {
            job_id: job.job_id.clone(),
            status: job.status.clone(),
            phase: job.phase.clone(),
            total: job.total,
            done: job.done,
            failed: job.failed,
            objective: job.objective.clone(),
            error: job.error.clone(),
            reserved_bytes: job.reserved_bytes,
            request_json: job.request_json.clone(),
            resolved_seed: job.resolved_seed,
            created_at_ms: job.created_at_ms,
            started_at_ms: job.started_at_ms,
            finished_at_ms: job.finished_at_ms,
        })
        .map_err(|error| error.to_string())
}

fn unix_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

fn release_job_reservation(state: &AppState, record: &Arc<Mutex<JobRecord>>) {
    let released = record
        .lock()
        .ok()
        .map(|mut job| {
            let released = job.reserved_bytes;
            job.reserved_bytes = 0;
            released
        })
        .unwrap_or(0);
    if released > 0 {
        release_result_memory(state, released);
        let _ = persist_job(&state.store, record);
    }
}

fn reserve_result_memory(
    state: &AppState,
    requested: usize,
    maximum: usize,
) -> Result<(), (axum::http::StatusCode, Json<Value>)> {
    let requested = requested as u64;
    let maximum = maximum as u64;
    let mut current = state.reserved_result_bytes.load(Ordering::Acquire);
    loop {
        let Some(next) = current.checked_add(requested) else {
            return Err(service_unavailable("global result reservation overflow"));
        };
        if next > maximum {
            return Err(service_unavailable(format!(
                "global bounded result memory is full: reserved {current}, requested {requested}, maximum {maximum}"
            )));
        }
        match state.reserved_result_bytes.compare_exchange_weak(
            current,
            next,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return Ok(()),
            Err(observed) => current = observed,
        }
    }
}

fn release_result_memory(state: &AppState, released: usize) {
    state
        .reserved_result_bytes
        .fetch_sub(released as u64, Ordering::AcqRel);
}

fn preflight_combinations<I>(
    engine: Arc<dyn StrategyEngine>,
    base: &StrategyConfig,
    objective: &str,
    combinations: I,
    record: &Arc<Mutex<JobRecord>>,
    store: &JobStore,
    memory: usize,
    maximum: u64,
) -> Result<(), String>
where
    I: Iterator<Item = ComboOverride>,
{
    let job_id = record
        .lock()
        .map_err(|_| "job state lock poisoned".to_string())?
        .job_id
        .clone();
    store.clear_failures(&job_id).map_err(|error| error.to_string())?;
    update_job(record, |job| job.failed = 0);
    let chunk_size = env_u64("RUST_SHADOW_OPTIMIZER_CHUNK", 256) as usize;
    let limits = BatchLimits {
        max_combinations: maximum,
        chunk_size,
        memory_budget_bytes: memory / 4,
        estimated_bytes_per_combo: 32 * 1024,
    };
    run_optimization_iterator_streaming(engine, base, combinations, objective, limits, |results| {
        if is_cancelled(record) {
            return Err(algotest_optimizer::OptimizerError::Serialization(
                "optimization cancelled during execution preflight".into(),
            ));
        }
        let failed = results
            .iter()
            .filter(|result| !result.is_success())
            .map(serde_json::to_value)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| {
                algotest_optimizer::OptimizerError::Serialization(error.to_string())
            })?;
        if !failed.is_empty() {
            store.append_failures(&job_id, &failed).map_err(|error| {
                algotest_optimizer::OptimizerError::Serialization(error.to_string())
            })?;
            update_job(record, |job| job.failed += failed.len() as u64);
        }
        Ok(())
    })
    .map_err(|error| error.to_string())?;
    let failed = store
        .failure_count(&job_id)
        .map_err(|error| error.to_string())?;
    persist_job(store, record)?;
    if failed > 0 {
        Err(format!(
            "execution preflight rejected {failed} combination(s); no success results were published; inspect /api/optimize/jobs/{job_id}/failures"
        ))
    } else {
        Ok(())
    }
}

fn is_cancelled(record: &Arc<Mutex<JobRecord>>) -> bool {
    record
        .lock()
        .map(|job| job.cancel_requested)
        .unwrap_or(true)
}

fn random_seed() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn env_u64(name: &str, fallback: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .filter(|value| *value > 0)
        .unwrap_or(fallback)
}

fn parse_date(value: Option<&str>, name: &str) -> Result<NaiveDate, String> {
    let value = value.ok_or_else(|| format!("{name} is required"))?;
    let prefix = value
        .get(..10)
        .ok_or_else(|| format!("{name} must be YYYY-MM-DD"))?;
    NaiveDate::parse_from_str(prefix, "%Y-%m-%d").map_err(|_| format!("{name} must be YYYY-MM-DD"))
}

/// Canonical, de-duplicated symbol set a strategy needs — also the cache key's
/// symbol component, so the loaded market is independent of leg ordering.
fn strategy_symbols(strategy: &StrategyConfig) -> Vec<String> {
    let mut symbols = vec![strategy.index.clone()];
    if strategy.multi_index_mode {
        symbols.extend(strategy.legs.iter().filter_map(|leg| leg.index.clone()));
    }
    if !strategy.midcap_legs.is_empty() {
        let symbol = strategy
            .extra
            .get("midcap_symbol")
            .and_then(|value| value.as_str())
            .unwrap_or("NIFTYMIDCAP100");
        symbols.push(symbol.into());
    }
    for symbol in &mut symbols {
        *symbol = symbol.trim().to_ascii_uppercase();
    }
    symbols.retain(|symbol| !symbol.is_empty());
    symbols.sort();
    symbols.dedup();
    symbols
}

struct CachedMarket {
    symbols: Vec<String>,
    from: NaiveDate,
    to: NaiveDate,
    version: i64,
    market: Arc<CsvMarketDataSet>,
}

/// Process-global single-entry warm cache of the loaded market. Keyed only on
/// (symbols, range, data-version) — NOT the strategy — so iterating legs on a
/// fixed date range hits the cache. Single entry + evict-before-load keeps at
/// most one market resident, which is OOM-safe under the single compute permit.
/// The market depends only on the data, never on the strategy, so reuse is exact.
fn market_cache() -> &'static std::sync::Mutex<Option<CachedMarket>> {
    static CACHE: std::sync::OnceLock<std::sync::Mutex<Option<CachedMarket>>> =
        std::sync::OnceLock::new();
    CACHE.get_or_init(|| std::sync::Mutex::new(None))
}

fn load_strategy_market(
    repository_root: impl AsRef<FsPath>,
    strategy: &StrategyConfig,
    from: NaiveDate,
    to: NaiveDate,
    budget_bytes: usize,
) -> Result<Arc<CsvMarketDataSet>, MarketDataError> {
    let symbols = strategy_symbols(strategy);
    let database_url = std::env::var("RUST_SHADOW_DATABASE_URL")
        .ok()
        .filter(|url| !url.trim().is_empty());
    let cache_on = std::env::var("RUST_SHADOW_MARKET_CACHE")
        .map(|value| value.trim() != "0")
        .unwrap_or(true);
    // Version guard: a logged re-import bumps the key so the cache never serves
    // stale data. CSV mode (no DB) uses 0 and relies on restart.
    let version = match (&database_url, cache_on) {
        (Some(url), true) => algotest_engine::market_data::data_version(url),
        _ => 0,
    };
    if cache_on {
        // Scope the guard so it is dropped before the eviction re-lock below
        // (std Mutex is non-reentrant — re-locking while held deadlocks).
        {
            let guard = market_cache().lock().unwrap();
            if let Some(entry) = guard.as_ref() {
                if entry.symbols == symbols
                    && entry.from == from
                    && entry.to == to
                    && entry.version == version
                {
                    return Ok(entry.market.clone());
                }
            }
        }
        // Evict the stale/other market BEFORE loading so peak residency is one
        // market, not two (with the single compute permit this cannot race).
        *market_cache().lock().unwrap() = None;
    }
    let loaded = match &database_url {
        Some(url) => CsvMarketDataSet::load_from_postgres(
            url,
            repository_root,
            &symbols,
            from,
            to,
            budget_bytes,
        )?,
        None => CsvMarketDataSet::load(repository_root, &symbols, from, to, budget_bytes)?,
    };
    let market = Arc::new(loaded);
    if cache_on {
        *market_cache().lock().unwrap() = Some(CachedMarket {
            symbols,
            from,
            to,
            version,
            market: market.clone(),
        });
    }
    Ok(market)
}

fn admitted_memory_bytes() -> Result<usize, String> {
    const SAFETY_HEADROOM: u64 = 512 * 1024 * 1024;
    let configured = env_u64("RUST_SHADOW_MEMORY_LIMIT_MB", 2048)
        .checked_mul(1024 * 1024)
        .ok_or_else(|| "configured memory budget overflow".to_string())?;
    let cgroup_available = cgroup_value("/sys/fs/cgroup/memory.max")
        .zip(cgroup_value("/sys/fs/cgroup/memory.current"))
        .map(|(maximum, current)| {
            maximum
                .saturating_sub(current)
                .saturating_sub(SAFETY_HEADROOM)
        });
    let admitted = cgroup_available.map_or(configured, |available| configured.min(available));
    if admitted < 64 * 1024 * 1024 {
        return Err("insufficient memory headroom; request was not started".into());
    }
    usize::try_from(admitted).map_err(|_| "memory budget is not representable".into())
}

fn cgroup_value(path: &str) -> Option<u64> {
    std::fs::read_to_string(path).ok()?.trim().parse().ok()
}

fn bad_request(error: impl std::fmt::Display) -> (axum::http::StatusCode, Json<Value>) {
    (
        axum::http::StatusCode::BAD_REQUEST,
        Json(json!({"detail":error.to_string(), "engine":ENGINE, "authoritative":false})),
    )
}

fn service_unavailable(error: impl std::fmt::Display) -> (axum::http::StatusCode, Json<Value>) {
    (
        axum::http::StatusCode::SERVICE_UNAVAILABLE,
        Json(json!({"detail":error.to_string(), "engine":ENGINE, "authoritative":false})),
    )
}

fn not_found(job_id: &str) -> (axum::http::StatusCode, Json<Value>) {
    (
        axum::http::StatusCode::NOT_FOUND,
        Json(json!({"detail":format!("unknown Rust shadow job {job_id}"),"engine":ENGINE})),
    )
}

fn lock_error(error: impl std::fmt::Display) -> (axum::http::StatusCode, Json<Value>) {
    service_unavailable(format!("job state unavailable: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> AppState {
        AppState {
            compute_slot: Arc::new(tokio::sync::Semaphore::new(1)),
            repository_root: PathBuf::new(),
            jobs: RwLock::new(HashMap::new()),
            next_job_id: AtomicU64::new(1),
            reserved_result_bytes: AtomicU64::new(0),
            store: Arc::new(
                JobStore::open(
                    std::env::temp_dir()
                        .join(format!("algotest-api-test-{}.sqlite3", std::process::id())),
                )
                .unwrap(),
            ),
        }
    }

    #[test]
    fn global_result_reservations_fail_before_overcommit() {
        let state = state();
        reserve_result_memory(&state, 60, 100).expect("first reservation");
        assert!(reserve_result_memory(&state, 41, 100).is_err());
        assert_eq!(state.reserved_result_bytes.load(Ordering::Acquire), 60);
        release_result_memory(&state, 60);
        assert_eq!(state.reserved_result_bytes.load(Ordering::Acquire), 0);
    }

    #[test]
    fn wow_mom_buckets_parent_trade_without_leg_double_counting() {
        let row = |leg_id, leg_pnl| TradeRow {
            trade_id: 1,
            leg_id,
            entry_date: "2024-01-10".into(),
            exit_date: "2024-01-15".into(),
            expiry: "2024-01-18".into(),
            entry_spot: 100.0,
            leg_pnl,
            ..Default::default()
        };
        let result = EngineResult {
            trades: vec![row(2, -1.0), row(1, 3.0)],
            summary: SummaryMetrics::default(),
        };
        let block = wow_mom_block(7, &result, false).unwrap();
        assert_eq!(block.combo_id, 7);
        assert_eq!(block.wow[&2024][&3], 0.02);
        assert_eq!(block.mom[&2024][&1], 0.02);
        let workbook = render_wow_mom_workbook(&[block]).unwrap();
        assert!(workbook.starts_with(b"PK"));
        let mut archive = zip::ZipArchive::new(std::io::Cursor::new(workbook)).unwrap();
        let mut xml = String::new();
        std::io::Read::read_to_string(&mut archive.by_name("xl/workbook.xml").unwrap(), &mut xml).unwrap();
        for name in ["WOW Summary", "MOM Summary", "WOW Min Pivots", "MOM Min Pivots"] {
            assert!(xml.contains(name), "missing worksheet {name}");
        }
    }

    #[test]
    fn drawdown_skips_positive_runs_and_spans_missing_periods() {
        let drawdown = period_max_drawdown([0.05, -0.02, -0.03, 0.01, 0.06].into_iter());
        assert!((drawdown - -0.05).abs() < 1e-12);
    }

    #[test]
    fn optimizer_request_retains_frontend_metadata_for_replay() {
        let request: OptimizeJobRequest = serde_json::from_value(json!({
            "base_payload": {"index":"NIFTY", "legs":[{"position":"SELL", "option_type":"CE"}]},
            "param_specs": [], "method":"exhaustive", "objective":"total_pnl",
            "parallelism": 8, "zip_naming": {"level1":"sweep"},
            "node_id":"shadow-canary", "auto_download":true
        })).unwrap();
        assert_eq!(request.parallelism, Some(8));
        assert_eq!(request.zip_naming.unwrap()["level1"], "sweep");
        assert_eq!(request.node_id.as_deref(), Some("shadow-canary"));
        assert_eq!(request.auto_download, Some(true));
    }

    #[test]
    fn zip_naming_root_matches_nested_production_layout_safely() {
        let value = json!({"level1":"With Adj/Rollover", "level2":"Fresh", "level3":"A:B"});
        assert_eq!(zip_naming_root(Some(&value)), "With Adj_Rollover/Fresh/A_B");
        assert_eq!(zip_naming_root(None), "tradesheets");
    }

    #[test]
    fn combo_archive_label_is_descriptive_and_safe() {
        let combo = ComboOverride {
            combo_id: 1000,
            values: BTreeMap::from([
                ("legs[0].expiry".into(), json!("WEEKLY")),
                ("legs[0].strike".into(), json!("ATM/OTM")),
            ]),
        };
        let label = combo_archive_label(&combo);
        assert!(label.starts_with("1000_"));
        assert!(label.contains("legs_0__expiry_WEEKLY"));
        assert!(!label.contains('/'));
    }
}
