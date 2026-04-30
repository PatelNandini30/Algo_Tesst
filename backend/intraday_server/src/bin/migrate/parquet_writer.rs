use crate::csv_reader::BarRow;
use arrow_array::{
    BooleanArray, Date32Array, Int16Array, Int32Array, RecordBatch, StringArray,
};
use arrow_schema::{DataType, Field, Schema};
use chrono::{Datelike, NaiveDate};
use parquet::arrow::ArrowWriter;
use parquet::basic::{Compression, ZstdLevel};
use parquet::file::properties::{EnabledStatistics, WriterProperties};
use std::path::Path;
use std::sync::Arc;

fn epoch() -> NaiveDate {
    NaiveDate::from_ymd_opt(1970, 1, 1).unwrap()
}

fn to_days(d: NaiveDate) -> i32 {
    let days = (d - epoch()).num_days();
    debug_assert!(
        days >= 0 && days <= i64::from(i32::MAX),
        "date {d} is out of Date32 range (days={days})"
    );
    days as i32
}

fn writer_props() -> anyhow::Result<WriterProperties> {
    Ok(WriterProperties::builder()
        .set_compression(Compression::ZSTD(ZstdLevel::try_new(3)?))
        .set_dictionary_enabled(true)
        .set_statistics_enabled(EnabledStatistics::Chunk)
        .build())
}

/// Write one trading day's real (non-padded) option bars to a Parquet file.
/// `rows` must already be sorted by (expiry_date, strike_x100, opt_type, ts_min).
/// Writes atomically: .tmp → rename (no fsync; idempotent via manifest on re-run).
pub fn write_options_parquet(
    path: &Path,
    symbol: &str,
    trade_date: NaiveDate,
    rows: &[BarRow],
) -> anyhow::Result<()> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("symbol",      DataType::Utf8,    false),
        Field::new("trade_date",  DataType::Date32,  false),
        Field::new("ts_min",      DataType::Int16,   false),
        Field::new("expiry_date", DataType::Date32,  false),
        Field::new("strike_x100", DataType::Int32,   false),
        Field::new("opt_type",    DataType::Boolean, false),
        Field::new("open_x100",   DataType::Int32,   false),
        Field::new("high_x100",   DataType::Int32,   false),
        Field::new("low_x100",    DataType::Int32,   false),
        Field::new("close_x100",  DataType::Int32,   false),
        Field::new("volume",      DataType::Int32,   false),
        Field::new("oi",          DataType::Int32,   false),
    ]));

    let n = rows.len();
    let trade_days = to_days(trade_date);
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(StringArray::from(vec![symbol; n])),
        Arc::new(Date32Array::from(vec![trade_days; n])),
        Arc::new(Int16Array::from(rows.iter().map(|r| r.ts_min).collect::<Vec<_>>())),
        Arc::new(Date32Array::from(rows.iter().map(|r| to_days(r.expiry_date)).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.strike_x100).collect::<Vec<_>>())),
        Arc::new(BooleanArray::from(rows.iter().map(|r| r.opt_type).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.open_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.high_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.low_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.close_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.volume).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(rows.iter().map(|r| r.oi).collect::<Vec<_>>())),
    ])?;

    atomic_write_parquet(path, schema, &batch)
}

/// Write one year's spot bars to a Parquet file.
pub fn write_spot_parquet(path: &Path, bars: &[SpotBar]) -> anyhow::Result<()> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("trade_date",  DataType::Date32, false),
        Field::new("ts_min",      DataType::Int16,  false),
        Field::new("open_x100",   DataType::Int32,  false),
        Field::new("high_x100",   DataType::Int32,  false),
        Field::new("low_x100",    DataType::Int32,  false),
        Field::new("close_x100",  DataType::Int32,  false),
    ]));
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(Date32Array::from(bars.iter().map(|b| to_days(b.trade_date)).collect::<Vec<_>>())),
        Arc::new(Int16Array::from(bars.iter().map(|b| b.ts_min).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.open_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.high_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.low_x100).collect::<Vec<_>>())),
        Arc::new(Int32Array::from(bars.iter().map(|b| b.close_x100).collect::<Vec<_>>())),
    ])?;
    atomic_write_parquet(path, schema, &batch)
}

