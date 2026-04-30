use memmap2::Mmap;
use std::fs::File;
use std::path::Path;

pub const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16;
pub const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY;
pub const CHAIN_STRIKES: usize = 11;
pub const CHAIN_TYPES: usize = 2;
pub const CHAIN_FIELDS: usize = 4;
pub const EXPIRY_SIZE: usize =
    2 + MINUTES * 4 + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4;

pub struct Snapshot {
    mmap: Mmap,
    pub expiry_count: usize,
    pub date_days: i32,
    pub symbol: String,
    pub minute_count: usize,
}

impl Snapshot {
    pub fn open(path: &Path) -> std::io::Result<Self> {
        let file = File::open(path)?;
        // SAFETY: file is opened read-only; snapshot files are write-once historical records.
        // SIGBUS on disk I/O error is an accepted risk and will terminate the process.
        let mmap = unsafe { Mmap::map(&file)? };
        if mmap.len() < HEADER_SIZE {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "file too small"));
        }
        if &mmap[0..4] != b"ITDS" {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "bad magic"));
        }
        if mmap[4] != 1 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unsupported format version: {}", mmap[4]),
            ));
        }
        let symbol = std::str::from_utf8(&mmap[5..21])
            .unwrap_or("")
            .trim_end_matches('\0')
            .to_string();
        let date_days = i32::from_le_bytes(mmap[21..25].try_into().unwrap());
        let expiry_count = mmap[25] as usize;
        let minute_count = u16::from_le_bytes(mmap[26..28].try_into().unwrap()) as usize;
        let min_len = HEADER_SIZE + SPOT_SIZE + expiry_count * EXPIRY_SIZE;
        if mmap.len() < min_len {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("file too small: {} < {}", mmap.len(), min_len),
            ));
        }
        Ok(Snapshot { mmap, expiry_count, date_days, symbol, minute_count })
    }

    fn expiry_base(&self, e: usize) -> usize {
        HEADER_SIZE + SPOT_SIZE + e * EXPIRY_SIZE
    }

    pub fn spot_open_x100(&self, m: usize) -> i32 {
        debug_assert!(m < MINUTES);
        let off = HEADER_SIZE + m * SPOT_ENTRY;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_high_x100(&self, m: usize) -> i32 {
        debug_assert!(m < MINUTES);
        let off = HEADER_SIZE + m * SPOT_ENTRY + 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_low_x100(&self, m: usize) -> i32 {
        debug_assert!(m < MINUTES);
        let off = HEADER_SIZE + m * SPOT_ENTRY + 8;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }
    pub fn spot_close_x100(&self, m: usize) -> i32 {
        debug_assert!(m < MINUTES);
        let off = HEADER_SIZE + m * SPOT_ENTRY + 12;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    pub fn expiry_idx(&self, e: usize) -> i16 {
        debug_assert!(e < self.expiry_count);
        let off = self.expiry_base(e);
        i16::from_le_bytes(self.mmap[off..off+2].try_into().unwrap())
    }

    pub fn atm_x100(&self, e: usize, m: usize) -> i32 {
        debug_assert!(e < self.expiry_count);
        let off = self.expiry_base(e) + 2 + m * 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    /// field: 0=close 1=high 2=low 3=volume
    pub fn chain_val(&self, e: usize, s: usize, t: usize, field: usize, m: usize) -> i32 {
        debug_assert!(e < self.expiry_count);
        debug_assert!(s < CHAIN_STRIKES);
        debug_assert!(t < CHAIN_TYPES);
        debug_assert!(field < CHAIN_FIELDS);
        debug_assert!(m < MINUTES);
        let chain_off = self.expiry_base(e) + 2 + MINUTES * 4;
        let idx = s * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + t * CHAIN_FIELDS * MINUTES
            + field * MINUTES
            + m;
        let off = chain_off + idx * 4;
        i32::from_le_bytes(self.mmap[off..off+4].try_into().unwrap())
    }

    /// Find the e index (0..expiry_count) matching a given i16 expiry_idx.
    pub fn find_expiry_e(&self, target_idx: i16) -> Option<usize> {
        (0..self.expiry_count).find(|&e| self.expiry_idx(e) == target_idx)
    }
}

#[cfg(test)]
pub mod test_helpers {
    use super::*;

    /// Build a minimal valid DaySnapshot in memory for testing.
    pub fn synthetic_snapshot(
        date_str: &str,
        atm_x100: i32,
        entry_close_x100: i32,
        later_close_x100: i32,
        entry_minute: usize,
    ) -> Vec<u8> {
        use chrono::NaiveDate;
        let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
        let d = NaiveDate::parse_from_str(date_str, "%Y-%m-%d").unwrap();
        let date_days = (d - epoch).num_days() as i32;

        let mut buf = Vec::new();
        // Header 32 bytes
        buf.extend_from_slice(b"ITDS");
        buf.push(1); // version
        let sym = b"NIFTY\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00";
        buf.extend_from_slice(sym); // 16 bytes
        buf.extend_from_slice(&date_days.to_le_bytes());
        buf.push(1); // expiry_count
        buf.extend_from_slice(&(MINUTES as u16).to_le_bytes());
        buf.extend_from_slice(&[0u8; 4]); // padding
        assert_eq!(buf.len(), HEADER_SIZE);

        // SPOT: all bars = atm_x100
        for _ in 0..MINUTES {
            for _ in 0..4 {
                buf.extend_from_slice(&atm_x100.to_le_bytes());
            }
        }
        assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE);

        // Expiry section
        buf.extend_from_slice(&0i16.to_le_bytes()); // expiry_idx = 0
        // ATM array
        for _ in 0..MINUTES { buf.extend_from_slice(&atm_x100.to_le_bytes()); }
        // Chain[11][2][4][375]: default = 100 (1.00 INR)
        let chain_size = CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES;
        let mut chain = vec![100i32; chain_size];
        // Set s=5 (ATM), t=0 (CE), field=0..2 (close/high/low)
        for m in 0..MINUTES {
            let px = if m <= entry_minute { entry_close_x100 } else { later_close_x100 };
            for field in 0..3 {
                let idx = 5 * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
                    + 0 * CHAIN_FIELDS * MINUTES
                    + field * MINUTES
                    + m;
                chain[idx] = px;
            }
        }
        for v in &chain { buf.extend_from_slice(&v.to_le_bytes()); }
        assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE + EXPIRY_SIZE);
        buf
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_open_synthetic() {
        let bytes = test_helpers::synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let snap = Snapshot::open(f.path()).unwrap();
        assert_eq!(snap.symbol, "NIFTY");
        assert_eq!(snap.expiry_count, 1);
        assert_eq!(snap.minute_count, MINUTES);
        assert_eq!(snap.atm_x100(0, 0), 2400000);
        assert_eq!(snap.spot_close_x100(0), 2400000);
        // CE chain at s=5, entry minute = 5
        assert_eq!(snap.chain_val(0, 5, 0, 0, 5), 20000);
        assert_eq!(snap.chain_val(0, 5, 0, 0, 10), 10000);
    }

    #[test]
    fn test_bad_magic() {
        let bytes = vec![0u8; 100];
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let result = Snapshot::open(f.path());
        assert!(result.is_err());
    }
}
