use memmap2::Mmap;
use std::fs::File;
use std::path::Path;

pub const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16; // 4 × i32
const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY; // 6000
const CHAIN_STRIKES: usize = 11; // ATM-5 .. ATM+5
const CHAIN_TYPES: usize = 2;  // 0=CE 1=PE
const CHAIN_FIELDS: usize = 4; // 0=close 1=high 2=low 3=volume
pub const EXPIRY_SIZE: usize = 2
    + MINUTES * 4
    + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4; // 133502

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
        let mmap = unsafe { Mmap::map(&file)? };
        if &mmap[0..4] != b"ITDS" {
            return Err(std::io::Error::new(std::io::ErrorKind::InvalidData, "bad ITDS magic"));
        }
        let symbol_bytes = &mmap[5..21];
        let symbol = std::str::from_utf8(symbol_bytes)
            .unwrap_or("")
            .trim_end_matches('\0')
            .to_string();
        let date_days = i32::from_le_bytes(mmap[21..25].try_into().unwrap());
        let expiry_count = mmap[25] as usize;
        let minute_count = u16::from_le_bytes(mmap[26..28].try_into().unwrap()) as usize;
        Ok(Snapshot { mmap, expiry_count, date_days, symbol, minute_count })
    }

    fn expiry_base(&self, e: usize) -> usize {
        HEADER_SIZE + SPOT_SIZE + e * EXPIRY_SIZE
    }

    pub fn spot_close_x100(&self, m: usize) -> i32 {
        let off = HEADER_SIZE + m * SPOT_ENTRY + 12; // close is 4th i32
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }

    pub fn expiry_idx(&self, e: usize) -> i16 {
        let off = self.expiry_base(e);
        i16::from_le_bytes(self.mmap[off..off + 2].try_into().unwrap())
    }

    pub fn atm_x100(&self, e: usize, m: usize) -> i32 {
        let off = self.expiry_base(e) + 2 + m * 4;
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }

    /// field: 0=close 1=high 2=low 3=volume
    pub fn chain_val(&self, e: usize, s: usize, t: usize, field: usize, m: usize) -> i32 {
        let chain_off = self.expiry_base(e) + 2 + MINUTES * 4;
        let idx = s * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + t * CHAIN_FIELDS * MINUTES
            + field * MINUTES
            + m;
        let off = chain_off + idx * 4;
        i32::from_le_bytes(self.mmap[off..off + 4].try_into().unwrap())
    }
}
