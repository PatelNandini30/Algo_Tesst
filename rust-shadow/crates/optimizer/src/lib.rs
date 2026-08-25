use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use algotest_domain::{canonical_fingerprint, set_json_path, ComboOverride, StrategyConfig};
use algotest_engine::{EngineError, StrategyEngine, SummaryMetrics};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum ParameterSpec {
    Range {
        path: String,
        min: f64,
        max: f64,
        step: f64,
    },
    Values {
        path: String,
        values: Vec<Value>,
    },
    Enum {
        path: String,
        values: Vec<Value>,
    },
}

impl ParameterSpec {
    pub fn path(&self) -> &str {
        match self {
            Self::Range { path, .. } | Self::Values { path, .. } | Self::Enum { path, .. } => path,
        }
    }

    pub fn values(&self) -> Result<Vec<Value>, OptimizerError> {
        match self {
            Self::Range {
                min,
                max,
                step,
                path,
            } => {
                if !min.is_finite()
                    || !max.is_finite()
                    || !step.is_finite()
                    || *step <= 0.0
                    || max < min
                {
                    return Err(OptimizerError::InvalidParameter(format!(
                        "invalid range for {path}"
                    )));
                }
                let count = (((max - min) / step) + 1e-9).floor() as usize + 1;
                let integers = min.fract() == 0.0 && max.fract() == 0.0 && step.fract() == 0.0;
                Ok((0..count)
                    .map(|i| {
                        let value = min + *step * i as f64;
                        if integers {
                            Value::from(value.round() as i64)
                        } else {
                            Value::from((value * 1_000_000.0).round() / 1_000_000.0)
                        }
                    })
                    .collect())
            }
            Self::Values { values, path } | Self::Enum { values, path } => {
                if values.is_empty() {
                    Err(OptimizerError::InvalidParameter(format!(
                        "values must not be empty for {path}"
                    )))
                } else {
                    Ok(values.clone())
                }
            }
        }
    }
}

#[derive(Debug, Error)]
pub enum OptimizerError {
    #[error("invalid parameter: {0}")]
    InvalidParameter(String),
    #[error("combination space overflow")]
    CombinationOverflow,
    #[error("strategy serialization failed: {0}")]
    Serialization(String),
    #[error("combination limit exceeded: planned {planned}, configured maximum {maximum}")]
    CombinationLimitExceeded { planned: u64, maximum: u64 },
    #[error("memory budget exceeded: requested {requested} bytes, budget {budget} bytes")]
    MemoryBudgetExceeded { requested: usize, budget: usize },
}

/// Hard admission limits. They prevent a request from allocating an unbounded
/// Cartesian product. Large jobs must use `run_optimization_streaming`, which
/// retains at most one chunk of combinations and results.
#[derive(Debug, Clone, Copy)]
pub struct BatchLimits {
    pub max_combinations: u64,
    pub chunk_size: usize,
    pub memory_budget_bytes: usize,
    pub estimated_bytes_per_combo: usize,
}

impl Default for BatchLimits {
    fn default() -> Self {
        Self {
            max_combinations: 1_000_000,
            chunk_size: 256,
            memory_budget_bytes: 512 * 1024 * 1024,
            estimated_bytes_per_combo: 16 * 1024,
        }
    }
}

