-- ============================================================================
-- 009_filter_date_sets.sql
-- Folder-based date-range filters (replaces the 5x1 / 5x2 / base2 model that
-- lived in super_trend_segments).
--
-- Source of truth is the "Filter Dates" share, imported by
-- backend/import_filter_dates.py. Each CSV file inside a folder becomes ONE
-- selectable filter; the folder becomes a UI group. Labels are the EXACT
-- folder / file names, dates are stored verbatim from the CSV.
--
-- The engine read path (base.get_filter_segments) resolves a filter by
-- filter_key and returns its ordered [{start, end}] segments.
-- ============================================================================

CREATE TABLE IF NOT EXISTS filter_date_sets (
    id            BIGSERIAL PRIMARY KEY,
    group_key     VARCHAR(60)  NOT NULL,   -- slug of the folder, e.g. base2_nifty_options
    group_label   VARCHAR(200) NOT NULL,   -- exact folder name
    group_order   INT          NOT NULL DEFAULT 0,
    filter_key    VARCHAR(120) NOT NULL,   -- slug of the CSV file (payload value)
    filter_label  VARCHAR(255) NOT NULL,   -- exact file name (no .csv)
    filter_order  INT          NOT NULL DEFAULT 0,
    source_file   VARCHAR(300),            -- original CSV filename
    seq           INT          NOT NULL,   -- row order within the file
    start_date    DATE         NOT NULL,
    end_date      DATE         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date),
    UNIQUE (filter_key, seq)
);

CREATE INDEX IF NOT EXISTS idx_filter_date_sets_filter
    ON filter_date_sets (filter_key, seq);

CREATE INDEX IF NOT EXISTS idx_filter_date_sets_group
    ON filter_date_sets (group_order, filter_order, seq);
