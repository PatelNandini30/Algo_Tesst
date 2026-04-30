use chrono::{Datelike, NaiveDate};
use std::path::Path;

#[derive(Debug, Clone)]
pub struct BarRow {
    pub trade_date:  NaiveDate,
    pub ts_min:      i16,        // minutes since midnight: 09:15 = 555
    pub expiry_date: NaiveDate,
    pub strike_x100: i32,        // strike × 100
    pub opt_type:    bool,       // false = CE, true = PE
    pub open_x100:   i32,
    pub high_x100:   i32,
    pub low_x100:    i32,
    pub close_x100:  i32,
    pub volume:      i32,
    pub oi:          i32,
}

/// Extract (strike_x100, opt_type) from a filename like "NIFTY31DEC2616000CE.csv".
/// Returns None if filename does not match the expected pattern.
pub fn parse_filename(name: &str) -> Option<(i32, bool)> {
    let stem = name.strip_suffix(".csv")?;
    let opt_type = if stem.ends_with("CE") {
        false
    } else if stem.ends_with("PE") {
        true
    } else {
        return None;
    };
    let stem = &stem[..stem.len() - 2];
    // Filename format: SYMBOL + DD + MMM + YY + STRIKE
    // rfind last non-digit gives end of "MMM", then next 2 chars are the year (YY),
    // then the remainder is the strike.
    let after_month = stem.rfind(|c: char| !c.is_ascii_digit())? + 1;
    // skip the 2-digit year
    let strike_start = after_month + 2;
    if strike_start > stem.len() { return None; }
    let strike_str = &stem[strike_start..];
    let strike: i32 = strike_str.parse().ok()?;
    Some((strike * 100, opt_type))
}

/// Extract the expiry 2-digit year from filename (e.g. "NIFTY31DEC2616000CE.csv" → 26).
pub fn filename_expiry_year_2digit(name: &str) -> Option<u32> {
    let stem = name.strip_suffix(".csv")?;
    let digit_start = stem.find(|c: char| c.is_ascii_digit())?;
    let rest = &stem[digit_start..];
    if rest.len() < 7 { return None; }
    let yy: u32 = rest[5..7].parse().ok()?;
    Some(yy)
}

/// Read one CSV file, filter to rows where Date is in `target_year` and Padding Flag = 0.
pub fn read_file(path: &Path, target_year: i32) -> anyhow::Result<Vec<BarRow>> {
    let name = path.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("");
    let (strike_x100, opt_type) = parse_filename(name)
        .ok_or_else(|| anyhow::anyhow!("cannot parse filename: {name}"))?;

    let mut rdr = csv::Reader::from_path(path)?;
    let mut rows = Vec::new();

    for result in rdr.records() {
        let rec = result?;
        let date_str    = rec.get(1).unwrap_or("");
        let time_str    = rec.get(2).unwrap_or("");
        let expiry_str  = rec.get(3).unwrap_or("");
        let open_str    = rec.get(4).unwrap_or("0");
        let high_str    = rec.get(5).unwrap_or("0");
        let low_str     = rec.get(6).unwrap_or("0");
        let close_str   = rec.get(7).unwrap_or("0");
        let vol_str     = rec.get(8).unwrap_or("0");
        let oi_str      = rec.get(9).unwrap_or("0");
        let padded_str  = rec.get(10).unwrap_or("0");

        if padded_str.trim() == "1" { continue; }

        let trade_date = NaiveDate::parse_from_str(date_str.trim(), "%Y-%m-%d")
            .map_err(|_| anyhow::anyhow!("bad date: {date_str}"))?;
        if trade_date.year() != target_year { continue; }

        let expiry_date = NaiveDate::parse_from_str(expiry_str.trim(), "%Y-%m-%d")
            .map_err(|_| anyhow::anyhow!("bad expiry: {expiry_str}"))?;

        let mut parts = time_str.trim().splitn(3, ':');
        let hh: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let mm: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let ts_min = hh * 60 + mm;

        let px = |s: &str| -> i32 {
            (s.trim().parse::<f64>().unwrap_or(0.0) * 100.0).round() as i32
        };

        rows.push(BarRow {
            trade_date,
            ts_min,
            expiry_date,
            strike_x100,
            opt_type,
            open_x100:  px(open_str),
            high_x100:  px(high_str),
            low_x100:   px(low_str),
            close_x100: px(close_str),
            volume:     vol_str.trim().parse().unwrap_or(0),
            oi:         oi_str.trim().parse().unwrap_or(0),
        });
    }
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_filename_ce() {
        let (strike, opt) = parse_filename("NIFTY31DEC2616000CE.csv").unwrap();
        assert_eq!(strike, 1_600_000);
        assert!(!opt);
    }

    #[test]
    fn test_parse_filename_pe() {
        let (strike, opt) = parse_filename("BANKNIFTY24JAN2548000PE.csv").unwrap();
        assert_eq!(strike, 4_800_000);
        assert!(opt);
    }

    #[test]
    fn test_parse_filename_bad() {
        assert!(parse_filename("something.txt").is_none());
        assert!(parse_filename("NIFTY.csv").is_none());
    }

    #[test]
    fn test_expiry_year() {
        assert_eq!(filename_expiry_year_2digit("NIFTY31DEC2616000CE.csv"), Some(26));
        assert_eq!(filename_expiry_year_2digit("NIFTY24JAN2519000PE.csv"), Some(25));
        assert_eq!(filename_expiry_year_2digit("NIFTY30JAN2524500CE.csv"), Some(25));
    }

    #[test]
    fn test_read_file_filters_year_and_padding() {
        use std::io::Write;
        use tempfile::NamedTempFile;

        let csv = b"Ticker,Date,Time,Expiry Date,Open,High,Low,Close,Volume,Open Interest,Padding Flag\n\
NIFTY24JAN2519000CE.NFO,2024-12-31,09:15:00,2025-01-24,100.0,105.0,98.0,103.0,50,500,0\n\
NIFTY24JAN2519000CE.NFO,2025-01-02,09:15:00,2025-01-24,104.0,108.0,102.0,106.0,75,480,0\n\
NIFTY24JAN2519000CE.NFO,2025-01-02,09:16:00,2025-01-24,106.0,109.0,104.0,107.0,30,480,1\n\
NIFTY24JAN2519000CE.NFO,2025-01-03,09:15:00,2025-01-24,107.0,112.0,105.0,110.0,60,460,0\n";

        let mut f = NamedTempFile::with_suffix(".csv").unwrap();
        let dir = f.path().parent().unwrap().to_path_buf();
        let named = dir.join("NIFTY24JAN2519000CE.csv");
        f.write_all(csv).unwrap();
        std::fs::copy(f.path(), &named).unwrap();

        let rows = read_file(&named, 2025).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].trade_date, NaiveDate::from_ymd_opt(2025, 1, 2).unwrap());
        assert_eq!(rows[0].ts_min, 555);
        assert_eq!(rows[0].close_x100, 10600);
        assert_eq!(rows[0].strike_x100, 1_900_000);
        assert!(!rows[0].opt_type);
        assert_eq!(rows[1].trade_date, NaiveDate::from_ymd_opt(2025, 1, 3).unwrap());
        std::fs::remove_file(&named).ok();
    }
}