impl BatchLimits {
    pub fn validate(self) -> Result<Self, OptimizerError> {
        if self.max_combinations == 0 || self.chunk_size == 0 {
            return Err(OptimizerError::InvalidParameter(
                "max_combinations and chunk_size must be greater than zero".into(),
            ));
        }
        let requested = self
            .chunk_size
            .checked_mul(self.estimated_bytes_per_combo)
            .ok_or(OptimizerError::CombinationOverflow)?;
        if requested > self.memory_budget_bytes {
            return Err(OptimizerError::MemoryBudgetExceeded {
                requested,
                budget: self.memory_budget_bytes,
            });
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ComboResult {
    pub combo_id: u64,
    pub fingerprint: String,
    #[serde(default)]
    pub parameter_values: BTreeMap<String, Value>,
    pub objective_value: Option<f64>,
    pub trade_count: u64,
    pub summary: Option<SummaryMetrics>,
    pub error: Option<String>,
}

impl ComboResult {
    pub fn is_success(&self) -> bool {
        self.error.is_none() && self.summary.is_some()
    }
}

pub fn raw_combination_count(specs: &[ParameterSpec]) -> Result<u64, OptimizerError> {
    specs.iter().try_fold(1u64, |total, spec| {
        let n = spec.values()?.len() as u64;
        total
            .checked_mul(n)
            .ok_or(OptimizerError::CombinationOverflow)
    })
}

pub struct CombinationStream {
    axes: Vec<(String, Vec<Value>)>,
    indexes: Vec<usize>,
    started: bool,
    finished: bool,
    emitted: u64,
}

/// Uniform sampling over the raw Cartesian grid without materializing it.
/// Only `sample_n` integer indexes are retained, so memory is O(sample_n), not
/// O(product size). A supplied seed makes the sample reproducible.
pub struct RandomCombinationStream {
    axes: Vec<(String, Vec<Value>)>,
    selected: std::vec::IntoIter<u64>,
    emitted: u64,
}

impl RandomCombinationStream {
    pub fn new(
        specs: &[ParameterSpec],
        sample_n: u64,
        seed: u64,
        maximum_sample: u64,
    ) -> Result<Self, OptimizerError> {
        if sample_n == 0 {
            return Err(OptimizerError::InvalidParameter(
                "random sample_n must be greater than zero".into(),
            ));
        }
        let total = raw_combination_count(specs)?;
        let wanted = sample_n.min(total);
        if wanted > maximum_sample {
            return Err(OptimizerError::CombinationLimitExceeded {
                planned: wanted,
                maximum: maximum_sample,
            });
        }
        let axes = specs
            .iter()
            .map(|spec| Ok((spec.path().to_string(), spec.values()?)))
            .collect::<Result<Vec<_>, OptimizerError>>()?;
        let mut rng = SplitMix64(seed);
        let mut chosen = HashSet::with_capacity(wanted as usize);
        let mut selected = Vec::with_capacity(wanted as usize);
        // Floyd's algorithm samples without replacement in O(sample_n).
        for upper in total - wanted..total {
            let candidate = rng.below(upper + 1);
            let picked = if chosen.insert(candidate) {
                candidate
            } else {
                chosen.insert(upper);
                upper
            };
            selected.push(picked);
        }
        Ok(Self {
            axes,
            selected: selected.into_iter(),
            emitted: 0,
        })
    }
}

impl Iterator for RandomCombinationStream {
    type Item = ComboOverride;

    fn next(&mut self) -> Option<Self::Item> {
        let mut linear = self.selected.next()?;
        let mut values = BTreeMap::new();
        for (path, choices) in self.axes.iter().rev() {
            let radix = choices.len() as u64;
            let index = (linear % radix) as usize;
            linear /= radix;
            values.insert(path.clone(), choices[index].clone());
        }
        self.emitted += 1;
        Some(ComboOverride {
            combo_id: self.emitted,
            values,
        })
    }
}

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut value = self.0;
        value = (value ^ (value >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        value ^ (value >> 31)
    }

    fn below(&mut self, upper_exclusive: u64) -> u64 {
        if upper_exclusive <= 1 {
            0
        } else {
            self.next() % upper_exclusive
        }
    }
}

/// Bounded, dependency-free discrete adaptive search. It keeps only the best
/// coordinate vector and the last candidate; objective feedback is supplied
/// with `tell`, so the search remains entirely inside Rust.
pub struct SmartCombinationSearch {
    axes: Vec<(String, Vec<Value>)>,
    algorithm: String,
    budget: u64,
    emitted: u64,
    rng: SplitMix64,
    best: Option<(Vec<usize>, f64)>,
    pending: Option<Vec<usize>>,
}

impl SmartCombinationSearch {
    pub fn new(
        specs: &[ParameterSpec],
        algorithm: &str,
        budget: u64,
        seed: u64,
        maximum: u64,
    ) -> Result<Self, OptimizerError> {
        if budget == 0 {
            return Err(OptimizerError::InvalidParameter(
                "smart budget must be greater than zero".into(),
            ));
        }
        if budget > maximum {
            return Err(OptimizerError::CombinationLimitExceeded {
                planned: budget,
                maximum,
            });
        }
        let algorithm = match algorithm.trim().to_ascii_lowercase().as_str() {
            "cma-es" | "cmaes" => "cma-es",
            "pso" => "pso",
            "ga" | "de" => "ga",
            other => {
                return Err(OptimizerError::InvalidParameter(format!(
                    "unknown smart algorithm {other}"
                )))
            }
        }
        .to_string();
        let axes = specs
            .iter()
            .map(|spec| Ok((spec.path().to_string(), spec.values()?)))
            .collect::<Result<Vec<_>, OptimizerError>>()?;
        Ok(Self {
            axes,
            algorithm,
            budget,
            emitted: 0,
            rng: SplitMix64(seed),
            best: None,
            pending: None,
        })
    }

    pub fn ask(&mut self) -> Option<ComboOverride> {
        if self.emitted >= self.budget {
            return None;
        }
        let mut indexes = self
            .best
            .as_ref()
            .map(|(v, _)| v.clone())
            .unwrap_or_else(|| {
                self.axes
                    .iter()
                    .map(|(_, values)| self.rng.below(values.len() as u64) as usize)
                    .collect()
            });
        if self.best.is_some() && !indexes.is_empty() {
            match self.algorithm.as_str() {
                "pso" => {
                    for (axis, (_, values)) in indexes.iter_mut().zip(&self.axes) {
                        if self.rng.below(2) == 0 {
                            *axis = self.rng.below(values.len() as u64) as usize;
                        }
                    }
                }
                "ga" => {
                    let mutations = (indexes.len() / 3).max(1);
                    for _ in 0..mutations {
                        let axis = self.rng.below(indexes.len() as u64) as usize;
                        indexes[axis] = self.rng.below(self.axes[axis].1.len() as u64) as usize;
                    }
                }
                _ => {
                    let axis = self.rng.below(indexes.len() as u64) as usize;
                    let width = self.axes[axis].1.len();
                    if width > 1 {
                        indexes[axis] = if self.rng.below(2) == 0 {
                            indexes[axis].saturating_sub(1)
                        } else {
                            (indexes[axis] + 1).min(width - 1)
                        };
                    }
                }
            }
        }
        self.emitted += 1;
        self.pending = Some(indexes.clone());
        Some(ComboOverride {
            combo_id: self.emitted,
            values: self
                .axes
                .iter()
                .zip(indexes)
                .map(|((path, values), index)| (path.clone(), values[index].clone()))
                .collect(),
        })
    }

    pub fn tell(&mut self, score: f64) -> Result<(), OptimizerError> {
        let pending = self.pending.take().ok_or_else(|| {
            OptimizerError::InvalidParameter("smart tell called without ask".into())
        })?;
        if score.is_finite()
            && self
                .best
                .as_ref()
                .is_none_or(|(_, best_score)| score > *best_score)
        {
            self.best = Some((pending, score));
        }
        Ok(())
    }
}

impl CombinationStream {
    pub fn new(specs: &[ParameterSpec], maximum: u64) -> Result<Self, OptimizerError> {
        let planned = raw_combination_count(specs)?;
        if planned > maximum {
            return Err(OptimizerError::CombinationLimitExceeded { planned, maximum });
        }
        let axes = specs
            .iter()
            .map(|spec| Ok((spec.path().to_string(), spec.values()?)))
            .collect::<Result<Vec<_>, OptimizerError>>()?;
        Ok(Self {
            indexes: vec![0; axes.len()],
            axes,
            started: false,
            finished: false,
            emitted: 0,
        })
    }

    fn advance(&mut self) {
        if self.axes.is_empty() {
            self.finished = true;
            return;
        }
        for axis in (0..self.indexes.len()).rev() {
            self.indexes[axis] += 1;
            if self.indexes[axis] < self.axes[axis].1.len() {
                return;
            }
            self.indexes[axis] = 0;
        }
        self.finished = true;
    }

    fn current(&mut self) -> Option<ComboOverride> {
        let mut values = self
            .axes
            .iter()
            .zip(&self.indexes)
            .map(|((path, choices), index)| (path.clone(), choices[*index].clone()))
            .collect::<BTreeMap<_, _>>();

        if values
            .get("spot_adjustment_enabled")
            .is_some_and(|v| !json_truthy(v))
        {
            // Dependent axes collapse when disabled. Yield only the first raw
            // representation, avoiding an unbounded de-duplication HashSet.
            let duplicate = self
                .axes
                .iter()
                .zip(&self.indexes)
                .any(|((path, _), index)| is_spot_adjustment_dependent(path) && *index != 0);
            if duplicate {
                return None;
            }
            values.retain(|path, _| !is_spot_adjustment_dependent(path));
        }
        self.emitted += 1;
        Some(ComboOverride {
            combo_id: self.emitted,
            values,
        })
    }
}

impl Iterator for CombinationStream {
    type Item = ComboOverride;

    fn next(&mut self) -> Option<Self::Item> {
        while !self.finished {
            if self.started {
                self.advance();
            } else {
                self.started = true;
            }
            if self.finished {
                return None;
            }
            if let Some(combo) = self.current() {
                return Some(combo);
            }
        }
        None
    }
}

fn is_spot_adjustment_dependent(path: &str) -> bool {
    matches!(
        path,
        "spot_adjustment_pct" | "spot_adjustment_direction" | "spot_adjustment_value"
    )
}

pub fn expand_combinations(specs: &[ParameterSpec]) -> Result<Vec<ComboOverride>, OptimizerError> {
    CombinationStream::new(specs, BatchLimits::default().max_combinations).map(Iterator::collect)
}

pub fn effective_combination_count(
    specs: &[ParameterSpec],
    maximum: u64,
) -> Result<u64, OptimizerError> {
    Ok(CombinationStream::new(specs, maximum)?.count() as u64)
}

fn json_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(v) => *v,
        Value::Number(n) => n.as_f64().is_some_and(|v| v != 0.0),
        Value::String(v) => !v.is_empty(), // live param-expander uses raw Python truthiness
        Value::Array(v) => !v.is_empty(),
        Value::Object(v) => !v.is_empty(),
    }
}

pub fn effective_strategy(
    base: &StrategyConfig,
    combo: &ComboOverride,
) -> Result<(StrategyConfig, String), OptimizerError> {
    let mut json =
        serde_json::to_value(base).map_err(|e| OptimizerError::Serialization(e.to_string()))?;
    for (path, value) in &combo.values {
        set_json_path(&mut json, path, value.clone())
            .map_err(|e| OptimizerError::InvalidParameter(e.to_string()))?;
    }
    let fingerprint = canonical_fingerprint(&json);
    let strategy =
        serde_json::from_value(json).map_err(|e| OptimizerError::Serialization(e.to_string()))?;
    Ok((strategy, fingerprint))
}

pub fn run_optimization_batch(
    engine: Arc<dyn StrategyEngine>,
    base: &StrategyConfig,
    combinations: &[ComboOverride],
    objective: &str,
) -> Vec<ComboResult> {
    combinations
        .par_iter()
        .map(|combo| {
            let (strategy, fingerprint) = match effective_strategy(base, combo) {
                Ok(value) => value,
                Err(error) => return failed(combo, String::new(), error.to_string()),
            };
            if let Err(error) = strategy
                .validate()
                .map_err(|e| EngineError::InvalidStrategy(e.to_string()))
                .and_then(|_| engine.validate(&strategy))
            {
                return failed(combo, fingerprint, error.to_string());
            }
            match engine.run(&strategy, combo) {
                Ok(result) => {
                    let objective_value = objective_value(&result.summary, objective);
                    ComboResult {
                        combo_id: combo.combo_id,
                        fingerprint,
                        parameter_values: combo.values.clone(),
                        objective_value,
                        trade_count: result.summary.count,
                        summary: Some(result.summary),
                        error: objective_value
                            .is_none()
                            .then(|| format!("unknown objective: {objective}")),
                    }
                }
                Err(error) => failed(combo, fingerprint, error.to_string()),
            }
        })
        .collect()
}

/// Runs the Cartesian product in bounded chunks and hands each completed chunk
/// to the caller immediately. The sink can persist summaries, update progress,
/// or compare parity without retaining the whole job in RAM.
pub fn run_optimization_streaming<F>(
    engine: Arc<dyn StrategyEngine>,
    base: &StrategyConfig,
    specs: &[ParameterSpec],
    objective: &str,
    limits: BatchLimits,
    sink: F,
) -> Result<u64, OptimizerError>
where
    F: FnMut(&[ComboResult]) -> Result<(), OptimizerError>,
{
    let limits = limits.validate()?;
    let stream = CombinationStream::new(specs, limits.max_combinations)?;
    run_optimization_iterator_streaming(engine, base, stream, objective, limits, sink)
}

pub fn run_optimization_iterator_streaming<I, F>(
    engine: Arc<dyn StrategyEngine>,
    base: &StrategyConfig,
    mut combinations: I,
    objective: &str,
    limits: BatchLimits,
    mut sink: F,
) -> Result<u64, OptimizerError>
where
    I: Iterator<Item = ComboOverride>,
    F: FnMut(&[ComboResult]) -> Result<(), OptimizerError>,
{
    let limits = limits.validate()?;
    let mut processed = 0u64;
    loop {
        let chunk: Vec<_> = combinations.by_ref().take(limits.chunk_size).collect();
        if chunk.is_empty() {
            break;
        }
        let results = run_optimization_batch(engine.clone(), base, &chunk, objective);
        processed = processed
            .checked_add(results.len() as u64)
            .ok_or(OptimizerError::CombinationOverflow)?;
        sink(&results)?;
    }
    Ok(processed)
}

#[allow(clippy::too_many_arguments)]
pub fn run_smart_optimization<F>(
    engine: Arc<dyn StrategyEngine>,
    base: &StrategyConfig,
    specs: &[ParameterSpec],
    objective: &str,
    algorithm: &str,
    budget: u64,
    seed: u64,
    limits: BatchLimits,
    mut sink: F,
) -> Result<u64, OptimizerError>
where
    F: FnMut(&[ComboResult]) -> Result<(), OptimizerError>,
{
    let limits = limits.validate()?;
    let mut search =
        SmartCombinationSearch::new(specs, algorithm, budget, seed, limits.max_combinations)?;
    let mut processed = 0u64;
    while let Some(combo) = search.ask() {
        let results = run_optimization_batch(engine.clone(), base, &[combo], objective);
        // Invalid suggestions are scored as worst-possible and still sent to
        // the bounded sink. This lets the caller report every failed smart
        // suggestion without allowing one invalid point to hide later ones.
        let score = results[0].objective_value.unwrap_or(f64::NEG_INFINITY);
        search.tell(score)?;
        sink(&results)?;
        processed += 1;
    }
    Ok(processed)
}

fn failed(combo: &ComboOverride, fingerprint: String, error: String) -> ComboResult {
    ComboResult {
        combo_id: combo.combo_id,
        fingerprint,
        parameter_values: combo.values.clone(),
        objective_value: None,
        trade_count: 0,
        summary: None,
        error: Some(error),
    }
}

fn objective_value(summary: &SummaryMetrics, objective: &str) -> Option<f64> {
    match objective {
        "total_pnl" => Some(summary.total_pnl),
        "avg_profit_per_trade" => Some(summary.average_pnl),
        "win_pct" => Some(summary.win_pct),
        "max_dd_pct" => Some(summary.max_dd_pct),
        "cagr_options" => Some(summary.cagr_options),
        "car_mdd" => Some(summary.car_mdd),
        other => summary.extra.get(other).copied(),
    }
}

pub fn batch_is_complete(results: &[ComboResult], planned: usize) -> bool {
    results.len() == planned && results.iter().all(ComboResult::is_success)
}

#[cfg(test)]
mod tests {
    use super::*;
    use algotest_engine::{EngineResult, StrategyEngine, SummaryMetrics};
    use serde_json::json;

