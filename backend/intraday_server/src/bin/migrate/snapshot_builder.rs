use crate::parquet_writer::SpotBar;
use crate::csv_reader::BarRow;
use chrono::NaiveDate;
use std::collections::HashMap;

// Constants mirror engine/snapshot.rs — must stay in sync
const MINUTES: usize = 375;
const HEADER_SIZE: usize = 32;
const SPOT_ENTRY: usize = 16;
const SPOT_SIZE: usize = MINUTES * SPOT_ENTRY;          // 6000
const CHAIN_STRIKES: usize = 11;
const CHAIN_TYPES: usize = 2;
const CHAIN_FIELDS: usize = 4;
const EXPIRY_SIZE: usize =
    2 + MINUTES * 4 + CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES * 4;  // 133502

const SESSION_START_MIN: i16 = 555;  // 09:15

fn push_i16(buf: &mut Vec<u8>, v: i16) { buf.extend_from_slice(&v.to_le_bytes()); }
fn push_i32(buf: &mut Vec<u8>, v: i32) { buf.extend_from_slice(&v.to_le_bytes()); }
fn push_u16(buf: &mut Vec<u8>, v: u16) { buf.extend_from_slice(&v.to_le_bytes()); }

/// Pick up to 4 active expiries: nearest post-date expiry dates from the unique set.
pub fn pick_active_expiries(
    all_expiries: &[NaiveDate],
    trade_date: NaiveDate,
) -> Vec<NaiveDate> {
    let mut after: Vec<NaiveDate> = all_expiries
        .iter()
        .filter(|&&e| e > trade_date)
        .copied()
        .collect();
    after.sort();
    after.dedup();   // guard against duplicate expiries from caller
    after.truncate(4);
    after
}

/// Compute the ATM strike × 100 per minute from spot close prices.
/// anchor = ATM at the first non-zero spot close (forward-fill from there).
/// Returns (anchor_x100, atm_per_minute[375]).
pub fn compute_atm(
    spot_by_min: &[i32; MINUTES],
    strike_step: i32,
) -> (i32, [i32; MINUTES]) {
    let step_x100 = strike_step * 100;
    let round_to_step = |px: i32| -> i32 {
        if px <= 0 { return 0; }
        ((px as f64 / step_x100 as f64).round() as i32) * step_x100
    };

    let anchor_raw = (0..MINUTES)
        .map(|m| spot_by_min[m])
        .find(|&v| v > 0)
        .unwrap_or(0);
    let anchor_x100 = round_to_step(anchor_raw);

    let mut atm = [0i32; MINUTES];
    let mut last = anchor_raw;
    for m in 0..MINUTES {
        if spot_by_min[m] > 0 { last = spot_by_min[m]; }
        atm[m] = round_to_step(last);
    }
    (anchor_x100, atm)
}

