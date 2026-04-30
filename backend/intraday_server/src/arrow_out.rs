use arrow_array::{
    Float64Array, Int64Array, RecordBatch, StringArray, UInt32Array,
};
use arrow_ipc::writer::StreamWriter;
use arrow_schema::{DataType, Field, Schema};
use std::sync::Arc;

use crate::engine::types::{ChainRow, OhlcvBar, SeriesBar, TradeRecord};
use crate::error::AppError;

fn ipc_bytes(batch: RecordBatch) -> Result<Vec<u8>, AppError> {
    let mut buf = Vec::new();
    {
        let mut writer = StreamWriter::try_new(&mut buf, &batch.schema())
            .map_err(|e| AppError::Arrow(e.to_string()))?;
        writer.write(&batch).map_err(|e| AppError::Arrow(e.to_string()))?;
        writer.finish().map_err(|e| AppError::Arrow(e.to_string()))?;
    }
    Ok(buf)
}

pub fn ohlcv_to_ipc(rows: &[OhlcvBar]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("minute", DataType::Utf8, false),
        Field::new("open",   DataType::Float64, false),
        Field::new("high",   DataType::Float64, false),
        Field::new("low",    DataType::Float64, false),
        Field::new("close",  DataType::Float64, false),
        Field::new("volume", DataType::Int64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.minute.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.open).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.low).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.close).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.volume).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn chain_to_ipc(rows: &[ChainRow]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("strike",    DataType::Float64, false),
        Field::new("ce_close",  DataType::Float64, false),
        Field::new("ce_high",   DataType::Float64, false),
        Field::new("ce_low",    DataType::Float64, false),
        Field::new("ce_volume", DataType::Int64, false),
        Field::new("pe_close",  DataType::Float64, false),
        Field::new("pe_high",   DataType::Float64, false),
        Field::new("pe_low",    DataType::Float64, false),
        Field::new("pe_volume", DataType::Int64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(Float64Array::from(rows.iter().map(|r| r.strike).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_close).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.ce_low).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.ce_volume).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_close).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pe_low).collect::<Vec<_>>())),
        Arc::new(Int64Array::from(rows.iter().map(|r| r.pe_volume).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn series_to_ipc(rows: &[SeriesBar]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("date",   DataType::Utf8, false),
        Field::new("minute", DataType::Utf8, false),
        Field::new("open",   DataType::Float64, false),
        Field::new("high",   DataType::Float64, false),
        Field::new("low",    DataType::Float64, false),
        Field::new("close",  DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.date.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.minute.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.open).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.high).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.low).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.close).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub fn trades_to_ipc(rows: &[TradeRecord]) -> Result<Vec<u8>, AppError> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("date",         DataType::Utf8, false),
        Field::new("symbol",       DataType::Utf8, false),
        Field::new("expiry",       DataType::Utf8, false),
        Field::new("strike",       DataType::Float64, false),
        Field::new("opt_type",     DataType::Utf8, false),
        Field::new("action",       DataType::Utf8, false),
        Field::new("entry_time",   DataType::Utf8, false),
        Field::new("entry_price",  DataType::Float64, false),
        Field::new("exit_time",    DataType::Utf8, false),
        Field::new("exit_price",   DataType::Float64, false),
        Field::new("exit_reason",  DataType::Utf8, false),
        Field::new("quantity",     DataType::UInt32, false),
        Field::new("pnl",          DataType::Float64, false),
        Field::new("mae",          DataType::Float64, false),
        Field::new("mfe",          DataType::Float64, false),
    ]));
    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(StringArray::from(rows.iter().map(|r| r.date.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.symbol.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.expiry.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.strike).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.opt_type.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.action.as_str()).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.entry_time.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.entry_price).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.exit_time.as_str()).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.exit_price).collect::<Vec<_>>())),
        Arc::new(StringArray::from(rows.iter().map(|r| r.exit_reason.as_str()).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.quantity).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.pnl).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.mae).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.mfe).collect::<Vec<_>>())),
    ]).map_err(|e| AppError::Arrow(e.to_string()))?;
    ipc_bytes(batch)
}

pub const ARROW_CONTENT_TYPE: &str = "application/vnd.apache.arrow.stream";

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_ipc::reader::StreamReader;

    #[test]
    fn test_ohlcv_roundtrip() {
        let rows = vec![OhlcvBar { minute: "09:15".into(), open: 100.0, high: 110.0, low: 90.0, close: 105.0, volume: 0 }];
        let bytes = ohlcv_to_ipc(&rows).unwrap();
        assert!(!bytes.is_empty());
        let mut reader = StreamReader::try_new(std::io::Cursor::new(bytes), None).unwrap();
        let batch = reader.next().unwrap().unwrap();
        assert_eq!(batch.num_rows(), 1);
        assert_eq!(batch.num_columns(), 6);
    }

    #[test]
    fn test_trades_roundtrip_empty() {
        let bytes = trades_to_ipc(&[]).unwrap();
        let mut reader = StreamReader::try_new(std::io::Cursor::new(bytes), None).unwrap();
        let batch = reader.next().unwrap().unwrap();
        assert_eq!(batch.num_rows(), 0);
        assert_eq!(batch.num_columns(), 15);
    }
}