    struct FakeEngine;
    impl StrategyEngine for FakeEngine {
        fn validate(&self, _: &StrategyConfig) -> Result<(), EngineError> {
            Ok(())
        }
        fn run(
            &self,
            _: &StrategyConfig,
            combo: &ComboOverride,
        ) -> Result<EngineResult, EngineError> {
            Ok(EngineResult {
                trades: vec![],
                summary: SummaryMetrics {
                    count: 1,
                    total_pnl: combo.combo_id as f64,
                    ..Default::default()
                },
            })
        }
    }

    fn base() -> StrategyConfig {
        serde_json::from_value(
            json!({"index":"NIFTY","legs":[{"position":"SELL","option_type":"CE"}]}),
        )
        .unwrap()
    }

    #[test]
    fn expansion_order_matches_cartesian_input_order() {
        let specs = vec![
            ParameterSpec::Values {
                path: "entry_dte".into(),
                values: vec![json!(1), json!(2)],
            },
            ParameterSpec::Enum {
                path: "legs[0].expiry".into(),
                values: vec![json!("WEEKLY"), json!("MONTHLY")],
            },
        ];
        let c = expand_combinations(&specs).unwrap();
        assert_eq!(c.len(), 4);
        assert_eq!(c[0].values["entry_dte"], 1);
        assert_eq!(c[1].values["legs[0].expiry"], "MONTHLY");
        assert_eq!(c[2].values["entry_dte"], 2);
    }