/// Build the full DaySnapshot binary for one trading date.
pub fn build(
    symbol: &str,
    trade_date: NaiveDate,
    spot_bars: &[SpotBar],
    option_rows: &[BarRow],
    active_expiries: &[(NaiveDate, i16)],
    strike_step: i32,
) -> Vec<u8> {
    let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
    let date_days = (trade_date - epoch).num_days() as i32;
    let expiry_count = active_expiries.len().min(4) as u8;

    // Build spot_by_min lookup
    let mut spot_close = [0i32; MINUTES];
    let mut spot_open  = [0i32; MINUTES];
    let mut spot_high  = [0i32; MINUTES];
    let mut spot_low   = [0i32; MINUTES];
    for b in spot_bars {
        let m = (b.ts_min - SESSION_START_MIN) as usize;
        if m < MINUTES {
            spot_close[m] = b.close_x100;
            spot_open[m]  = b.open_x100;
            spot_high[m]  = b.high_x100;
            spot_low[m]   = b.low_x100;
        }
    }
    // Forward-fill
    let mut last_close = spot_close.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_open  = spot_open.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_high  = spot_high.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut last_low   = spot_low.iter().copied().find(|&v| v > 0).unwrap_or(0);
    let mut ff_close = [0i32; MINUTES];
    let mut ff_open  = [0i32; MINUTES];
    let mut ff_high  = [0i32; MINUTES];
    let mut ff_low   = [0i32; MINUTES];
    for m in 0..MINUTES {
        if spot_close[m] > 0 {
            last_close = spot_close[m];
            last_open  = spot_open[m];
            last_high  = spot_high[m];
            last_low   = spot_low[m];
        }
        ff_close[m] = last_close;
        ff_open[m]  = last_open;
        ff_high[m]  = last_high;
        ff_low[m]   = last_low;
    }

    // Header
    let mut buf: Vec<u8> = Vec::with_capacity(
        HEADER_SIZE + SPOT_SIZE + expiry_count as usize * EXPIRY_SIZE
    );
    buf.extend_from_slice(b"ITDS");
    buf.push(1u8);
    let mut sym_bytes = [0u8; 16];
    let s = symbol.as_bytes();
    sym_bytes[..s.len().min(16)].copy_from_slice(&s[..s.len().min(16)]);
    buf.extend_from_slice(&sym_bytes);
    push_i32(&mut buf, date_days);
    buf.push(expiry_count);
    push_u16(&mut buf, MINUTES as u16);
    buf.extend_from_slice(&[0u8; 4]);
    assert_eq!(buf.len(), HEADER_SIZE);

    // Spot section
    for m in 0..MINUTES {
        push_i32(&mut buf, ff_open[m]);
        push_i32(&mut buf, ff_high[m]);
        push_i32(&mut buf, ff_low[m]);
        push_i32(&mut buf, ff_close[m]);
    }
    assert_eq!(buf.len(), HEADER_SIZE + SPOT_SIZE);

    // Per-expiry sections
    let (anchor_x100, atm_per_min) = compute_atm(&ff_close, strike_step);
    let step_x100 = strike_step * 100;

    type Key = (NaiveDate, i32, u8, usize);
    let mut lookup: HashMap<Key, (i32, i32, i32, i32)> = HashMap::new();
    for r in option_rows {
        let m = (r.ts_min - SESSION_START_MIN) as usize;
        if m >= MINUTES { continue; }
        let key: Key = (r.expiry_date, r.strike_x100, r.opt_type as u8, m);
        lookup.insert(key, (r.close_x100, r.high_x100, r.low_x100, r.volume));
    }

    for &(expiry_date, expiry_idx) in &active_expiries[..expiry_count as usize] {
        let section_start = buf.len();
        push_i16(&mut buf, expiry_idx);

        // ATM array
        for m in 0..MINUTES {
            push_i32(&mut buf, atm_per_min[m]);
        }

        // Chain: [s=0..11][t=0..2][field=0..4][m=0..375]
        let chain_size = CHAIN_STRIKES * CHAIN_TYPES * CHAIN_FIELDS * MINUTES;
        let mut chain = vec![0i32; chain_size];

        for s_offset in 0..CHAIN_STRIKES as i32 {
            let rel = s_offset - 5;  // -5..+5
            let strike_x100 = anchor_x100 + rel * step_x100;
            if strike_x100 <= 0 { continue; }

            for t in 0..CHAIN_TYPES {
                let opt_type = t as u8;
                let mut last_close = 0i32;
                for m in 0..MINUTES {
                    let key: Key = (expiry_date, strike_x100, opt_type, m);
                    let idx_base = (s_offset as usize) * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
                        + t * CHAIN_FIELDS * MINUTES;
                    if let Some(&(close, high, low, vol)) = lookup.get(&key) {
                        last_close = close;
                        chain[idx_base + 0 * MINUTES + m] = close;
                        chain[idx_base + 1 * MINUTES + m] = high;
                        chain[idx_base + 2 * MINUTES + m] = low;
                        chain[idx_base + 3 * MINUTES + m] = vol;
                    } else {
                        chain[idx_base + 0 * MINUTES + m] = last_close;
                        // high/low/volume stay 0 for missing bars
                    }
                }
            }
        }
        for v in &chain { push_i32(&mut buf, *v); }
        assert_eq!(buf.len() - section_start, EXPIRY_SIZE,
            "expiry section size mismatch: got {}", buf.len() - section_start);
    }

    buf
}

