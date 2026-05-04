use chrono::{Datelike, NaiveDate, Weekday};

/// Pick snapshot expiry_e index from the list of (snapshot_e, expiry_date) pairs.
/// `expiry_type`: "WEEKLY" | "NEXT_WEEKLY" | "MONTHLY" | "NEXT_MONTHLY"
/// Returns the position in snap.expiry_count (0-based) matching the desired expiry,
/// or None if not found in the snapshot.
pub fn pick_expiry_e(
    expiry_type: &str,
    trade_date: NaiveDate,
    expiries: &[(i16, NaiveDate)],
) -> Option<usize> {
    match expiry_type {
        "WEEKLY" => {
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| *d >= trade_date && d.weekday() == Weekday::Thu)
                .min_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        "NEXT_WEEKLY" => {
            let mut candidates: Vec<_> = expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| *d > trade_date && d.weekday() == Weekday::Thu)
                .collect();
            candidates.sort_by_key(|(_, (_, d))| *d);
            candidates.get(1).map(|(e, _)| *e)
        }
        "MONTHLY" => {
            let month = trade_date.month();
            let year = trade_date.year();
            let target_month = if trade_date.day() > 25 {
                if month == 12 { NaiveDate::from_ymd_opt(year + 1, 1, 1) }
                else { NaiveDate::from_ymd_opt(year, month + 1, 1) }
            } else {
                NaiveDate::from_ymd_opt(year, month, 1)
            };
            let target_month = target_month?;
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| {
                    d.year() == target_month.year()
                        && d.month() == target_month.month()
                        && d.weekday() == Weekday::Thu
                })
                .max_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        "NEXT_MONTHLY" => {
            let month = trade_date.month();
            let year = trade_date.year();
            let next_month = if month == 12 {
                NaiveDate::from_ymd_opt(year + 1, 1, 1)?
            } else {
                NaiveDate::from_ymd_opt(year, month + 1, 1)?
            };
            expiries
                .iter()
                .enumerate()
                .filter(|(_, (_, d))| {
                    d.year() == next_month.year()
                        && d.month() == next_month.month()
                        && d.weekday() == Weekday::Thu
                })
                .max_by_key(|(_, (_, d))| *d)
                .map(|(e, _)| e)
        }
        _ => expiries.first().map(|_| 0),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pick_weekly_returns_nearest_future_expiry() {
        let expiries = vec![
            (0i16, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),
        ];
        let e = pick_expiry_e("WEEKLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(0));
    }

    #[test]
    fn test_pick_monthly_returns_last_thursday_of_month() {
        let expiries = vec![
            (0i16, NaiveDate::from_ymd_opt(2024, 1, 4).unwrap()),
            (1, NaiveDate::from_ymd_opt(2024, 1, 11).unwrap()),
            (2, NaiveDate::from_ymd_opt(2024, 1, 25).unwrap()),
        ];
        let e = pick_expiry_e("MONTHLY", NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(), &expiries);
        assert_eq!(e, Some(2));
    }
}
