//! Phase-5 Rust tradesheet writer (rust_xlsxwriter). A cell-identical port of
//! services/optimizer/excel_builder._write_trade_sheet, promotion-gated by
//! tools/xlsx_celldiff (must reach 0 cell-diffs). Default-off until proven.
//!
//! Only the "Trade Sheet" is ported here (first sheet). Summary / Patch-wise /
//! WOW-MOM remain openpyxl until each passes the same gate.

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};
use rust_xlsxwriter::{Color, ExcelDateTime, Format, FormatAlign, FormatBorder, Workbook, Worksheet};
use std::collections::HashMap;

// ── palette (matches excel_builder.py) ──────────────────────────────────────
const HEADER_BG: u32 = 0x34495E;
const GREEN_BG: u32 = 0xD4EFDF;
const GREEN_TX: u32 = 0x1E7E34;
const RED_BG: u32 = 0xFDE8E8;
const RED_TX: u32 = 0xC0392B;
const ALT_ROW: u32 = 0xF9FBFD;
const WHITE: u32 = 0xFFFFFF;
const BORDER_CLR: u32 = 0xB0C4D8;
const BLACK: u32 = 0x000000;

// rust_xlsxwriter's set_column_width() treats the value as a user-visible width and
// stores width + max-digit padding (0.7109375 for the default font). openpyxl stores
// the raw width. Subtract the padding so our stored width equals openpyxl's exactly.
const COL_PAD: f64 = 0.7109375;

fn date_cols() -> &'static [&'static str] {
    &["Entry Date", "Exit Date", "Expiry", "Leg Exit Date", "Lazy Entry Date", "Lazy Exit Date"]
}
fn true_pct_cols() -> &'static [&'static str] {
    &["Spot P&L %", "CE P&L %", "PE P&L %", "FUT P&L %", "%DD"]
}
fn mae_cols() -> &'static [&'static str] {
    &["MAE", "MFE", "Net MAE 1", "Net MAE 2", "Final MAE", "Midcap MAE", "Midcap MFE",
      "Combined Net MAE 1", "Combined Net MAE 2", "Combined Final MAE"]
}
fn col_width(key: &str) -> f64 {
    let w: &[(&str, i32)] = &[
        ("Leg", 12), ("Entry Date", 13), ("Exit Date", 13), ("Entry Spot", 12), ("Exit Spot", 12),
        ("buffer_ref_price", 12), ("buffer_strike_offset", 10), ("Re-Entry Type", 14),
        ("Raw Entry Price", 12), ("Entry Price", 12), ("Raw Exit Price", 12), ("Exit Price", 12),
        ("MAE", 9), ("MFE", 9), ("Net MAE 1", 10), ("Net MAE 2", 10), ("Final MAE", 10),
        ("Net P&L", 10), ("% P&L", 8), ("Cumulative", 11), ("Peak", 10), ("DD", 9), ("%DD", 8),
        ("Lowest NAV", 13), ("Actual Live DD", 15), ("Spot P&L %", 10), ("CE P&L %", 10), ("PE P&L %", 10), ("FUT P&L %", 10),
        ("ATM Strike", 11), ("ATM Call Price", 13), ("ATM Put Price", 13), ("ATM Call+Put Price", 16),
        ("ATM Straddle Price Source", 40), ("Exit Reason", 14), ("Strike Shift Reason", 40),
        ("Expiry", 12), ("STR Segment", 14), ("Filter Segment", 22),
        ("Midcap Entry Spot", 15), ("Midcap Exit Spot", 15), ("Midcap Spot P&L", 14),
        ("Midcap Spot P&L %", 15), ("Midcap No Of Days", 15), ("Midcap Rollover Cost %", 18),
        ("Midcap Hypo P&L", 15), ("Midcap Hypo P&L %", 16), ("Midcap MAE", 12), ("Midcap MFE", 12),
        ("Combined Net P&L", 15), ("Combined Net P&L %", 16), ("Combined Cumulative", 17),
        ("Combined Peak", 13), ("Combined DD", 12), ("Combined %DD", 12),
        ("Combined Net MAE 1", 16), ("Combined Net MAE 2", 16), ("Combined Final MAE", 15),
        ("Combined Lowest NAV", 16), ("Combined Actual Live DD", 18),
    ];
    for (k, v) in w {
        if *k == key {
            return *v as f64;
        }
    }
    10.0
}

