use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum StorageError {
    #[error("state directory unavailable: {0}")]
    Io(#[from] std::io::Error),
    #[error("SQLite state failure: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("state JSON failure: {0}")]
    Json(#[from] serde_json::Error),
    #[error("state lock poisoned")]
    Poisoned,
    #[error("integer is outside SQLite range")]
    IntegerRange,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StoredJob {
    pub job_id: String,
    pub status: String,
    pub phase: String,
    pub total: u64,
    pub done: u64,
    pub failed: u64,
    pub objective: String,
    pub error: Option<String>,
    pub reserved_bytes: usize,
    #[serde(default)]
    pub request_json: Option<String>,
    #[serde(default)]
    pub resolved_seed: Option<u64>,
    #[serde(default)]
    pub created_at_ms: u64,
    #[serde(default)]
    pub started_at_ms: Option<u64>,
    #[serde(default)]
    pub finished_at_ms: Option<u64>,
}

pub struct JobStore {
    connection: Mutex<Connection>,
    path: PathBuf,
}

impl JobStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, StorageError> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let connection = Connection::open(&path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "NORMAL")?;
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                total INTEGER NOT NULL,
                done INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                objective TEXT NOT NULL,
                error TEXT,
                reserved_bytes INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
             );
             CREATE TABLE IF NOT EXISTS results (
                job_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(job_id, ordinal),
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
             );
             CREATE INDEX IF NOT EXISTS results_job_ordinal
                ON results(job_id, ordinal);
             CREATE TABLE IF NOT EXISTS failures (
                job_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY(job_id, ordinal),
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
             );
             CREATE INDEX IF NOT EXISTS failures_job_ordinal
                ON failures(job_id, ordinal);",
        )?;
        add_column_if_missing(&connection, "jobs", "request_json", "TEXT")?;
        add_column_if_missing(&connection, "jobs", "resolved_seed", "INTEGER")?;
        add_column_if_missing(&connection, "jobs", "created_at_ms", "INTEGER NOT NULL DEFAULT 0")?;
        add_column_if_missing(&connection, "jobs", "started_at_ms", "INTEGER")?;
        add_column_if_missing(&connection, "jobs", "finished_at_ms", "INTEGER")?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        Ok(Self {
            connection: Mutex::new(connection),
            path,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn upsert_job(&self, job: &StoredJob) -> Result<(), StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        connection.execute(
            "INSERT INTO jobs(job_id,status,phase,total,done,failed,objective,error,reserved_bytes,request_json,resolved_seed,created_at_ms,started_at_ms,finished_at_ms)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)
             ON CONFLICT(job_id) DO UPDATE SET
               status=excluded.status, phase=excluded.phase, total=excluded.total,
               done=excluded.done, failed=excluded.failed, objective=excluded.objective,
               error=excluded.error, reserved_bytes=excluded.reserved_bytes,
               request_json=excluded.request_json, resolved_seed=excluded.resolved_seed,
               created_at_ms=excluded.created_at_ms, started_at_ms=excluded.started_at_ms,
               finished_at_ms=excluded.finished_at_ms,
               updated_at=CURRENT_TIMESTAMP",
            params![
                job.job_id,
                job.status,
                job.phase,
                to_i64(job.total)?,
                to_i64(job.done)?,
                to_i64(job.failed)?,
                job.objective,
                job.error,
                to_i64(job.reserved_bytes as u64)?,
                job.request_json,
                job.resolved_seed.map(to_i64).transpose()?,
                to_i64(job.created_at_ms)?,
                job.started_at_ms.map(to_i64).transpose()?,
                job.finished_at_ms.map(to_i64).transpose()?,
            ],
        )?;
        Ok(())
    }

    pub fn append_results(&self, job_id: &str, values: &[Value]) -> Result<(), StorageError> {
        if values.is_empty() {
            return Ok(());
        }
        let mut connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let start: i64 = transaction.query_row(
            "SELECT COALESCE(MAX(ordinal) + 1, 0) FROM results WHERE job_id=?1",
            [job_id],
            |row| row.get(0),
        )?;
        {
            let mut statement = transaction
                .prepare("INSERT INTO results(job_id,ordinal,payload) VALUES(?1,?2,?3)")?;
            for (offset, value) in values.iter().enumerate() {
                statement.execute(params![
                    job_id,
                    start + to_i64(offset as u64)?,
                    serde_json::to_string(value)?,
                ])?;
            }
        }
        transaction.commit()?;
        Ok(())
    }

    pub fn append_failures(&self, job_id: &str, values: &[Value]) -> Result<(), StorageError> {
        append_values(&self.connection, "failures", job_id, values)
    }

    pub fn failures(
        &self,
        job_id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<Vec<Value>, StorageError> {
        page_values(&self.connection, "failures", job_id, offset, limit)
    }

    pub fn failure_count(&self, job_id: &str) -> Result<u64, StorageError> {
        count_values(&self.connection, "failures", job_id)
    }

    pub fn clear_failures(&self, job_id: &str) -> Result<(), StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        connection.execute("DELETE FROM failures WHERE job_id=?1", [job_id])?;
        Ok(())
    }

    pub fn results(
        &self,
        job_id: &str,
        offset: usize,
        limit: usize,
    ) -> Result<Vec<Value>, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let mut statement = connection.prepare(
            "SELECT payload FROM results WHERE job_id=?1 ORDER BY ordinal LIMIT ?2 OFFSET ?3",
        )?;
        let rows = statement.query_map(
            params![job_id, to_i64(limit as u64)?, to_i64(offset as u64)?],
            |row| row.get::<_, String>(0),
        )?;
        rows.map(|row| Ok(serde_json::from_str(&row?)?)).collect()
    }

    pub fn results_sorted(
        &self,
        job_id: &str,
        offset: usize,
        limit: usize,
        metric: &str,
        descending: bool,
    ) -> Result<Vec<Value>, StorageError> {
        if metric.is_empty()
            || !metric
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return self.results(job_id, offset, limit);
        }
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let direct_path = format!("$.summary.{metric}");
        let extra_path = format!("$.summary.extra.{metric}");
        let mut statement = connection.prepare(
            "SELECT payload FROM results WHERE job_id=?1
             ORDER BY
               CASE WHEN ?6=1 THEN COALESCE(
                 CAST(json_extract(payload, ?4) AS REAL),
                 CAST(json_extract(payload, ?5) AS REAL)
               ) END DESC,
               CASE WHEN ?6=0 THEN COALESCE(
                 CAST(json_extract(payload, ?4) AS REAL),
                 CAST(json_extract(payload, ?5) AS REAL)
               ) END ASC,
               ordinal ASC
             LIMIT ?2 OFFSET ?3",
        )?;
        let rows = statement.query_map(
            params![
                job_id,
                to_i64(limit as u64)?,
                to_i64(offset as u64)?,
                direct_path,
                extra_path,
                if descending { 1 } else { 0 },
            ],
            |row| row.get::<_, String>(0),
        )?;
        rows.map(|row| Ok(serde_json::from_str(&row?)?)).collect()
    }

    pub fn load_jobs(&self) -> Result<Vec<StoredJob>, StorageError> {
        self.load_recent_jobs(10_000)
    }

    pub fn load_recent_jobs(&self, limit: usize) -> Result<Vec<StoredJob>, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let mut statement = connection.prepare(
            "SELECT job_id,status,phase,total,done,failed,objective,error,reserved_bytes,request_json,resolved_seed,created_at_ms,started_at_ms,finished_at_ms
             FROM jobs ORDER BY updated_at DESC,job_id DESC LIMIT ?1",
        )?;
        let rows = statement.query_map([to_i64(limit as u64)?], |row| {
            Ok(StoredJob {
                job_id: row.get(0)?,
                status: row.get(1)?,
                phase: row.get(2)?,
                total: from_i64(row.get(3)?)?,
                done: from_i64(row.get(4)?)?,
                failed: from_i64(row.get(5)?)?,
                objective: row.get(6)?,
                error: row.get(7)?,
                reserved_bytes: usize::try_from(from_i64(row.get(8)?)?)
                    .map_err(|_| rusqlite::Error::IntegralValueOutOfRange(8, i64::MAX))?,
                request_json: row.get(9)?,
                resolved_seed: row.get::<_, Option<i64>>(10)?.map(from_i64).transpose()?,
                created_at_ms: from_i64(row.get(11)?)?,
                started_at_ms: row.get::<_, Option<i64>>(12)?.map(from_i64).transpose()?,
                finished_at_ms: row.get::<_, Option<i64>>(13)?.map(from_i64).transpose()?,
            })
        })?;
        let mut jobs = rows
            .collect::<Result<Vec<_>, _>>()
            .map_err(StorageError::from)?;
        jobs.reverse();
        Ok(jobs)
    }

    pub fn mark_interrupted_jobs(&self) -> Result<usize, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        Ok(connection.execute(
            "UPDATE jobs SET status='failed', phase='interrupted',
             error='Rust shadow process restarted before strict completion',
             updated_at=CURRENT_TIMESTAMP WHERE status IN ('queued','running')",
            [],
        )?)
    }

    pub fn delete_job(&self, job_id: &str) -> Result<bool, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        Ok(connection.execute("DELETE FROM jobs WHERE job_id=?1", [job_id])? > 0)
    }

    pub fn result_count(&self, job_id: &str) -> Result<u64, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let count = connection
            .query_row(
                "SELECT COUNT(*) FROM results WHERE job_id=?1",
                [job_id],
                |row| row.get::<_, i64>(0),
            )
            .optional()?
            .unwrap_or(0);
        from_i64(count).map_err(StorageError::from)
    }

    pub fn result_by_combo_id(
        &self,
        job_id: &str,
        combo_id: u64,
    ) -> Result<Option<Value>, StorageError> {
        let connection = self.connection.lock().map_err(|_| StorageError::Poisoned)?;
        let payload = connection
            .query_row(
                "SELECT payload FROM results
                 WHERE job_id=?1 AND CAST(json_extract(payload, '$.combo_id') AS INTEGER)=?2
                 LIMIT 1",
                params![job_id, to_i64(combo_id)?],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        payload
            .map(|value| serde_json::from_str(&value))
            .transpose()
            .map_err(StorageError::from)
    }
}

