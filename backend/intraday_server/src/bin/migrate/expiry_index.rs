use chrono::NaiveDate;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// Maintains a `expiries.json` file mapping integer index → ISO date string.
///
/// Indices are **permanent once assigned** (append-only, never renumber).
/// New expiry dates get the next available index (appended at the end of the list).
pub struct ExpiryIndex {
    path: PathBuf,
    by_date: HashMap<NaiveDate, i16>,
    /// ordered[i] = date at index i; stable forever
    ordered: Vec<NaiveDate>,
}

impl ExpiryIndex {
    /// Load from path if it exists, else start empty.
    pub fn load_or_create(path: &Path) -> anyhow::Result<Self> {
        let mut by_date: HashMap<NaiveDate, i16> = HashMap::new();
        let mut ordered: Vec<NaiveDate> = Vec::new();

        if path.exists() {
            let content = std::fs::read_to_string(path)?;
            let map: HashMap<String, String> = serde_json::from_str(&content)?;

            // Parse integer keys and collect in order
            let mut pairs: Vec<(i16, NaiveDate)> = map
                .into_iter()
                .map(|(k, v)| {
                    let idx: i16 = k.parse().map_err(|_| {
                        anyhow::anyhow!("invalid index key in expiries.json: {k}")
                    })?;
                    let date = NaiveDate::parse_from_str(&v, "%Y-%m-%d").map_err(|_| {
                        anyhow::anyhow!("invalid date in expiries.json: {v}")
                    })?;
                    Ok((idx, date))
                })
                .collect::<anyhow::Result<Vec<_>>>()?;

            // Sort by index to rebuild `ordered` in the correct slot order
            pairs.sort_by_key(|(idx, _)| *idx);

            for (idx, date) in pairs {
                // Indices must be contiguous starting at 0
                if idx as usize != ordered.len() {
                    return Err(anyhow::anyhow!(
                        "expiries.json has non-contiguous index {idx} (expected {})",
                        ordered.len()
                    ));
                }
                ordered.push(date);
                by_date.insert(date, idx);
            }
        }

        Ok(Self {
            path: path.to_path_buf(),
            by_date,
            ordered,
        })
    }

    /// Get the index for a date.  Assigns a new index if not seen (appended at end).
    /// Once assigned, an index NEVER changes.
    pub fn get_or_insert(&mut self, date: NaiveDate) -> i16 {
        if let Some(&idx) = self.by_date.get(&date) {
            return idx;
        }
        let idx = self.ordered.len() as i16;
        self.ordered.push(date);
        self.by_date.insert(date, idx);
        idx
    }

    /// Return the index for a date if it has already been assigned.
    pub fn get(&self, date: NaiveDate) -> Option<i16> {
        self.by_date.get(&date).copied()
    }

    /// Number of expiry dates currently tracked.
    pub fn len(&self) -> usize {
        self.ordered.len()
    }

    /// Persist to disk atomically (write .tmp → rename).
    pub fn save(&self) -> anyhow::Result<()> {
        let mut map = serde_json::Map::with_capacity(self.ordered.len());
        for (idx, date) in self.ordered.iter().enumerate() {
            map.insert(idx.to_string(), serde_json::Value::String(date.format("%Y-%m-%d").to_string()));
        }
        let json = serde_json::to_string_pretty(&serde_json::Value::Object(map))?;

        let mut tmp_path = self.path.clone();
        let mut tmp_name = self.path.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "expiries.json".to_string());
        tmp_name.push_str(".tmp");
        tmp_path.set_file_name(tmp_name);

        std::fs::write(&tmp_path, &json)?;
        std::fs::rename(&tmp_path, &self.path)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    #[test]
    fn test_append_only_stable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("expiries.json");
        let mut idx = ExpiryIndex::load_or_create(&path).unwrap();

        let d1 = NaiveDate::from_ymd_opt(2025, 1, 23).unwrap();
        let d2 = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap(); // earlier date, inserted second
        let d3 = NaiveDate::from_ymd_opt(2025, 2, 27).unwrap();

        let i1 = idx.get_or_insert(d1); // first insertion → index 0
        let i2 = idx.get_or_insert(d2); // second insertion → index 1 (NOT 0)
        let i3 = idx.get_or_insert(d3); // third insertion → index 2

        assert_eq!(i1, 0);
        assert_eq!(i2, 1);
        assert_eq!(i3, 2);

        // Indices must be stable — re-querying same date returns same index
        assert_eq!(idx.get_or_insert(d1), i1);
        assert_eq!(idx.get_or_insert(d2), i2);
        assert_eq!(idx.get_or_insert(d3), i3);
    }

    #[test]
    fn test_save_and_reload() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("expiries.json");

        let d1 = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let d2 = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();

        {
            let mut idx = ExpiryIndex::load_or_create(&path).unwrap();
            idx.get_or_insert(d1);
            idx.get_or_insert(d2);
            idx.save().unwrap();
        }

        // Reload and verify same indices
        let idx2 = ExpiryIndex::load_or_create(&path).unwrap();
        assert_eq!(idx2.get(d1), Some(0));
        assert_eq!(idx2.get(d2), Some(1));
        assert_eq!(idx2.len(), 2);
    }
}