/// Mirror excel_builder._to_num: strip , % ₹ and parse; None on failure/empty.
fn to_num(v: &Bound<PyAny>) -> Option<f64> {
    if let Ok(f) = v.extract::<f64>() {
        if f.is_finite() {
            return Some(f);
        }
        return None;
    }
    if v.is_none() {
        return None;
    }
    let s = v.str().ok()?.to_string();
    let s = s.replace(',', "").replace('%', "").replace('₹', "");
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    match s.parse::<f64>() {
        Ok(f) if f.is_finite() => Some(f),
        _ => None,
    }
}

/// Mirror excel_builder._parse_date: try the year-first then day-first formats.
fn parse_date(s: &str) -> Option<(i32, u32, u32)> {
    use chrono::NaiveDate;
    let s = s.trim();
    if s.is_empty() {
        return None;
    }
    let fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%B-%Y"];
    for f in fmts {
        if let Ok(d) = NaiveDate::parse_from_str(s, f) {
            use chrono::Datelike;
            return Some((d.year(), d.month(), d.day()));
        }
    }
    None
}

fn base_format(bg: u32, bold: bool, font_color: u32) -> Format {
    let mut fmt = Format::new()
        .set_font_name("Calibri")
        .set_font_size(10)
        .set_font_color(Color::RGB(font_color))
        .set_background_color(Color::RGB(bg))
        .set_border(FormatBorder::Thin)
        .set_border_color(Color::RGB(BORDER_CLR))
        .set_align(FormatAlign::Left)
        .set_align(FormatAlign::VerticalCenter);
    if bold {
        fmt = fmt.set_bold();
    }
    fmt
}

// Build the "Trade Sheet" as a standalone worksheet (no save) so it can be pushed
// into either a single-sheet workbook or the combined 4-sheet workbook.
fn build_trade_sheet(
    cleaned: &Bound<'_, PyList>,
    key_order: &[String],
) -> PyResult<Worksheet> {
    let dcols = date_cols();
    let pcols = true_pct_cols();
    let mcols = mae_cols();

    let mut ws = Worksheet::new();
    ws.set_name("Trade Sheet")
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    ws.set_freeze_panes(1, 0)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

    for (ci, key) in key_order.iter().enumerate() {
        ws.set_column_width(ci as u16, col_width(key) - COL_PAD)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    }

    // Header row (height 22)
    let hdr = base_format(HEADER_BG, true, WHITE)
        .set_align(FormatAlign::Center)
        .set_align(FormatAlign::VerticalCenter);
    ws.set_row_height(0, 22.0)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    for (ci, key) in key_order.iter().enumerate() {
        ws.write_string_with_format(0, ci as u16, key, &hdr)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    }

    // Format cache: (bg, bold, font_color, num_format) -> Format
    let mut cache: HashMap<(u32, bool, u32, &'static str), Format> = HashMap::new();
    let mut get_fmt = |bg: u32, bold: bool, fc: u32, nf: &'static str| -> Format {
        if let Some(f) = cache.get(&(bg, bold, fc, nf)) {
            return f.clone();
        }
        let mut f = base_format(bg, bold, fc);
        if !nf.is_empty() {
            f = f.set_num_format(nf);
        }
        cache.insert((bg, bold, fc, nf), f.clone());
        f
    };

    let n = cleaned.len();
    for ri in 0..n {
        let row = cleaned.get_item(ri)?;
        let row: &Bound<PyDict> = row.downcast()?;
        // openpyxl: `for ri, row in enumerate(cleaned, 2)` → bg = WHITE if ri%2==0
        // else ALT_ROW. Our 0-based data index `ri` maps to openpyxl ri = ri+2,
        // so bg = WHITE iff ri is even. Rust rows are 0-based (header = row 0).
        let excel_row = (ri + 1) as u32;
        let bg = if ri % 2 == 0 { WHITE } else { ALT_ROW };

        ws.set_row_height(excel_row, 18.0)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        // Determine coloring inputs
        let net_num = match row.get_item("Net P&L")? {
            Some(v) => to_num(&v),
            None => None,
        };
        let c_net = match row.get_item("Combined Net P&L")? {
            Some(v) => to_num(&v),
            None => None,
        };

        for (ci, key) in key_order.iter().enumerate() {
            let col = ci as u16;
            let raw_opt = row.get_item(key.as_str())?;

            // coloring override for these columns
            let (bold, fc, cell_bg) = if net_num.is_some()
                && (key == "Net P&L" || key == "% P&L")
            {
                let pos = net_num.unwrap() >= 0.0;
                (true, if pos { GREEN_TX } else { RED_TX }, if pos { GREEN_BG } else { RED_BG })
            } else if c_net.is_some()
                && (key == "Combined Net P&L" || key == "Combined Net P&L %")
            {
                let pos = c_net.unwrap() >= 0.0;
                (true, if pos { GREEN_TX } else { RED_TX }, if pos { GREEN_BG } else { RED_BG })
            } else {
                (false, BLACK, bg)
            };

            // DATE columns
            if dcols.contains(&key.as_str()) {
                let s = match &raw_opt {
                    Some(v) if !v.is_none() => v.str().ok().map(|x| x.to_string()).unwrap_or_default(),
                    _ => String::new(),
                };
                if let Some((y, m, d)) = parse_date(&s) {
                    let dt = ExcelDateTime::from_ymd(y as u16, m as u8, d as u8)
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                    let f = get_fmt(cell_bg, bold, fc, "DD-MMM-YYYY");
                    ws.write_datetime_with_format(excel_row, col, &dt, &f)
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                } else if !s.is_empty() {
                    let f = get_fmt(cell_bg, bold, fc, "");
                    ws.write_string_with_format(excel_row, col, &s, &f)
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                } else {
                    let f = get_fmt(cell_bg, bold, fc, "");
                    ws.write_blank(excel_row, col, &f)
                        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                }
                continue;
            }

            // numeric?
            let num = match &raw_opt {
                Some(v) if !v.is_none() => to_num(v),
                _ => None,
            };
            if let Some(num) = num {
                let nf: &'static str = if pcols.contains(&key.as_str()) {
                    "0.00%"
                } else if mcols.contains(&key.as_str()) {
                    "#,##0.0000"
                } else if num == num.trunc() {
                    "0"
                } else {
                    "#,##0.00"
                };
                let f = get_fmt(cell_bg, bold, fc, nf);
                ws.write_number_with_format(excel_row, col, num, &f)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
                continue;
            }

            // text / blank
            let s = match &raw_opt {
                Some(v) if !v.is_none() => v.str().ok().map(|x| x.to_string()).unwrap_or_default(),
                _ => String::new(),
            };
            let f = get_fmt(cell_bg, bold, fc, "");
            if s.is_empty() {
                ws.write_blank(excel_row, col, &f)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            } else {
                ws.write_string_with_format(excel_row, col, &s, &f)
                    .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            }
        }
    }

    Ok(ws)
}

