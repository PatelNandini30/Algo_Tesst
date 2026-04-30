use axum::{extract::State, Json};
use crate::{AppState, cache};
use serde_json::{json, Value};
use std::path::Path;

pub async fn handler(State(state): State<AppState>) -> Json<Value> {
    let data_dir_clone = state.data_dir.clone();
    let scan_result = tokio::task::spawn_blocking(move || {
        let data_dir = Path::new(&data_dir_clone);
        let symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
        let mut snapshot_counts = std::collections::HashMap::new();
        let mut earliest: Option<String> = None;
        let mut latest: Option<String> = None;

        for sym in &symbols {
            let snaps = data_dir.join(sym).join("snapshots");
            if snaps.exists() {
                let count = std::fs::read_dir(&snaps)
                    .map(|rd| rd.filter_map(|e| e.ok())
                        .filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("arrow"))
                        .count())
                    .unwrap_or(0);
                snapshot_counts.insert(sym.to_string(), count);
                if count > 0 {
                    let mut dates: Vec<String> = std::fs::read_dir(&snaps)
                        .unwrap()
                        .filter_map(|e| e.ok())
                        .filter_map(|e| {
                            let p = e.path();
                            if p.extension()?.to_str()? == "arrow" {
                                p.file_stem()?.to_str().map(|s| s.to_string())
                            } else { None }
                        })
                        .collect();
                    dates.sort();
                    if let Some(d) = dates.first() {
                        if earliest.as_ref().map_or(true, |e| d < e) { earliest = Some(d.clone()); }
                    }
                    if let Some(d) = dates.last() {
                        if latest.as_ref().map_or(true, |l| d > l) { latest = Some(d.clone()); }
                    }
                }
            } else {
                snapshot_counts.insert(sym.to_string(), 0);
            }
        }
        (snapshot_counts, earliest, latest)
    }).await.unwrap_or_else(|_| (Default::default(), None, None));
    let (snapshot_counts, earliest, latest) = scan_result;

    let mut redis = state.redis.clone();
    let redis_ok = cache::ping(&mut redis).await;

    Json(json!({
        "service": "intraday",
        "redis_ok": redis_ok,
        "snapshot_counts": snapshot_counts,
        "date_range": { "earliest": earliest, "latest": latest },
    }))
}
