use chrono::NaiveDate;
use rusqlite::{Connection, params};
use std::path::Path;

pub struct Manifest {
    conn: Connection,
}

impl Manifest {
    pub fn open(path: &Path) -> anyhow::Result<Self> {
        if let Some(p) = path.parent() { std::fs::create_dir_all(p)?; }
        let conn = Connection::open(path)?;
        conn.execute_batch("
            CREATE TABLE IF NOT EXISTS imports (
                symbol      TEXT    NOT NULL,
                trade_date  TEXT    NOT NULL,
                sha256      TEXT    NOT NULL,
                row_count   INTEGER NOT NULL,
                ingested_at INTEGER NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            );
        ")?;
        Ok(Self { conn })
    }

    /// Returns the stored sha256 for (symbol, date), or None if not yet imported.
    pub fn check(&self, symbol: &str, date: NaiveDate) -> anyhow::Result<Option<String>> {
        use rusqlite::OptionalExtension;
        let date_str = date.format("%Y-%m-%d").to_string();
        let result: Option<String> = self.conn
            .query_row(
                "SELECT sha256 FROM imports WHERE symbol = ?1 AND trade_date = ?2",
                params![symbol, date_str],
                |row| row.get(0),
            )
            .optional()?;
        Ok(result)
    }

    /// Insert or replace the manifest record for (symbol, date).
    pub fn upsert(&self, symbol: &str, date: NaiveDate, sha256: &str, row_count: i32) -> anyhow::Result<()> {
        let date_str = date.format("%Y-%m-%d").to_string();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        self.conn.execute(
            "INSERT OR REPLACE INTO imports (symbol, trade_date, sha256, row_count, ingested_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![symbol, date_str, sha256, row_count, now],
        )?;
        Ok(())
    }
}

/// Compute SHA-256 hex of a byte slice.
pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(data);
    format!("{:x}", hash)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_check_none_then_upsert_then_check() {
        let dir = tempfile::tempdir().unwrap();
        let db = Manifest::open(&dir.path().join("manifest.db")).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();

        assert!(db.check("NIFTY", date).unwrap().is_none());
        db.upsert("NIFTY", date, "abc123def456", 60000).unwrap();
        assert_eq!(db.check("NIFTY", date).unwrap(), Some("abc123def456".into()));
    }

    #[test]
    fn test_upsert_replaces_on_new_sha() {
        let dir = tempfile::tempdir().unwrap();
        let db = Manifest::open(&dir.path().join("manifest.db")).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();

        db.upsert("NIFTY", date, "sha_v1", 60000).unwrap();
        db.upsert("NIFTY", date, "sha_v2", 60001).unwrap();
        assert_eq!(db.check("NIFTY", date).unwrap(), Some("sha_v2".into()));
    }

    #[test]
    fn test_sha256_hex_stable() {
        let h1 = sha256_hex(b"hello");
        let h2 = sha256_hex(b"hello");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64);
        assert_ne!(sha256_hex(b"hello"), sha256_hex(b"world"));
    }

    #[test]
    fn test_creates_parent_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let deep = dir.path().join("a/b/c/manifest.db");
        let db = Manifest::open(&deep).unwrap();
        let date = NaiveDate::from_ymd_opt(2025, 3, 1).unwrap();
        db.upsert("NIFTY", date, "xyz", 100).unwrap();
        assert!(deep.exists());
    }
}