#[pyfunction]
pub fn write_trade_sheet_xlsx(
    cleaned: &Bound<'_, PyList>,
    key_order: Vec<String>,
    path: String,
) -> PyResult<()> {
    let ws = build_trade_sheet(cleaned, &key_order)?;
    let mut wb = Workbook::new();
    wb.push_worksheet(ws);
    wb.save(&path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(())
}

// ── Generic layout-sheet replayer ───────────────────────────────────────────
// Consumes a plain ops dict built by excel_builder._summary_ops (and, later,
// the Patch-wise / WOW-MOM ops builders). Python computes every value; Rust only
// writes the styled cells — so the numbers are the single source of truth and
// only the styling is verified cell-identical by tools/xlsx_celldiff.

#[inline]
fn hex6(s: &str) -> u32 {
    u32::from_str_radix(s.trim_start_matches('#'), 16).unwrap_or(0)
}

// Border spec: bool (thin-all / none) for the simple sheets, or a per-side style
// list [top,left,bottom,right] (each None|"thin"|"medium"|"thick") extracted from
// openpyxl for the WOW/MOM sheet's month-edge and drawdown-box borders.
enum BorderSpec {
    None,
    ThinAll,
    Sides([Option<String>; 4]),
}

fn parse_border(obj: &Bound<'_, PyAny>) -> BorderSpec {
    if let Ok(b) = obj.extract::<bool>() {
        return if b { BorderSpec::ThinAll } else { BorderSpec::None };
    }
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut sides: [Option<String>; 4] = [None, None, None, None];
        for (i, x) in list.iter().enumerate().take(4) {
            sides[i] = x.extract::<Option<String>>().unwrap_or(None);
        }
        return BorderSpec::Sides(sides);
    }
    BorderSpec::None
}

fn border_style(name: &str) -> FormatBorder {
    match name {
        "thin" => FormatBorder::Thin,
        "medium" => FormatBorder::Medium,
        "thick" => FormatBorder::Thick,
        "hair" => FormatBorder::Hair,
        "double" => FormatBorder::Double,
        "dashed" => FormatBorder::Dashed,
        "dotted" => FormatBorder::Dotted,
        _ => FormatBorder::Thin,
    }
}