/// Write snapshot bytes atomically (.arrow.tmp → rename).
pub fn write(path: &std::path::Path, bytes: &[u8]) -> anyhow::Result<()> {
    if let Some(p) = path.parent() { std::fs::create_dir_all(p)?; }
    let tmp = path.with_extension("arrow.tmp");
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parquet_writer::SpotBar;
    use crate::csv_reader::BarRow;

    fn make_spot(date: NaiveDate, close_x100: i32) -> Vec<SpotBar> {
        (0..MINUTES as i16).map(|m| SpotBar {
            trade_date: date, ts_min: SESSION_START_MIN + m,
            open_x100: close_x100, high_x100: close_x100 + 100,
            low_x100: close_x100 - 100, close_x100,
        }).collect()
    }

    fn make_ce_row(date: NaiveDate, expiry: NaiveDate, strike_x100: i32, m: i16, close_x100: i32) -> BarRow {
        BarRow {
            trade_date: date, ts_min: SESSION_START_MIN + m,
            expiry_date: expiry, strike_x100,
            opt_type: false,
            open_x100: close_x100, high_x100: close_x100 + 50,
            low_x100: close_x100 - 50, close_x100,
            volume: 100, oi: 1000,
        }
    }

    #[test]
    fn test_build_size() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let spot = make_spot(date, 2_400_000);
        let rows = vec![make_ce_row(date, expiry, 2_400_000, 0, 15000)];
        let active = vec![(expiry, 0i16)];
        let bytes = build("NIFTY", date, &spot, &rows, &active, 50);
        assert_eq!(bytes.len(), HEADER_SIZE + SPOT_SIZE + EXPIRY_SIZE);
    }

    #[test]
    fn test_build_header_fields() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let bytes = build("NIFTY", date, &make_spot(date, 2_400_000), &[], &[(expiry, 0i16)], 50);

        assert_eq!(&bytes[0..4], b"ITDS");
        assert_eq!(bytes[4], 1u8);
        assert_eq!(&bytes[5..10], b"NIFTY");
        assert_eq!(bytes[10], 0u8);  // null pad

        let epoch = NaiveDate::from_ymd_opt(1970, 1, 1).unwrap();
        let expected_days = (date - epoch).num_days() as i32;
        let actual_days = i32::from_le_bytes(bytes[21..25].try_into().unwrap());
        assert_eq!(actual_days, expected_days);

        assert_eq!(bytes[25], 1u8);  // expiry_count
        let minute_count = u16::from_le_bytes(bytes[26..28].try_into().unwrap());
        assert_eq!(minute_count as usize, MINUTES);
    }

    #[test]
    fn test_chain_value_roundtrip() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let expiry = NaiveDate::from_ymd_opt(2025, 1, 9).unwrap();
        let atm_x100 = 2_400_000i32;
        let spot = make_spot(date, atm_x100);
        let rows = vec![make_ce_row(date, expiry, atm_x100, 0, 25000)];
        let active = vec![(expiry, 0i16)];
        let bytes = build("NIFTY", date, &spot, &rows, &active, 50);

        // Manually verify chain value at s=5 (ATM), t=0 (CE), field=0 (close), m=0
        let chain_base = HEADER_SIZE + SPOT_SIZE + 2 + MINUTES * 4;
        let idx = 5 * CHAIN_TYPES * CHAIN_FIELDS * MINUTES
            + 0 * CHAIN_FIELDS * MINUTES
            + 0 * MINUTES
            + 0;
        let off = chain_base + idx * 4;
        let val = i32::from_le_bytes(bytes[off..off+4].try_into().unwrap());
        assert_eq!(val, 25000);
    }

    #[test]
    fn test_pick_active_expiries() {
        let date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let exp: Vec<NaiveDate> = [
            "2024-12-26", "2025-01-02", "2025-01-09", "2025-01-16",
            "2025-01-23", "2025-01-30", "2025-02-27",
        ].iter().map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").unwrap()).collect();

        let active = pick_active_expiries(&exp, date);
        assert_eq!(active.len(), 4);
        assert!(active[0] > date);
        for w in active.windows(2) { assert!(w[0] < w[1]); }
    }
}
