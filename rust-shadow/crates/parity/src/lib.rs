use algotest_engine::{SummaryMetrics, TradeRow};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum DifferenceKind {
    RowCount,
    TradeField,
    SummaryField,
    MissingField,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Difference {
    pub kind: DifferenceKind,
    pub path: String,
    pub expected: serde_json::Value,
    pub actual: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ParityReport {
    pub clean: bool,
    pub differences: Vec<Difference>,
}

pub fn compare_trades(expected: &[TradeRow], actual: &[TradeRow]) -> ParityReport {
    compare_json("trades", expected, actual)
}

pub fn compare_summary(expected: &SummaryMetrics, actual: &SummaryMetrics) -> ParityReport {
    compare_json("summary", expected, actual)
}

fn compare_json<T: Serialize + ?Sized>(root: &str, expected: &T, actual: &T) -> ParityReport {
    let expected = serde_json::to_value(expected).expect("serializable expected parity value");
    let actual = serde_json::to_value(actual).expect("serializable actual parity value");
    let mut differences = Vec::new();
    walk(root, &expected, &actual, &mut differences);
    ParityReport {
        clean: differences.is_empty(),
        differences,
    }
}

fn walk(
    path: &str,
    expected: &serde_json::Value,
    actual: &serde_json::Value,
    out: &mut Vec<Difference>,
) {
    use serde_json::Value;
    match (expected, actual) {
        (Value::Object(a), Value::Object(b)) => {
            let mut keys: Vec<_> = a.keys().chain(b.keys()).collect();
            keys.sort();
            keys.dedup();
            for key in keys {
                let p = format!("{path}.{key}");
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => walk(&p, x, y, out),
                    (x, y) => out.push(Difference {
                        kind: DifferenceKind::MissingField,
                        path: p,
                        expected: x.cloned().unwrap_or(Value::Null),
                        actual: y.cloned().unwrap_or(Value::Null),
                    }),
                }
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                out.push(Difference {
                    kind: DifferenceKind::RowCount,
                    path: path.into(),
                    expected: Value::from(a.len()),
                    actual: Value::from(b.len()),
                });
            }
            for (i, (x, y)) in a.iter().zip(b).enumerate() {
                walk(&format!("{path}[{i}]"), x, y, out);
            }
        }
        _ if expected != actual => out.push(Difference {
            kind: if path.starts_with("summary") {
                DifferenceKind::SummaryField
            } else {
                DifferenceKind::TradeField
            },
            path: path.into(),
            expected: expected.clone(),
            actual: actual.clone(),
        }),
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reports_exact_summary_path() {
        let a = SummaryMetrics {
            total_pnl: 10.0,
            ..Default::default()
        };
        let b = SummaryMetrics {
            total_pnl: 11.0,
            ..Default::default()
        };
        let report = compare_summary(&a, &b);
        assert!(!report.clean);
        assert_eq!(report.differences[0].path, "summary.total_pnl");
    }
}