fn layout_format(bold: bool, size: f64, fc: u32, bg: Option<u32>, align: &str,
                 border: &BorderSpec, nfmt: &str) -> Format {
    let mut f = Format::new()
        .set_font_name("Calibri")
        .set_font_size(size)
        .set_font_color(Color::RGB(fc));
    if let Some(b) = bg {
        f = f.set_background_color(Color::RGB(b));
    }
    f = f
        .set_align(if align == "C" { FormatAlign::Center } else { FormatAlign::Left })
        .set_align(FormatAlign::VerticalCenter);
    match border {
        BorderSpec::ThinAll => {
            f = f.set_border(FormatBorder::Thin).set_border_color(Color::RGB(BORDER_CLR));
        }
        BorderSpec::Sides(sides) => {
            // thin edges use the light border colour; anything heavier is black
            // (matches wow_mom.py: _thin=B0C4D8, medium box=000000).
            for (i, s) in sides.iter().enumerate() {
                if let Some(name) = s {
                    let fb = border_style(name);
                    let col = if name == "thin" { Color::RGB(BORDER_CLR) } else { Color::RGB(BLACK) };
                    f = match i {
                        0 => f.set_border_top(fb).set_border_top_color(col),
                        1 => f.set_border_left(fb).set_border_left_color(col),
                        2 => f.set_border_bottom(fb).set_border_bottom_color(col),
                        _ => f.set_border_right(fb).set_border_right_color(col),
                    };
                }
            }
        }
        BorderSpec::None => {}
    }
    if bold {
        f = f.set_bold();
    }
    if !nfmt.is_empty() && nfmt != "General" {
        f = f.set_num_format(nfmt);
    }
    f
}

fn build_layout_sheet(sheet: &Bound<'_, PyDict>) -> PyResult<Worksheet> {
    let rt = |e: rust_xlsxwriter::XlsxError| pyo3::exceptions::PyRuntimeError::new_err(e.to_string());

    let name: String = sheet
        .get_item("name")?
        .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err("name"))?
        .extract()?;
    let mut ws = Worksheet::new();
    ws.set_name(&name).map_err(rt)?;

    // freeze panes (r, c) 1-based-row/0-based? excel_builder emits None for these sheets.
    if let Some(fr) = sheet.get_item("freeze")? {
        if !fr.is_none() {
            let (r, c): (u32, u16) = fr.extract()?;
            ws.set_freeze_panes(r, c).map_err(rt)?;
        }
    }

    // column widths (1-indexed col); subtract COL_PAD so stored width == openpyxl's.
    if let Some(cw) = sheet.get_item("col_widths")? {
        for item in cw.downcast::<PyList>()?.iter() {
            let (c, w): (u16, f64) = item.extract()?;
            ws.set_column_width(c - 1, w - COL_PAD).map_err(rt)?;
        }
    }

    // row heights (1-indexed row)
    if let Some(rh) = sheet.get_item("row_heights")? {
        for item in rh.downcast::<PyList>()?.iter() {
            let (r, h): (u32, f64) = item.extract()?;
            ws.set_row_height(r - 1, h).map_err(rt)?;
        }
    }

    // merge top-left map: (row, first_col) -> (last_row, last_col)  (all 1-indexed).
    // Accepts 3-tuples (r,c1,c2) single-row from Summary/Patch, or 4-tuples
    // (r1,c1,r2,c2) multi-row from the extracted WOW/MOM sheet.
    let mut merge_tl: HashMap<(u32, u16), (u32, u16)> = HashMap::new();
    if let Some(m) = sheet.get_item("merges")? {
        for item in m.downcast::<PyList>()?.iter() {
            let t = item.downcast::<pyo3::types::PyTuple>()?;
            if t.len() == 4 {
                let (r1, c1, r2, c2): (u32, u16, u32, u16) = item.extract()?;
                merge_tl.insert((r1, c1), (r2, c2));
            } else {
                let (r, c1, c2): (u32, u16, u16) = item.extract()?;
                merge_tl.insert((r, c1), (r, c2));
            }
        }
    }

    // cells
    if let Some(cells) = sheet.get_item("cells")? {
        for item in cells.downcast::<PyList>()?.iter() {
            let cd = item.downcast::<PyDict>()?;
            let r: u32 = cd.get_item("r")?.unwrap().extract()?;
            let c: u16 = cd.get_item("c")?.unwrap().extract()?;
            let bold: bool = cd.get_item("bold")?.unwrap().extract()?;
            let size: f64 = cd.get_item("size")?.unwrap().extract()?;
            let fc: u32 = hex6(&cd.get_item("fc")?.unwrap().extract::<String>()?);
            let bg: Option<u32> = match cd.get_item("bg")? {
                Some(v) if !v.is_none() => Some(hex6(&v.extract::<String>()?)),
                _ => None,
            };
            let align: String = cd.get_item("align")?.unwrap().extract()?;
            let border = parse_border(&cd.get_item("border")?.unwrap());
            let nfmt: String = match cd.get_item("nfmt")? {
                Some(v) if !v.is_none() => v.extract()?,
                _ => String::new(),
            };
            let fmt = layout_format(bold, size, fc, bg, &align, &border, &nfmt);
            let vobj = cd.get_item("v")?.unwrap();
            let er = r - 1;
            let ec = c - 1;

            if let Some(&(r2, c2)) = merge_tl.get(&(r, c)) {
                // Establish the merge with the top-left's format. Fillers get the same
                // format but are visually covered (and skipped by the gate). A string
                // value goes straight into merge_range; a number needs an overwrite.
                if vobj.is_instance_of::<PyString>() {
                    let s: String = vobj.extract()?;
                    ws.merge_range(er, ec, r2 - 1, c2 - 1, &s, &fmt).map_err(rt)?;
                } else if let Ok(numv) = vobj.extract::<f64>() {
                    ws.merge_range(er, ec, r2 - 1, c2 - 1, "", &fmt).map_err(rt)?;
                    ws.write_number_with_format(er, ec, numv, &fmt).map_err(rt)?;
                } else {
                    ws.merge_range(er, ec, r2 - 1, c2 - 1, "", &fmt).map_err(rt)?;
                }
                continue;
            }

            if vobj.is_instance_of::<PyString>() {
                let s: String = vobj.extract()?;
                if s.is_empty() {
                    ws.write_blank(er, ec, &fmt).map_err(rt)?;
                } else {
                    ws.write_string_with_format(er, ec, &s, &fmt).map_err(rt)?;
                }
            } else if let Ok(numv) = vobj.extract::<f64>() {
                ws.write_number_with_format(er, ec, numv, &fmt).map_err(rt)?;
            } else {
                ws.write_blank(er, ec, &fmt).map_err(rt)?;
            }
        }
    }

    Ok(ws)
}

