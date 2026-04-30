use crate::engine::snapshot::{Snapshot, MINUTES};
use crate::engine::types::{ChainRow, OhlcvBar, OptType};
use crate::error::AppError;

const SESSION_START: u32 = 9 * 60 + 15;

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

pub fn time_to_idx(hhmm: &str) -> usize {
    let parts: Vec<u32> = hhmm.splitn(2, ':')
        .map(|s| s.parse().unwrap_or(0))
        .collect();
    let abs_min = parts[0] * 60 + *parts.get(1).unwrap_or(&0);
    (abs_min.saturating_sub(SESSION_START)) as usize
}

pub fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY" => 10000,
        "MIDCPNIFTY" => 2500,
        _ => 5000,
    }
}

/// Extract full spot OHLCV for all minutes of the day.
pub fn spot_series(snap: &Snapshot) -> Vec<OhlcvBar> {
    (0..snap.minute_count)
        .map(|m| OhlcvBar {
            minute: idx_to_time(m),
            open: snap.spot_open_x100(m) as f64 / 100.0,
            high: snap.spot_high_x100(m) as f64 / 100.0,
            low: snap.spot_low_x100(m) as f64 / 100.0,
            close: snap.spot_close_x100(m) as f64 / 100.0,
            volume: 0,
        })
        .collect()
}

/// Extract OHLCV for one option contract (identified by expiry_idx + strike + opt_type).
pub fn ohlcv_series(
    snap: &Snapshot,
    expiry_idx: i16,
    strike_x100: i32,
    opt_type: OptType,
) -> Result<Vec<OhlcvBar>, AppError> {
    let e = snap
        .find_expiry_e(expiry_idx)
        .ok_or_else(|| AppError::NotFound(format!("expiry_idx {expiry_idx} not in snapshot")))?;

    let step = strike_step(&snap.symbol);
    let anchor = snap.atm_x100(e, 0) - 5 * step;
    let s_raw = (strike_x100 - anchor) / step;
    if s_raw < 0 || s_raw >= 11 {
        return Err(AppError::BadRequest(format!(
            "strike {} is outside ATM±5 chain range for this day",
            strike_x100 as f64 / 100.0
        )));
    }
    let s = s_raw as usize;
    let t = opt_type.chain_idx();

    let bars = (0..snap.minute_count)
        .map(|m| OhlcvBar {
            minute: idx_to_time(m),
            open: snap.chain_val(e, s, t, 0, m) as f64 / 100.0,
            high: snap.chain_val(e, s, t, 1, m) as f64 / 100.0,
            low: snap.chain_val(e, s, t, 2, m) as f64 / 100.0,
            close: snap.chain_val(e, s, t, 0, m) as f64 / 100.0,
            volume: snap.chain_val(e, s, t, 3, m) as i64,
        })
        .collect();
    Ok(bars)
}

/// Extract the full option chain at a single minute.
pub fn chain_snapshot(
    snap: &Snapshot,
    expiry_idx: i16,
    minute_idx: usize,
) -> Result<Vec<ChainRow>, AppError> {
    let e = snap
        .find_expiry_e(expiry_idx)
        .ok_or_else(|| AppError::NotFound(format!("expiry_idx {expiry_idx} not in snapshot")))?;

    let step = strike_step(&snap.symbol);
    let anchor = snap.atm_x100(e, 0) - 5 * step;

    let rows = (0..11usize)
        .map(|s| {
            let strike = (anchor + s as i32 * step) as f64 / 100.0;
            ChainRow {
                strike,
                ce_close: snap.chain_val(e, s, 0, 0, minute_idx) as f64 / 100.0,
                ce_high:  snap.chain_val(e, s, 0, 1, minute_idx) as f64 / 100.0,
                ce_low:   snap.chain_val(e, s, 0, 2, minute_idx) as f64 / 100.0,
                ce_volume: snap.chain_val(e, s, 0, 3, minute_idx) as i64,
                pe_close: snap.chain_val(e, s, 1, 0, minute_idx) as f64 / 100.0,
                pe_high:  snap.chain_val(e, s, 1, 1, minute_idx) as f64 / 100.0,
                pe_low:   snap.chain_val(e, s, 1, 2, minute_idx) as f64 / 100.0,
                pe_volume: snap.chain_val(e, s, 1, 3, minute_idx) as i64,
            }
        })
        .collect();
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use std::io::Write;
    use tempfile::NamedTempFile;

    fn make_snap() -> (Snapshot, NamedTempFile) {
        let bytes = synthetic_snapshot("2024-01-01", 2400000, 20000, 10000, 5);
        let mut f = NamedTempFile::new().unwrap();
        f.write_all(&bytes).unwrap();
        let snap = Snapshot::open(f.path()).unwrap();
        (snap, f)
    }

    #[test]
    fn test_spot_series_length() {
        let (snap, _f) = make_snap();
        let bars = spot_series(&snap);
        assert_eq!(bars.len(), MINUTES);
        assert_eq!(bars[0].minute, "09:15");
        assert_eq!(bars[374].minute, "15:29");
    }

    #[test]
    fn test_spot_series_values() {
        let (snap, _f) = make_snap();
        let bars = spot_series(&snap);
        assert!((bars[0].close - 24000.0).abs() < 0.01);
    }

    #[test]
    fn test_ohlcv_series_atm_ce() {
        let (snap, _f) = make_snap();
        // ATM = 24000, anchor = 24000 - 5*50 = 23750, s=5 → strike_x100 = 23750*100 + 5*5000 = 2400000
        let bars = ohlcv_series(&snap, 0, 2400000, OptType::Ce).unwrap();
        assert_eq!(bars.len(), MINUTES);
        // minute 5 (entry) should be entry_close = 200.00
        assert!((bars[5].close - 200.0).abs() < 0.01);
        // minute 10 should be later_close = 100.00
        assert!((bars[10].close - 100.0).abs() < 0.01);
    }

    #[test]
    fn test_ohlcv_series_bad_expiry() {
        let (snap, _f) = make_snap();
        let result = ohlcv_series(&snap, 99, 2400000, OptType::Ce);
        assert!(matches!(result, Err(crate::error::AppError::NotFound(_))));
    }

    #[test]
    fn test_chain_snapshot_11_rows() {
        let (snap, _f) = make_snap();
        let rows = chain_snapshot(&snap, 0, 5).unwrap();
        assert_eq!(rows.len(), 11);
        // middle row (s=5) should have ce_close = entry_close = 200.0
        assert!((rows[5].ce_close - 200.0).abs() < 0.01);
    }

    #[test]
    fn test_time_to_idx() {
        assert_eq!(time_to_idx("09:15"), 0);
        assert_eq!(time_to_idx("09:20"), 5);
        assert_eq!(time_to_idx("15:29"), 374);
    }
}