    #[test]
    fn no_partial_batch_is_complete() {
        let combos = vec![ComboOverride {
            combo_id: 1,
            values: BTreeMap::new(),
        }];
        let results = run_optimization_batch(Arc::new(FakeEngine), &base(), &combos, "total_pnl");
        assert!(batch_is_complete(&results, 1));
        assert!(!batch_is_complete(&results, 2));
    }

    #[test]
    fn failed_combo_keeps_its_parameter_values_for_diagnostics() {
        let combo = ComboOverride {
            combo_id: 9,
            values: BTreeMap::from([
                ("entry_dte".into(), json!(0)),
                ("exit_dte".into(), json!(1)),
            ]),
        };
        let results =
            run_optimization_batch(Arc::new(FakeEngine), &base(), &[combo], "total_pnl");
        assert_eq!(results[0].parameter_values["entry_dte"], 0);
        assert!(results[0].error.is_some());
    }

    #[test]
    fn disabled_gate_collapses_without_a_seen_set() {
        let specs = vec![
            ParameterSpec::Values {
                path: "spot_adjustment_enabled".into(),
                values: vec![json!(false), json!(true)],
            },
            ParameterSpec::Values {
                path: "spot_adjustment_pct".into(),
                values: vec![json!(1), json!(2), json!(3)],
            },
        ];
        let combinations = expand_combinations(&specs).unwrap();
        assert_eq!(combinations.len(), 4);
        assert!(!combinations[0].values.contains_key("spot_adjustment_pct"));
    }

