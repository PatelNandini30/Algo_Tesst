mod csv_reader;
mod expiry_index;
mod manifest;
mod parquet_writer;
mod snapshot_builder;

use chrono::NaiveDate;
use clap::Parser;
use csv_reader::BarRow;
use indicatif::{ProgressBar, ProgressStyle};
use parquet_writer::SpotBar;
use rayon::prelude::*;
use std::collections::HashMap;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "migrate", about = "Import intraday options CSVs into /data/intraday")]
struct Args {
    /// Directory containing per-contract CSV files
    #[arg(long)]
    options_dir: PathBuf,

    /// Spot CSV file path, e.g. "NIFTY 50.csv"
    #[arg(long)]
    spot_file: PathBuf,

    /// Output data root
    #[arg(long, default_value = "/data/intraday")]
    data_dir: PathBuf,

    /// Symbol name, e.g. NIFTY
    #[arg(long)]
    symbol: String,

    /// Calendar year to import
    #[arg(long)]
    year: i32,

    /// Number of rayon threads (default: all CPUs)
    #[arg(long)]
    workers: Option<usize>,

    /// Validate without writing any files
    #[arg(long)]
    dry_run: bool,

    /// Re-ingest even if sha256 matches
    #[arg(long)]
    force: bool,
}

fn strike_step(symbol: &str) -> i32 {
    match symbol {
        "BANKNIFTY"  => 100,
        "MIDCPNIFTY" => 25,
        _            => 50,
    }
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();

    if let Some(n) = args.workers {
        rayon::ThreadPoolBuilder::new().num_threads(n).build_global()?;
    }

    // Stage 0: validate inputs
    anyhow::ensure!(args.options_dir.is_dir(),
        "options_dir does not exist: {}", args.options_dir.display());
    anyhow::ensure!(args.spot_file.is_file(),
        "spot_file does not exist: {}", args.spot_file.display());

    // Stage 1: discover option files, pre-filter by expiry year
    println!("Discovering option files in {} ...", args.options_dir.display());
    let target_yy = (args.year % 100) as u32;
    let csv_files: Vec<PathBuf> = std::fs::read_dir(&args.options_dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map(|e| e == "csv").unwrap_or(false))
        .filter(|p| {
            let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
            // Keep yy >= target: a contract expiring next year can contain bars
            // from the current year (e.g. Dec expiry settles in Jan).
            match csv_reader::filename_expiry_year_2digit(name) {
                Some(yy) => yy >= target_yy,
                None     => false,
            }
        })
        .collect();
    println!("  Found {} candidate files (after pre-filter).", csv_files.len());

    // Stage 2: parallel CSV scan
    println!("Scanning CSVs for year={} (parallel) ...", args.year);
    let pb = ProgressBar::new(csv_files.len() as u64);
    pb.set_style(ProgressStyle::default_bar()
        .template("[{elapsed_precise}] {bar:40} {pos}/{len} files ({eta})")
        .unwrap());

    let results: Vec<Result<Vec<BarRow>, String>> = csv_files
        .par_iter()
        .map(|path| {
            let r = csv_reader::read_file(path, args.year);
            pb.inc(1);
            r.map_err(|e| format!("{}: {}", path.display(), e))
        })
        .collect();
    pb.finish_with_message("scan done");

    let mut scan_errors = Vec::new();
    let mut all_rows: Vec<BarRow> = Vec::new();
    for r in results {
        match r {
            Ok(rows) => all_rows.extend(rows),
            Err(e)   => scan_errors.push(e),
        }
    }
    if !scan_errors.is_empty() {
        eprintln!("WARN: {} files failed to parse:", scan_errors.len());
        for e in &scan_errors { eprintln!("  {e}"); }
    }
    println!("  Collected {} real bars.", all_rows.len());

    // Stage 3: load spot data
    println!("Loading spot data from {} ...", args.spot_file.display());
    let spot_bars = parquet_writer::read_spot_csv(&args.spot_file, args.year)?;
    let mut spot_by_date: HashMap<NaiveDate, Vec<SpotBar>> = HashMap::new();
    for b in &spot_bars {
        spot_by_date.entry(b.trade_date).or_default().push(b.clone());
    }
    println!("  {} spot bars across {} trading dates.", spot_bars.len(), spot_by_date.len());

    // Stage 4: sort all rows
    all_rows.sort_unstable_by(|a, b| {
        a.trade_date.cmp(&b.trade_date)
            .then(a.expiry_date.cmp(&b.expiry_date))
            .then(a.strike_x100.cmp(&b.strike_x100))
            .then(a.opt_type.cmp(&b.opt_type))
            .then(a.ts_min.cmp(&b.ts_min))
    });

    // Stage 5: collect unique expiries, build/update index
    let unique_expiries: Vec<NaiveDate> = {
        let mut v: Vec<NaiveDate> = all_rows.iter().map(|r| r.expiry_date).collect();
        v.sort_unstable();
        v.dedup();
        v
    };
    let expiry_json_path = args.data_dir.join(&args.symbol).join("expiries.json");
    let mut expiry_idx = expiry_index::ExpiryIndex::load_or_create(&expiry_json_path)?;
    for &e in &unique_expiries { expiry_idx.get_or_insert(e); }
    if !args.dry_run { expiry_idx.save()?; }

    // Stage 6: open manifest
    let manifest_path = args.data_dir.join("_manifest.db");
    let db = manifest::Manifest::open(&manifest_path)?;

    // Stage 7: serial write per trading date
    let unique_dates: Vec<NaiveDate> = {
        let mut v: Vec<NaiveDate> = all_rows.iter().map(|r| r.trade_date).collect();
        v.sort_unstable();
        v.dedup();
        v
    };
    println!("Processing {} trading dates ...", unique_dates.len());
    let pb2 = ProgressBar::new(unique_dates.len() as u64);
    pb2.set_style(ProgressStyle::default_bar()
        .template("[{elapsed_precise}] {bar:40} {pos}/{len} dates ({eta})")
        .unwrap());

    let mut stats_ok = 0usize;
    let mut stats_skipped = 0usize;
    let mut stats_failed: Vec<(NaiveDate, String)> = Vec::new();
    let mut total_rows_written = 0usize;
    let mut total_bytes = 0u64;

    // Slice all_rows by date (sorted by trade_date)
    let empty_spot: Vec<SpotBar> = vec![];
    let mut row_start = 0usize;
    for &trade_date in &unique_dates {
        pb2.inc(1);

        let row_end = all_rows[row_start..]
            .iter()
            .position(|r| r.trade_date != trade_date)
            .map(|p| row_start + p)
            .unwrap_or(all_rows.len());
        let date_rows = &all_rows[row_start..row_end];
        row_start = row_end;

        // OHLCV validation
        let mut valid = true;
        for r in date_rows {
            if r.high_x100 < r.open_x100 || r.high_x100 < r.close_x100
                || r.low_x100 > r.open_x100 || r.low_x100 > r.close_x100
                || r.high_x100 < r.low_x100
                || r.open_x100 < 0 || r.close_x100 < 0
                || r.low_x100 < 0 || r.high_x100 < 0
            {
                stats_failed.push((trade_date, format!(
                    "OHLCV invariant: O={} H={} L={} C={}",
                    r.open_x100, r.high_x100, r.low_x100, r.close_x100
                )));
                valid = false;
                break;
            }
        }
        if !valid { continue; }

        if args.dry_run {
            stats_ok += 1;
            total_rows_written += date_rows.len();
            continue;
        }

        // Build snapshot first (needed for consistent sha)
        let day_spot = spot_by_date.get(&trade_date).unwrap_or(&empty_spot);
        let all_day_expiries: Vec<NaiveDate> = {
            let mut v: Vec<NaiveDate> = date_rows.iter().map(|r| r.expiry_date).collect();
            v.sort_unstable(); v.dedup(); v
        };
        let active_exp_dates = snapshot_builder::pick_active_expiries(&all_day_expiries, trade_date);
        let active_exp: Vec<(NaiveDate, i16)> = active_exp_dates.iter()
            .filter_map(|&d| expiry_idx.get(d).map(|i| (d, i)))
            .collect();

        let snap_bytes = snapshot_builder::build(
            &args.symbol, trade_date, day_spot, date_rows, &active_exp,
            strike_step(&args.symbol),
        );
        let sha = manifest::sha256_hex(&snap_bytes);

        // Manifest check (skip if sha unchanged)
        if !args.force {
            if let Ok(Some(stored)) = db.check(&args.symbol, trade_date) {
                if stored == sha {
                    stats_skipped += 1;
                    continue;
                }
            }
        }

        // Write Parquet
        let year  = trade_date.format("%Y").to_string();
        let month = trade_date.format("%m").to_string();
        let pq_path = args.data_dir
            .join(&args.symbol).join("options")
            .join(format!("year={year}")).join(format!("month={month}"))
            .join(format!("{}.parquet", trade_date.format("%Y-%m-%d")));

        if let Err(e) = parquet_writer::write_options_parquet(
            &pq_path, &args.symbol, trade_date, date_rows,
        ) {
            stats_failed.push((trade_date, format!("parquet: {e}")));
            continue;
        }
        total_bytes += pq_path.metadata().map(|m| m.len()).unwrap_or(0);

        // Write snapshot
        let snap_path = args.data_dir
            .join(&args.symbol).join("snapshots")
            .join(format!("{}.arrow", trade_date.format("%Y-%m-%d")));

        if let Err(e) = snapshot_builder::write(&snap_path, &snap_bytes) {
            stats_failed.push((trade_date, format!("snapshot: {e}")));
            continue;
        }
        total_bytes += snap_bytes.len() as u64;

        // Update manifest
        if let Err(e) = db.upsert(&args.symbol, trade_date, &sha, date_rows.len() as i32) {
            eprintln!("WARN: manifest upsert failed for {trade_date}: {e}");
        }

        stats_ok += 1;
        total_rows_written += date_rows.len();
    }
    pb2.finish_with_message("done");

    // Stage 8: write spot Parquet
    if !args.dry_run {
        let spot_path = args.data_dir
            .join(&args.symbol).join("spot")
            .join(format!("{}-spot-{}.parquet", &args.symbol, args.year));
        parquet_writer::write_spot_parquet(&spot_path, &spot_bars)?;
        total_bytes += spot_path.metadata().map(|m| m.len()).unwrap_or(0);
        println!("Spot Parquet written: {}", spot_path.display());
    }

    // Summary
    println!("\n=== Migration summary ===");
    println!("  Dates OK:      {}", stats_ok);
    println!("  Dates skipped: {} (sha unchanged)", stats_skipped);
    println!("  Dates failed:  {}", stats_failed.len());
    if !stats_failed.is_empty() {
        for (d, e) in &stats_failed { println!("    {d}: {e}"); }
    }
    println!("  Rows written:  {}", total_rows_written);
    println!("  Bytes written: {:.1} MB", total_bytes as f64 / 1_048_576.0);
    if args.dry_run { println!("  [DRY RUN — no files written]"); }

    if !stats_failed.is_empty() {
        std::process::exit(1);
    }
    Ok(())
}