fn append_values(
    connection: &Mutex<Connection>,
    table: &str,
    job_id: &str,
    values: &[Value],
) -> Result<(), StorageError> {
    if values.is_empty() {
        return Ok(());
    }
    let mut connection = connection.lock().map_err(|_| StorageError::Poisoned)?;
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    let start: i64 = transaction.query_row(
        &format!("SELECT COALESCE(MAX(ordinal) + 1, 0) FROM {table} WHERE job_id=?1"),
        [job_id],
        |row| row.get(0),
    )?;
    let sql = format!("INSERT INTO {table}(job_id,ordinal,payload) VALUES(?1,?2,?3)");
    {
        let mut statement = transaction.prepare(&sql)?;
        for (offset, value) in values.iter().enumerate() {
            statement.execute(params![
                job_id,
                start + to_i64(offset as u64)?,
                serde_json::to_string(value)?,
            ])?;
        }
    }
    transaction.commit()?;
    Ok(())
}

fn page_values(
    connection: &Mutex<Connection>,
    table: &str,
    job_id: &str,
    offset: usize,
    limit: usize,
) -> Result<Vec<Value>, StorageError> {
    let connection = connection.lock().map_err(|_| StorageError::Poisoned)?;
    let mut statement = connection.prepare(&format!(
        "SELECT payload FROM {table} WHERE job_id=?1 ORDER BY ordinal LIMIT ?2 OFFSET ?3"
    ))?;
    let rows = statement.query_map(
        params![job_id, to_i64(limit as u64)?, to_i64(offset as u64)?],
        |row| row.get::<_, String>(0),
    )?;
    rows.map(|row| Ok(serde_json::from_str(&row?)?)).collect()
}