fn atomic_write_parquet(path: &Path, schema: Arc<Schema>, batch: &RecordBatch) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("parquet.tmp");
    {
        let file = std::fs::File::create(&tmp)?;
        let mut writer = ArrowWriter::try_new(file, schema, Some(writer_props()?))?;
        writer.write(batch)?;
        writer.close()?;
    }
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Spot bar — used by snapshot_builder.
#[derive(Debug, Clone)]
pub struct SpotBar {
    pub trade_date:  NaiveDate,
    pub ts_min:      i16,
    pub open_x100:   i32,
    pub high_x100:   i32,
    pub low_x100:    i32,
    pub close_x100:  i32,
}

/// Parse the NIFTY 50.csv spot file, filtering to `target_year` (includes padded rows).
pub fn read_spot_csv(path: &Path, target_year: i32) -> anyhow::Result<Vec<SpotBar>> {
    let mut rdr = csv::Reader::from_path(path)?;
    let mut bars = Vec::new();
    for result in rdr.records() {
        let rec = result?;
        // Same column layout as option CSVs
        let date_str  = rec.get(1).unwrap_or("");
        let time_str  = rec.get(2).unwrap_or("");
        let open_str  = rec.get(4).unwrap_or("0");
        let high_str  = rec.get(5).unwrap_or("0");
        let low_str   = rec.get(6).unwrap_or("0");
        let close_str = rec.get(7).unwrap_or("0");

        let trade_date = match NaiveDate::parse_from_str(date_str.trim(), "%Y-%m-%d") {
            Ok(d) => d,
            Err(_) => continue,
        };
        if trade_date.year() != target_year { continue; }

        let mut parts = time_str.trim().splitn(3, ':');
        let hh: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);
        let mm: i16 = parts.next().unwrap_or("0").parse().unwrap_or(0);

        let px = |s: &str| -> i32 {
            (s.trim().parse::<f64>().unwrap_or(0.0) * 100.0).round() as i32
        };

        bars.push(SpotBar {
            trade_date,
            ts_min: hh * 60 + mm,
            open_x100:  px(open_str),
            high_x100:  px(high_str),
            low_x100:   px(low_str),
            close_x100: px(close_str),
        });
    }
    Ok(bars)
}

#[cfg(test)]
mod tests {
    use super::*;
    use parquet::file::reader::{FileReader, SerializedFileReader};

    fn sample_rows(trade_date: NaiveDate) -> Vec<BarRow> {
        let exp = NaiveDate::from_ymd_opt(2025, 1, 23).unwrap();
        vec![
            BarRow {
                trade_date, ts_min: 555, expiry_date: exp, strike_x100: 2_400_000,
                opt_type: false, open_x100: 10000, high_x100: 11000,
                low_x100: 9500, close_x100: 10500, volume: 100, oi: 5000,
            },
            BarRow {
                trade_date, ts_min: 556, expiry_date: exp, strike_x100: 2_400_000,
                opt_type: false, open_x100: 10500, high_x100: 11500,
                low_x100: 10000, close_x100: 11000, volume: 80, oi: 5000,
            },
        ]
    }

    #[test]
    fn test_write_options_parquet_readable() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("2025-01-02.parquet");
        let trade_date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        let rows = sample_rows(trade_date);

        write_options_parquet(&path, "NIFTY", trade_date, &rows).unwrap();
        assert!(path.exists());
        assert!(!dir.path().join("2025-01-02.parquet.tmp").exists()); // tmp cleaned up

        let file = std::fs::File::open(&path).unwrap();
        let reader = SerializedFileReader::new(file).unwrap();
        assert_eq!(reader.metadata().file_metadata().num_rows(), 2);
    }

    #[test]
    fn test_write_options_parquet_creates_dirs() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("year=2025/month=01/2025-01-02.parquet");
        let trade_date = NaiveDate::from_ymd_opt(2025, 1, 2).unwrap();
        write_options_parquet(&path, "NIFTY", trade_date, &sample_rows(trade_date)).unwrap();
        assert!(path.exists());
    }
}