#[pyfunction]
pub fn write_layout_sheet_xlsx(sheet: &Bound<'_, PyDict>, path: String) -> PyResult<()> {
    let ws = build_layout_sheet(sheet)?;
    let mut wb = Workbook::new();
    wb.push_worksheet(ws);
    wb.save(&path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(())
}

// ── Combined workbook: every sheet in one .xlsx, in build_combo_xlsx order ──
// [Rules] · Trade Sheet (Rust from cleaned) · Summary · [Patch wise] · WOW & MOM.
// patch_ops / wow_ops are None when that sheet is absent (matching openpyxl).
//
// rules_ops is LAST in the signature and defaults to None purely so existing
// callers keep working unchanged; when present it is pushed FIRST, because the
// leg-wise Rules sheet is the workbook's first tab. Without this parameter a
// tradesheet that wanted a Rules sheet had to fall back to openpyxl, which cost
// ~2.4 s per combo — the whole reason sweeps ran slow.
#[pyfunction]
#[pyo3(signature = (trade_cleaned, trade_key_order, summary_ops, patch_ops, wow_ops, path, rules_ops=None))]
pub fn write_workbook_xlsx(
    trade_cleaned: &Bound<'_, PyList>,
    trade_key_order: Vec<String>,
    summary_ops: &Bound<'_, PyDict>,
    patch_ops: Option<Bound<'_, PyDict>>,
    wow_ops: Option<Bound<'_, PyDict>>,
    path: String,
    rules_ops: Option<Bound<'_, PyDict>>,
) -> PyResult<()> {
    let mut wb = Workbook::new();
    if let Some(r) = &rules_ops {
        wb.push_worksheet(build_layout_sheet(r)?);
    }
    wb.push_worksheet(build_trade_sheet(trade_cleaned, &trade_key_order)?);
    wb.push_worksheet(build_layout_sheet(summary_ops)?);
    if let Some(p) = &patch_ops {
        wb.push_worksheet(build_layout_sheet(p)?);
    }
    if let Some(w) = &wow_ops {
        wb.push_worksheet(build_layout_sheet(w)?);
    }
    wb.save(&path)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(())
}