fn count_values(
    connection: &Mutex<Connection>,
    table: &str,
    job_id: &str,
) -> Result<u64, StorageError> {
    let connection = connection.lock().map_err(|_| StorageError::Poisoned)?;
    let count = connection.query_row(
        &format!("SELECT COUNT(*) FROM {table} WHERE job_id=?1"),
        [job_id],
        |row| row.get::<_, i64>(0),
    )?;
    from_i64(count).map_err(StorageError::from)
}

fn add_column_if_missing(
    connection: &Connection,
    table: &str,
    column: &str,
    definition: &str,
) -> Result<(), StorageError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let columns = statement.query_map([], |row| row.get::<_, String>(1))?;
    for existing in columns {
        if existing? == column {
            return Ok(());
        }
    }
    connection.execute(
        &format!("ALTER TABLE {table} ADD COLUMN {column} {definition}"),
        [],
    )?;
    Ok(())
}

fn to_i64(value: u64) -> Result<i64, StorageError> {
    i64::try_from(value).map_err(|_| StorageError::IntegerRange)
}

fn from_i64(value: i64) -> Result<u64, rusqlite::Error> {
    u64::try_from(value).map_err(|_| rusqlite::Error::IntegralValueOutOfRange(0, value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn persists_and_pages_results_without_a_result_vector() {
        let directory = std::env::temp_dir().join(format!(
            "algotest-rust-storage-{}-{}",
            std::process::id(),
            std::thread::current().name().unwrap_or("test")
        ));
        let store = JobStore::open(directory.join("jobs.sqlite3")).unwrap();
        let job = StoredJob {
            job_id: "job-1".into(),
            status: "running".into(),
            phase: "optimizing".into(),
            total: 3,
            done: 0,
            failed: 0,
            objective: "total_pnl".into(),
            error: None,
            reserved_bytes: 100,
            request_json: Some("{}".into()),
            resolved_seed: Some(42),
            created_at_ms: 100,
            started_at_ms: Some(110),
            finished_at_ms: None,
        };
        store.upsert_job(&job).unwrap();
        let restored = store.load_jobs().unwrap();
        assert_eq!(restored[0].created_at_ms, 100);
        assert_eq!(restored[0].started_at_ms, Some(110));
        assert_eq!(restored[0].finished_at_ms, None);
        store
            .append_results(
                "job-1",
                &[
                    json!({"combo_id":1,"n":1,"summary":{"total_pnl":20}}),
                    json!({"combo_id":2,"n":2,"summary":{"total_pnl":10}}),
                    json!({"combo_id":3,"n":3,"summary":{"total_pnl":30}}),
                ],
            )
            .unwrap();
        assert_eq!(store.result_count("job-1").unwrap(), 3);
        store
            .append_failures(
                "job-1",
                &[json!({"combo_id":7,"error":"missing strike"})],
            )
            .unwrap();
        assert_eq!(store.failure_count("job-1").unwrap(), 1);
        assert_eq!(store.failures("job-1", 0, 10).unwrap()[0]["combo_id"], 7);
        store.clear_failures("job-1").unwrap();
        assert_eq!(store.failure_count("job-1").unwrap(), 0);
        assert_eq!(
            store.results("job-1", 1, 1).unwrap(),
            vec![json!({"combo_id":2,"n":2,"summary":{"total_pnl":10}})]
        );
        assert_eq!(
            store.result_by_combo_id("job-1", 3).unwrap(),
            Some(json!({"combo_id":3,"n":3,"summary":{"total_pnl":30}}))
        );
        let sorted = store
            .results_sorted("job-1", 0, 3, "total_pnl", true)
            .unwrap();
        assert_eq!(sorted[0]["combo_id"], 3);
        assert_eq!(sorted[2]["combo_id"], 2);
        assert_eq!(store.mark_interrupted_jobs().unwrap(), 1);
        assert_eq!(store.load_jobs().unwrap()[0].status, "failed");
        assert!(store.delete_job("job-1").unwrap());
        let _ = fs::remove_dir_all(directory);
    }
}
