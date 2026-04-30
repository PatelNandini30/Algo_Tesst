use crate::engine::snapshot::{Snapshot, MINUTES};
use crate::engine::types::{ChainRow, ExpiryMode, OhlcvBar, OptType, Resolution, SeriesBar};
use crate::error::AppError;
use chrono::{Datelike, NaiveDate};
use std::collections::HashMap;
use std::path::Path;

const SESSION_START: u32 = 9 * 60 + 15;

fn idx_to_time(idx: usize) -> String {
    let abs = SESSION_START + idx as u32;
    format!("{:02}:{:02}", abs / 60, abs % 60)
}

pub fn time_to_idx(hhmm: &str) -> Result<usize, AppError> {
    let (h_str, m_str) = hhmm.split_once(':')
        .ok_or_else(|| AppError::BadRequest(format!("invalid time format: {hhmm}")))?;
    let h: u32 = h_str.parse()
        .map_err(|_| AppError::BadRequest(format!("invalid time format: {hhmm}")))?;
    let m: u32 = m_str.parse()
        .map_err(|_| AppError::BadRequest(format!("invalid time format: {hhmm}")))?;
    let abs_min = h * 60 + m;
    if abs_min < SESSION_START || abs_min >= SESSION_START + MINUTES as u32 {
        return Err(AppError::BadRequest(format!(
            "time {hhmm} is outside trading session 09:15–15:29"
        )));
    }
    Ok((abs_min - SESSION_START) as usize)
}

pub fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY" => 10000,
        "MIDCPNIFTY" => 2500,
        _ => 5000,
    }
}

/// Extract full spot OHLCV for all minutes of the day.
/// `volume` is always 0: the DaySnapshot format stores no spot volume.
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
    // Anchor is based on ATM at minute 0: chain layout is fixed at session open
    let anchor = snap.atm_x100(e, 0) - 5 * step;
    let diff = strike_x100 - anchor;
    if diff % step != 0 {
        return Err(AppError::BadRequest(format!(
            "strike {} is not on the {} step grid",
            strike_x100 as f64 / 100.0,
            step as f64 / 100.0,
        )));
    }
    let s_raw = diff / step;
    if s_raw < 0 || s_raw >= 11 {
        return Err(AppError::BadRequest(format!(
            "strike {} is outside ATM±5 chain range for this day",
            strike_x100 as f64 / 100.0
        )));
    }
    let s = s_raw as usize;
    let t = opt_type.chain_idx();

    // DaySnapshot stores close/high/low/volume only for options — no per-minute open.
    // OhlcvBar.open is aliased to close (field 0 = last price of that minute).
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

/// Load expiries.json → HashMap<i16 expiry_idx, NaiveDate>
pub fn load_expiry_map(symbol_dir: &Path) -> std::io::Result<HashMap<i16, NaiveDate>> {
    let text = std::fs::read_to_string(symbol_dir.join("expiries.json"))?;
    let raw: HashMap<String, String> = serde_json::from_str(&text)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string()))?;
    let map = raw.into_iter().filter_map(|(k, v)| {
        let idx = k.parse::<i16>().ok()?;
        let date = NaiveDate::parse_from_str(&v, "%Y-%m-%d").ok()?;
        Some((idx, date))
    }).collect();
    Ok(map)
}

/// Pick best expiry_idx for a given trade_date and expiry mode.
/// WEEKLY → nearest expiry_date >= trade_date.
/// MONTHLY → last expiry of current month if available, else first available.
pub fn pick_expiry_idx(
    trade_date: NaiveDate,
    expiry_mode: ExpiryMode,
    expiry_map: &HashMap<i16, NaiveDate>,
) -> Option<i16> {
    let mut candidates: Vec<(i16, NaiveDate)> = expiry_map
        .iter()
        .filter(|(_, &d)| d >= trade_date)
        .map(|(&idx, &d)| (idx, d))
        .collect();
    candidates.sort_by_key(|&(_, d)| d);

    match expiry_mode {
        ExpiryMode::Weekly => candidates.first().map(|&(idx, _)| idx),
        ExpiryMode::Monthly => {
            let month_end = candidates.iter()
                .filter(|(_, d)| d.month() == trade_date.month() && d.year() == trade_date.year())
                .last();
            month_end.or(candidates.first()).map(|&(idx, _)| idx)
        }
    }
}

