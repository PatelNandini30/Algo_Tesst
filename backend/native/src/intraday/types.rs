use serde::Deserialize;

#[derive(Deserialize, Debug)]
pub struct StrategySpec {
    pub symbol: String,
    pub date_from: String,   // "YYYY-MM-DD"
    pub date_to: String,
    pub entry_time: String,  // "HH:MM"
    pub square_off_time: String,
    pub legs: Vec<LegSpec>,
}

#[derive(Deserialize, Debug)]
pub struct LegSpec {
    pub opt_type: String,   // "CE" | "PE"
    pub action: String,     // "BUY" | "SELL"
    pub strike_selection: StrikeSelection,
    pub expiry: String,     // "WEEKLY" | "MONTHLY" | "NEXT_WEEKLY" | "NEXT_MONTHLY"
    pub quantity: u32,
    pub sl: Option<ExitCond>,
    pub target: Option<ExitCond>,
}

#[derive(Deserialize, Debug)]
pub struct StrikeSelection {
    pub mode: String,   // "ATM" | "ATM_OFFSET"
    pub value: i32,     // 0 for ATM; ±1..±5 for offset
}

#[derive(Deserialize, Debug)]
pub struct ExitCond {
    #[serde(rename = "type")]
    pub kind: String,   // "percent" | "points"
    pub value: f64,
}

#[derive(Debug)]
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_parse_strategy_spec() {
        let json = r#"{
            "symbol": "NIFTY",
            "date_from": "2024-01-01",
            "date_to": "2024-01-31",
            "entry_time": "09:20",
            "square_off_time": "15:15",
            "legs": [{
                "opt_type": "CE",
                "action": "SELL",
                "strike_selection": {"mode": "ATM", "value": 0},
                "expiry": "WEEKLY",
                "quantity": 1,
                "sl": {"type": "percent", "value": 50.0},
                "target": null
            }]
        }"#;
        let spec: StrategySpec = serde_json::from_str(json).unwrap();
        assert_eq!(spec.symbol, "NIFTY");
        assert_eq!(spec.legs.len(), 1);
        assert_eq!(spec.legs[0].strike_selection.value, 0);
    }
}
