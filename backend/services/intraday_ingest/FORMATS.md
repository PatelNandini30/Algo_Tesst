# Intraday CSV source formats

This document is the contract between source CSV files and ingestion handlers.
Each handler in this directory targets one of these formats. Adding a new format
requires (a) adding a section below, (b) creating a new handler module.

## clean_2023

In use from 2023 onwards. Clean, single-row-per-tick format with explicit headers.

**Header signature (used for auto-detection):** the first line is exactly
```
Date,Time,Symbol,ExpiryDate,StrikePrice,OptionType,Open,High,Low,Close,Volume,OI
```

**Delimiter:** comma, no quoting needed.
**Encoding:** UTF-8.
**Header row:** present (line 1).

**Columns:**

| Column      | Type    | Format       | Notes                          |
|-------------|---------|--------------|--------------------------------|
| Date        | date    | YYYY-MM-DD   | trade date                     |
| Time        | time    | HH:MM        | 24h, IST, 09:15..15:30         |
| Symbol      | string  |              | NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY|
| ExpiryDate  | date    | YYYY-MM-DD   |                                |
| StrikePrice | decimal | %.2f         | strike in INR                  |
| OptionType  | string  | CE \| PE     |                                |
| Open        | decimal | %.2f         |                                |
| High        | decimal | %.2f         |                                |
| Low         | decimal | %.2f         |                                |
| Close       | decimal | %.2f         |                                |
| Volume      | int     |              | contracts                      |
| OI          | int     |              | open interest                  |

**File granularity:** one CSV per (symbol, trading_date). Files are independent;
ingest order does not matter.

**Known caveats:** none observed in 2024 NIFTY data. Update this file if any
appear.

## raw_2017 (TODO — Plan F)

Pre-2023 format. Multi-format detection and cleaning is a separate plan.