    #[test]
    fn streaming_never_retains_more_than_one_chunk() {
        let specs = vec![ParameterSpec::Range {
            path: "entry_dte".into(),
            min: 1.0,
            max: 30.0,
            step: 1.0,
        }];
        let mut largest = 0;
        let processed = run_optimization_streaming(
            Arc::new(FakeEngine),
            &base(),
            &specs,
            "total_pnl",
            BatchLimits {
                chunk_size: 7,
                ..Default::default()
            },
            |results| {
                largest = largest.max(results.len());
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(processed, 30);
        assert_eq!(largest, 7);
    }

    #[test]
    fn rejects_chunk_above_memory_budget_before_work() {
        let error = BatchLimits {
            chunk_size: 100,
            memory_budget_bytes: 99,
            estimated_bytes_per_combo: 1,
            ..Default::default()
        }
        .validate()
        .unwrap_err();
        assert!(matches!(error, OptimizerError::MemoryBudgetExceeded { .. }));
    }

    #[test]
    fn random_sampling_is_unique_bounded_and_reproducible() {
        let specs = vec![
            ParameterSpec::Range {
                path: "entry_dte".into(),
                min: 1.0,
                max: 100.0,
                step: 1.0,
            },
            ParameterSpec::Values {
                path: "exit_dte".into(),
                values: vec![json!(0), json!(1), json!(2)],
            },
        ];
        let first = RandomCombinationStream::new(&specs, 50, 42, 100)
            .unwrap()
            .collect::<Vec<_>>();
        let second = RandomCombinationStream::new(&specs, 50, 42, 100)
            .unwrap()
            .collect::<Vec<_>>();
        assert_eq!(first, second);
        assert_eq!(first.len(), 50);
        assert_eq!(
            first
                .iter()
                .map(|combo| serde_json::to_string(&combo.values).unwrap())
                .collect::<HashSet<_>>()
                .len(),
            50
        );
    }

    #[test]
    fn smart_search_consumes_exact_budget_with_feedback() {
        let specs = vec![ParameterSpec::Range {
            path: "entry_dte".into(),
            min: 1.0,
            max: 10.0,
            step: 1.0,
        }];
        for algorithm in ["cma-es", "pso", "ga"] {
            let mut search = SmartCombinationSearch::new(&specs, algorithm, 25, 7, 100).unwrap();
            let mut count = 0;
            while let Some(combo) = search.ask() {
                let score = combo.values["entry_dte"].as_f64().unwrap();
                search.tell(score).unwrap();
                count += 1;
            }
            assert_eq!(count, 25);
        }
    }

    #[test]
    fn smart_run_reports_every_invalid_suggestion_without_stopping_early() {
        let specs = vec![ParameterSpec::Values {
            path: "entry_dte".into(),
            values: vec![json!(0)],
        }];
        let mut invalid = base();
        invalid.exit_dte = Some(1);
        invalid.entry_dte = Some(1);
        let mut failures = 0usize;
        let processed = run_smart_optimization(
            Arc::new(FakeEngine),
            &invalid,
            &specs,
            "total_pnl",
            "cma-es",
            7,
            42,
            BatchLimits::default(),
            |results| {
                failures += results.iter().filter(|result| !result.is_success()).count();
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(processed, 7);
        assert_eq!(failures, 7);
    }
}
