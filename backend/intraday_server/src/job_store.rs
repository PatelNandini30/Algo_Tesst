use crate::cache::{get_bytes, get_str, set_bytes_ex, set_str_ex, setnx_ex, RedisConn};
use serde::{Deserialize, Serialize};

const JOB_TTL: u64 = 3600;       // 1 hour
const RESULT_TTL: u64 = 604800;  // 7 days
const INFLIGHT_TTL: u64 = 120;   // 2 minutes

#[derive(Serialize, Deserialize, Debug, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus { Queued, Running, Done, Failed }

#[derive(Serialize, Deserialize, Debug)]
pub struct JobState {
    pub status: JobStatus,
    pub cache_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub started_at: Option<String>,
}

pub fn result_key(cache_key: &str)   -> String { format!("intraday:result:{cache_key}") }
pub fn job_key(job_id: &str)         -> String { format!("intraday:job:{job_id}") }
pub fn inflight_key(cache_key: &str) -> String { format!("intraday:inflight:{cache_key}") }

pub async fn get_result(conn: &mut RedisConn, cache_key: &str) -> Option<Vec<u8>> {
    get_bytes(conn, &result_key(cache_key)).await
}

pub async fn store_result(conn: &mut RedisConn, cache_key: &str, bytes: &[u8]) {
    set_bytes_ex(conn, &result_key(cache_key), bytes, RESULT_TTL).await;
}

pub async fn get_job(conn: &mut RedisConn, job_id: &str) -> Option<JobState> {
    let s = get_str(conn, &job_key(job_id)).await?;
    serde_json::from_str(&s).ok()
}

pub async fn set_job(conn: &mut RedisConn, job_id: &str, state: &JobState) {
    if let Ok(s) = serde_json::to_string(state) {
        set_str_ex(conn, &job_key(job_id), &s, JOB_TTL).await;
    }
}

/// Returns existing job_id if another identical request is already in flight.
pub async fn try_claim_inflight(conn: &mut RedisConn, cache_key: &str, job_id: &str) -> Option<String> {
    let claimed = setnx_ex(conn, &inflight_key(cache_key), job_id, INFLIGHT_TTL).await;
    if claimed {
        None // we claimed it, no existing job
    } else {
        get_str(conn, &inflight_key(cache_key)).await
    }
}