/// Downsample bars at the given resolution (bucket by resolution windows).
fn downsample(bars: Vec<OhlcvBar>, resolution: Resolution) -> Vec<OhlcvBar> {
    let bucket = resolution.minutes();
    if bucket <= 1 { return bars; }
    bars.chunks(bucket).map(|chunk| {
        let open  = chunk[0].open;
        let high  = chunk.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
        let low   = chunk.iter().map(|b| b.low).fold(f64::INFINITY, f64::min);
        let close = chunk.last().unwrap().close;
        OhlcvBar { minute: chunk[0].minute.clone(), open, high, low, close, volume: 0 }
    }).collect()
}

/// Multi-day OHLCV series for one option across a date range.
pub fn multi_day_series(
    symbol_dir: &Path,
    date_from: NaiveDate,
    date_to: NaiveDate,
    strike_x100: i32,
    opt_type: OptType,
    expiry_mode: ExpiryMode,
    resolution: Resolution,
) -> Result<Vec<SeriesBar>, AppError> {
    let expiry_map = load_expiry_map(symbol_dir)
        .map_err(AppError::Io)?;
    let snaps_dir = symbol_dir.join("snapshots");
    let mut result = Vec::new();
    let mut current = date_from;

    while current <= date_to {
        let date_str = current.format("%Y-%m-%d").to_string();
        let snap_path = snaps_dir.join(format!("{date_str}.arrow"));

        if snap_path.exists() {
            if let Some(expiry_idx) = pick_expiry_idx(current, expiry_mode, &expiry_map) {
                match Snapshot::open(&snap_path) {
                    Ok(snap) => {
                        match ohlcv_series(&snap, expiry_idx, strike_x100, opt_type) {
                            Ok(bars) => {
                                for bar in downsample(bars, resolution) {
                                    result.push(SeriesBar {
                                        date: date_str.clone(),
                                        minute: bar.minute,
                                        open: bar.open,
                                        high: bar.high,
                                        low: bar.low,
                                        close: bar.close,
                                    });
                                }
                            }
                            Err(_) => {} // skip days where strike is outside chain
                        }
                    }
                    Err(e) => tracing::warn!("skip {date_str}: {e}"),
                }
            }
        }

        current = current.succ_opt().unwrap_or(current);
    }

    Ok(result)
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
        assert_eq!(time_to_idx("09:15").unwrap(), 0);
        assert_eq!(time_to_idx("09:20").unwrap(), 5);
        assert_eq!(time_to_idx("15:29").unwrap(), 374);
    }
}

#[cfg(test)]
mod multi_day_tests {
    use super::*;
    use crate::engine::snapshot::test_helpers::synthetic_snapshot;
    use tempfile::TempDir;

    fn setup_two_day_dir() -> TempDir {
        let dir = TempDir::new().unwrap();
        let sym_dir = dir.path().join("NIFTY");
        std::fs::create_dir_all(sym_dir.join("snapshots")).unwrap();
        // expiries.json: idx 0 → "2024-01-04" (Thursday)
        std::fs::write(
            sym_dir.join("expiries.json"),
            r#"{"0": "2024-01-04"}"#,
        ).unwrap();
        // Write two snapshot files
        for (date, entry_px, later_px) in [
            ("2024-01-02", 20000i32, 10000i32),
            ("2024-01-03", 15000i32, 8000i32),
        ] {
            let bytes = synthetic_snapshot(date, 2400000, entry_px, later_px, 5);
            let path = sym_dir.join("snapshots").join(format!("{date}.arrow"));
            std::fs::write(path, bytes).unwrap();
        }
        dir
    }

    #[test]
    fn test_multi_day_series_two_days_1m() {
        let dir = setup_two_day_dir();
        let sym_dir = dir.path().join("NIFTY");
        let from = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let to   = NaiveDate::from_ymd_opt(2024, 1, 3).unwrap();
        let bars = multi_day_series(
            &sym_dir, from, to, 2400000, OptType::Ce,
            ExpiryMode::Weekly, Resolution::M1,
        ).unwrap();
        assert_eq!(bars.len(), 375 * 2);
        assert_eq!(bars[0].date, "2024-01-02");
        assert_eq!(bars[375].date, "2024-01-03");
    }

    #[test]
    fn test_multi_day_series_5m_downsamples() {
        let dir = setup_two_day_dir();
        let sym_dir = dir.path().join("NIFTY");
        let from = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let to   = NaiveDate::from_ymd_opt(2024, 1, 2).unwrap();
        let bars = multi_day_series(
            &sym_dir, from, to, 2400000, OptType::Ce,
            ExpiryMode::Weekly, Resolution::M5,
        ).unwrap();
        assert_eq!(bars.len(), 375 / 5);
    }
}
