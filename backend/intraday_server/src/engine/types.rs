use serde::{Deserialize, Serialize};

// ── Strategy config (inbound from API) ────────────────────────────────────

#[derive(Deserialize, Debug, Clone)]
pub struct StrategySpec {
    pub symbol: String,
    pub date_from: String,
    pub date_to: String,
    pub entry_time: String,
    pub square_off_time: String,
    pub legs: Vec<LegSpec>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct LegSpec {
    pub opt_type: String,
    pub action: String,
    pub strike_selection: StrikeSelection,
    pub expiry: String,
    pub quantity: u32,
    pub sl: Option<ExitCond>,
    pub target: Option<ExitCond>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct StrikeSelection {
    pub mode: String,
    pub value: i32,
}

#[derive(Deserialize, Debug, Clone)]
pub struct ExitCond {
    #[serde(rename = "type")]
    pub kind: String,
    pub value: f64,
}

// ── Output records ────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct TradeRecord {
    pub date: String,
    pub symbol: String,
    pub expiry: String,
    pub strike: f64,
    pub opt_type: String,
    pub action: String,
    pub entry_time: String,
    pub entry_price: f64,
    pub exit_time: String,
    pub exit_price: f64,
    pub exit_reason: String,
    pub quantity: u32,
    pub pnl: f64,
    pub mae: f64,
    pub mfe: f64,
}

#[derive(Debug, Clone)]
pub struct OhlcvBar {
    pub minute: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

#[derive(Debug, Clone)]
pub struct ChainRow {
    pub strike: f64,
    pub ce_close: f64,
    pub ce_high: f64,
    pub ce_low: f64,
    pub ce_volume: i64,
    pub pe_close: f64,
    pub pe_high: f64,
    pub pe_low: f64,
    pub pe_volume: i64,
}

#[derive(Debug, Clone)]
pub struct SeriesBar {
    pub date: String,
    pub minute: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
}

// ── Query enums ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum OptType { Ce, Pe }

impl OptType {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "CE" => Some(Self::Ce),
            "PE" => Some(Self::Pe),
            _ => None,
        }
    }
    pub fn chain_idx(self) -> usize { match self { Self::Ce => 0, Self::Pe => 1 } }
}

#[derive(Debug, Clone, Copy)]
pub enum ExpiryMode { Weekly, Monthly }

impl ExpiryMode {
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "WEEKLY" => Some(Self::Weekly),
            "MONTHLY" => Some(Self::Monthly),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub enum Resolution { M1, M5, M15, D1 }

impl Resolution {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "1m" => Some(Self::M1),
            "5m" => Some(Self::M5),
            "15m" => Some(Self::M15),
            "1d" => Some(Self::D1),
            _ => None,
        }
    }
    pub fn minutes(self) -> usize {
        match self { Self::M1 => 1, Self::M5 => 5, Self::M15 => 15, Self::D1 => 375 }
    }
}

// ── Backtest job request (inbound from API) ───────────────────────────────

#[derive(Deserialize, Serialize, Debug, Clone)]
pub struct BacktestRequest {
    pub symbol: String,
    pub date_from: String,
    pub date_to: String,
    pub entry_time: String,
    pub square_off_time: String,
    pub legs: Vec<serde_json::Value>,
}

impl BacktestRequest {
    pub fn canonical_key(&self) -> String {
        let payload = serde_json::to_string(self).unwrap_or_default();
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        payload.hash(&mut h);
        format!("{:016x}", h.finish())
    }

    pub fn requires_slow_path(&self) -> bool {
        self.legs.iter().any(|leg| {
            leg.get("strike_selection")
                .and_then(|ss| ss.get("value"))
                .and_then(|v| v.as_i64())
                .map(|v| v.abs() > 5)
                .unwrap_or(false)
        })
    }

    pub fn validate(&self) -> Result<(), String> {
        let valid_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];
        if !valid_symbols.contains(&self.symbol.as_str()) {
            return Err(format!("symbol must be one of {:?}", valid_symbols));
        }
        if self.legs.is_empty() { return Err("at least 1 leg required".into()); }
        if self.legs.len() > 6 { return Err("at most 6 legs allowed".into()); }
        Ok(())
    }
}
