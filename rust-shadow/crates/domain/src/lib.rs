use std::collections::BTreeMap;

use chrono::NaiveDate;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum DomainError {
    #[error("invalid strategy at {path}: {message}")]
    InvalidStrategy { path: String, message: String },
    #[error("invalid parameter path: {0}")]
    InvalidPath(String),
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "UPPERCASE")]
pub enum Position {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "UPPERCASE")]
pub enum OptionType {
    Ce,
    Pe,
    Fut,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DateSegment {
    #[serde(alias = "Start", alias = "from", alias = "start_date")]
    pub start: NaiveDate,
    #[serde(alias = "End", alias = "to", alias = "end_date")]
    pub end: NaiveDate,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct StrikeSelection {
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub strike_type: Option<String>,
    #[serde(default)]
    pub value: Option<f64>,
    #[serde(default)]
    pub delta: Option<f64>,
    #[serde(default)]
    pub premium: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct RiskRule {
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub value: Option<f64>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub trigger: Option<f64>,
    #[serde(default, alias = "move")]
    pub move_value: Option<f64>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct ReentryConfig {
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub count: Option<u32>,
    #[serde(default, rename = "lazyLegConfig", alias = "lazy_leg_config")]
    pub lazy_leg_config: Option<Box<LegConfig>>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct SpotAdjustment {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default, alias = "value")]
    pub pct: Option<f64>,
    #[serde(default)]
    pub direction: Option<String>,
    #[serde(default)]
    pub units: Option<String>,
    #[serde(default)]
    pub confirm_days: Option<u32>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct LegConfig {
    #[serde(default)]
    pub index: Option<String>,
    #[serde(default)]
    pub segment: Option<String>,
    #[serde(default)]
    pub option_type: Option<String>,
    #[serde(default)]
    pub position: Option<String>,
    #[serde(default)]
    pub expiry: Option<String>,
    #[serde(default)]
    pub lots: Option<f64>,
    #[serde(default, alias = "qty")]
    pub quantity: Option<f64>,
    #[serde(default)]
    pub slippage_pct: Option<f64>,
    #[serde(default)]
    pub strike_selection: StrikeSelection,
    #[serde(default, rename = "stopLoss")]
    pub stop_loss: Option<RiskRule>,
    #[serde(default, rename = "targetProfit")]
    pub target_profit: Option<RiskRule>,
    #[serde(default, rename = "trailSL")]
    pub trail_sl: Option<RiskRule>,
    #[serde(default, rename = "slWithBuffer")]
    pub sl_with_buffer: Option<RiskRule>,
    #[serde(default, rename = "reEntryOnSL")]
    pub reentry_on_sl: Option<ReentryConfig>,
    #[serde(default, rename = "reEntryOnTarget")]
    pub reentry_on_target: Option<ReentryConfig>,
    #[serde(default, alias = "spotAdjustment")]
    pub spot_adjustment: Option<SpotAdjustment>,
    #[serde(default)]
    pub filter_segments: Vec<DateSegment>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct StrategyConfig {
    #[serde(default, alias = "symbol")]
    pub index: String,
    #[serde(default, alias = "date_from")]
    pub from_date: Option<String>,
    #[serde(default, alias = "date_to")]
    pub to_date: Option<String>,
    #[serde(default)]
    pub expiry_type: Option<String>,
    #[serde(default)]
    pub entry_dte: Option<u32>,
    #[serde(default)]
    pub exit_dte: Option<u32>,
    #[serde(default)]
    pub legs: Vec<LegConfig>,
    #[serde(default)]
    pub filter_segments: Vec<DateSegment>,
    #[serde(default)]
    pub spot_adjustment_enabled: bool,
    #[serde(default)]
    pub spot_adjustment_pct: Option<f64>,
    #[serde(default)]
    pub spot_adjustment_direction: Option<String>,
    #[serde(default)]
    pub overall_sl_value: Option<f64>,
    #[serde(default)]
    pub overall_target_value: Option<f64>,
    #[serde(default)]
    pub rollover_toggle: bool,
    #[serde(default)]
    pub per_leg_rollover: bool,
    #[serde(default)]
    pub multi_index_mode: bool,
    #[serde(default)]
    pub midcap_legs: Vec<Value>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl StrategyConfig {
    /// Effective folder-based filter key for entry-window gating. Mirrors the
    /// live backend: `filter_config` is the real folder key (any `filter_key`
    /// in `filter_date_sets`, or legacy `5x1`/`5x2`/`base2`); the legacy
    /// `super_trend_config` is accepted as a fallback. `none`/`custom`/empty are
    /// not resolvable keys — `custom` and uploaded windows arrive via
    /// `filter_segments` instead, handled by the caller. Returns `None` when no
    /// named filter applies.
    pub fn filter_key(&self) -> Option<&str> {
        for field in ["filter_config", "super_trend_config"] {
            if let Some(value) = self.extra.get(field).and_then(|value| value.as_str()) {
                let trimmed = value.trim();
                if !trimmed.is_empty()
                    && !trimmed.eq_ignore_ascii_case("none")
                    && !trimmed.eq_ignore_ascii_case("custom")
                {
                    return Some(trimmed);
                }
            }
        }
        None
    }

    pub fn validate(&self) -> Result<(), DomainError> {
        if self.index.trim().is_empty() {
            return Err(DomainError::InvalidStrategy {
                path: "index".into(),
                message: "index/symbol is required".into(),
            });
        }
        if self.legs.is_empty() {
            return Err(DomainError::InvalidStrategy {
                path: "legs".into(),
                message: "at least one leg is required".into(),
            });
        }
        if let (Some(entry_dte), Some(exit_dte)) = (self.entry_dte, self.exit_dte) {
            if exit_dte > entry_dte {
                return Err(DomainError::InvalidStrategy {
                    path: "exit_dte".into(),
                    message: format!("exit_dte ({exit_dte}) cannot exceed entry_dte ({entry_dte})"),
                });
            }
        }
        for (index, segment) in self.filter_segments.iter().enumerate() {
            if segment.start > segment.end {
                return Err(DomainError::InvalidStrategy {
                    path: format!("filter_segments[{index}]"),
                    message: "start cannot be after end".into(),
                });
            }
        }
        for (i, leg) in self.legs.iter().enumerate() {
            let position = leg.position.as_deref().unwrap_or("").to_ascii_uppercase();
            if position != "BUY" && position != "SELL" {
                return Err(DomainError::InvalidStrategy {
                    path: format!("legs[{i}].position"),
                    message: "must be BUY or SELL".into(),
                });
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ComboOverride {
    pub combo_id: u64,
    #[serde(default)]
    pub values: BTreeMap<String, Value>,
}

pub fn canonical_fingerprint(value: &Value) -> String {
    fn canonical(v: &Value) -> Value {
        match v {
            Value::Object(map) => {
                let ordered: BTreeMap<_, _> =
                    map.iter().map(|(k, v)| (k.clone(), canonical(v))).collect();
                serde_json::to_value(ordered).expect("BTreeMap serialization cannot fail")
            }
            Value::Array(items) => Value::Array(items.iter().map(canonical).collect()),
            _ => v.clone(),
        }
    }
    let bytes =
        serde_json::to_vec(&canonical(value)).expect("JSON value serialization cannot fail");
    let digest = Sha256::digest(bytes);
    format!("{digest:x}")
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum PathToken {
    Key(String),
    Index(usize),
}

fn tokenize_path(path: &str) -> Result<Vec<PathToken>, DomainError> {
    let mut out = Vec::new();
    let chars: Vec<char> = path.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '.' {
            i += 1;
            continue;
        }
        if chars[i] == '[' {
            i += 1;
            let start = i;
            while i < chars.len() && chars[i].is_ascii_digit() {
                i += 1;
            }
            if start == i || i >= chars.len() || chars[i] != ']' {
                return Err(DomainError::InvalidPath(path.into()));
            }
            let n: usize = chars[start..i]
                .iter()
                .collect::<String>()
                .parse()
                .map_err(|_| DomainError::InvalidPath(path.into()))?;
            out.push(PathToken::Index(n));
            i += 1;
            continue;
        }
        let start = i;
        while i < chars.len() && chars[i] != '.' && chars[i] != '[' {
            i += 1;
        }
        if start == i {
            return Err(DomainError::InvalidPath(path.into()));
        }
        out.push(PathToken::Key(chars[start..i].iter().collect()));
    }
    if out.is_empty() {
        Err(DomainError::InvalidPath(path.into()))
    } else {
        Ok(out)
    }
}

pub fn set_json_path(root: &mut Value, path: &str, value: Value) -> Result<(), DomainError> {
    let tokens = tokenize_path(path)?;
    let mut current = root;
    for (pos, token) in tokens.iter().enumerate() {
        let last = pos + 1 == tokens.len();
        match token {
            PathToken::Key(key) => {
                let map = current
                    .as_object_mut()
                    .ok_or_else(|| DomainError::InvalidPath(path.into()))?;
                if last {
                    map.insert(key.clone(), value);
                    return Ok(());
                }
                let next_is_index = matches!(tokens[pos + 1], PathToken::Index(_));
                current = map.entry(key.clone()).or_insert_with(|| {
                    if next_is_index {
                        Value::Array(vec![])
                    } else {
                        Value::Object(Default::default())
                    }
                });
            }
            PathToken::Index(index) => {
                let array = current
                    .as_array_mut()
                    .ok_or_else(|| DomainError::InvalidPath(path.into()))?;
                while array.len() <= *index {
                    array.push(Value::Object(Default::default()));
                }
                if last {
                    array[*index] = value;
                    return Ok(());
                }
                current = &mut array[*index];
            }
        }
    }
    Err(DomainError::InvalidPath(path.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn nested_paths_match_live_notation() {
        let mut value = json!({"legs": [{"stopLoss": {"value": 10}}]});
        set_json_path(&mut value, "legs[0].stopLoss.value", json!(25)).unwrap();
        assert_eq!(value["legs"][0]["stopLoss"]["value"], 25);
    }

    #[test]
    fn fingerprint_is_key_order_independent() {
        assert_eq!(
            canonical_fingerprint(&json!({"b": 2, "a": 1})),
            canonical_fingerprint(&json!({"a": 1, "b": 2}))
        );
    }

    #[test]
    fn filter_key_prefers_filter_config_and_ignores_sentinels() {
        let cfg = |value: serde_json::Value| -> StrategyConfig {
            serde_json::from_value(value).unwrap()
        };
        // Folder key via filter_config.
        assert_eq!(
            cfg(json!({"index": "NIFTY", "legs": [{}], "filter_config": "base_2__bull"}))
                .filter_key(),
            Some("base_2__bull")
        );
        // Legacy super_trend_config fallback.
        assert_eq!(
            cfg(json!({"index": "NIFTY", "legs": [{}], "super_trend_config": "5x1"})).filter_key(),
            Some("5x1")
        );
        // filter_config wins over super_trend_config.
        assert_eq!(
            cfg(json!({"index": "NIFTY", "legs": [{}], "filter_config": "folder", "super_trend_config": "5x1"}))
                .filter_key(),
            Some("folder")
        );
        // Disabled/custom sentinels are not resolvable keys.
        for sentinel in ["none", "None", "custom", ""] {
            assert_eq!(
                cfg(json!({"index": "NIFTY", "legs": [{}], "filter_config": sentinel})).filter_key(),
                None
            );
        }
    }

    #[test]
    fn rejects_exit_dte_after_entry_before_execution() {
        let strategy: StrategyConfig = serde_json::from_value(json!({
            "index": "NIFTY",
            "entry_dte": 1,
            "exit_dte": 2,
            "legs": [{"position": "SELL"}]
        }))
        .unwrap();
        let error = strategy.validate().unwrap_err().to_string();
        assert!(error.contains("exit_dte (2) cannot exceed entry_dte (1)"));
    }
}
