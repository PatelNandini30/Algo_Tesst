/**
 * Single source of truth for which strategy payload fields are optimizable.
 *
 * Each entry:
 *   path           — JSON path used by the backend param_expander
 *                    (e.g. "legs[0].stopLoss.value", "entry_dte")
 *   label          — UI label
 *   group          — sectioning in the picker tree
 *   kind           — 'range' | 'enum'
 *   default        — default range / value list (used to pre-fill the editor)
 *   unit           — display unit (e.g. '%', 'days', 'pts') — UI hint only
 *   forLeg         — when true, the path is templated per leg; `path` contains
 *                    a literal `[I]` placeholder that the UI substitutes with
 *                    the leg index.
 *   discrete       — if true, force integer steps
 *
 * The optimizer backend accepts any nested path so this list can be extended
 * without backend changes. To start, we expose the parameters the research
 * team identified as primary optim targets.
 */

const RANGE = (min, max, step) => ({ kind: 'range', min, max, step });
const ENUM = (values) => ({ kind: 'enum', values });

export const OPTIM_PARAM_GROUPS = [
  {
    group: 'Per-Leg — Risk',
    forLeg: true,
    items: [
      {
        path: 'legs[I].stopLoss.value',
        label: 'Stop Loss value',
        unit: '% / pts',
        ...RANGE(10, 100, 10),
      },
      {
        path: 'legs[I].targetProfit.value',
        label: 'Target Profit value',
        unit: '% / pts',
        ...RANGE(20, 200, 20),
      },
      {
        path: 'legs[I].slWithBuffer.value',
        label: 'SL-with-Buffer value',
        unit: '%',
        ...RANGE(10, 60, 10),
      },
      {
        path: 'legs[I].slWithBuffer.buffer_pct',
        label: 'SL-with-Buffer buffer %',
        unit: '%',
        ...RANGE(1, 10, 1),
      },
      {
        path: 'legs[I].trailSL.trigger',
        label: 'Trail SL — Trigger',
        unit: '%',
        ...RANGE(5, 50, 5),
      },
      {
        path: 'legs[I].trailSL.move',
        label: 'Trail SL — Move',
        unit: '%',
        ...RANGE(1, 20, 2),
      },
    ],
  },
  {
    group: 'Per-Leg — Strike',
    forLeg: true,
    items: [
      {
        path: 'legs[I].strike_selection.value',
        label: 'Strike offset (pct_of_atm value)',
        unit: '%',
        ...RANGE(-5, 5, 0.5),
      },
      {
        path: 'legs[I].strike_selection.strike_type',
        label: 'Strike type (enum)',
        ...ENUM(['ATM', 'ITM1', 'ITM2', 'ITM3', 'OTM1', 'OTM2', 'OTM3']),
      },
      {
        path: 'legs[I].expiry',
        label: 'Expiry window',
        ...ENUM(['WEEKLY', 'NEXT_WEEKLY', 'MONTHLY', 'NEXT_MONTHLY']),
      },
    ],
  },
  {
    group: 'Global — Entry / Exit',
    forLeg: false,
    items: [
      {
        path: 'entry_dte',
        label: 'Entry DTE',
        unit: 'days',
        discrete: true,
        ...RANGE(0, 10, 1),
      },
      {
        path: 'exit_dte',
        label: 'Exit DTE',
        unit: 'days',
        discrete: true,
        ...RANGE(0, 10, 1),
      },
      {
        path: 'min_days_to_entry',
        label: 'Min days to entry',
        unit: 'days',
        discrete: true,
        ...RANGE(0, 5, 1),
      },
    ],
  },
  {
    group: 'Global — Risk',
    forLeg: false,
    items: [
      {
        path: 'overall_sl_value',
        label: 'Overall SL value',
        unit: '% / pts',
        ...RANGE(1, 10, 1),
      },
      {
        path: 'overall_target_value',
        label: 'Overall Target value',
        unit: '% / pts',
        ...RANGE(1, 20, 1),
      },
      {
        path: 'slippage_pct',
        label: 'Slippage %',
        unit: '%',
        ...RANGE(0, 0.5, 0.05),
      },
    ],
  },
  {
    group: 'Global — Spot Adjustment',
    forLeg: false,
    items: [
      {
        path: 'spot_adjustment_pct',
        label: 'Spot Adjustment %',
        unit: '%',
        ...RANGE(0.5, 5, 0.5),
      },
      {
        path: 'spot_adjustment_direction',
        label: 'Spot Adjustment direction',
        ...ENUM(['rise', 'fall', 'both']),
      },
    ],
  },
  {
    group: 'Global — Buffer Strike',
    forLeg: false,
    items: [
      {
        path: 'buffer_strike_value',
        label: 'Buffer strike value',
        unit: '% / pts',
        ...RANGE(0.5, 5, 0.5),
      },
    ],
  },
];

/**
 * Resolve [I] placeholders with concrete leg indices.
 * Returns a flat list `[{ path, label, group, ...spec }]`.
 *
 * @param {number} nLegs — number of legs in the current strategy
 */
export function expandSchemaForLegs(nLegs) {
  const out = [];
  for (const grp of OPTIM_PARAM_GROUPS) {
    if (!grp.forLeg) {
      for (const item of grp.items) {
        out.push({ ...item, group: grp.group });
      }
      continue;
    }
    for (let i = 0; i < nLegs; i += 1) {
      for (const item of grp.items) {
        out.push({
          ...item,
          path: item.path.replace('[I]', `[${i}]`),
          label: `${item.label} (Leg ${i + 1})`,
          group: grp.group,
        });
      }
    }
  }
  return out;
}

/** Master-summary 37-column layout — matches Summary_of_X.xlsx. */
export const MASTER_SUMMARY_COLUMNS = [
  { key: 'sr_no',                   label: 'Sr. No.' },
  { key: 'expiry',                  label: 'Expiry' },
  { key: 'shifting',                label: 'Shifting' },
  { key: 'put_strike_label',        label: 'Put ATM or ITM' },
  { key: 'call_strike_label',       label: 'Call ATM or ITM' },
  { key: 'spot_adjustment',         label: 'Spot Adjustment' },
  { key: 'count',                   label: 'Trades Count' },
  { key: 'total_pnl',               label: 'Net P/L Sum' },
  { key: 'total_pnl_pct',           label: 'Net P/L Sum %' },
  { key: 'avg_profit_per_trade',    label: 'Net P/L Avg.' },
  { key: 'avg_profit_per_trade_pct', label: 'Net P/L Avg. %' },
  { key: 'win_pct',                 label: 'Winners %' },
  { key: 'avg_win',                 label: 'Avg. win' },
  { key: 'avg_win_pct',             label: 'Avg. win %' },
  { key: 'loss_pct',                label: 'Looser %' },
  { key: 'avg_loss',                label: 'Avg. Loss' },
  { key: 'avg_loss_pct',            label: 'Avg. Loss %' },
  { key: 'expectancy',              label: 'Expectancy' },
  { key: 'cagr_options',            label: 'CAGR(Options)' },
  { key: 'max_dd_pct',              label: 'DD %' },
  { key: 'spot_change',             label: 'Spot Change' },
  { key: 'spot_change_pct',         label: 'Spot Change %' },
  { key: 'roi_vs_spot',             label: 'ROI vs Spot' },
  { key: 'cagr_spot',               label: 'CAGR(Spot)' },
  { key: 'car_mdd',                 label: 'CAR/MDD Booked' },
  { key: 'max_dd_pct',              label: 'DD',               dup: true },
  { key: 'actual_live_dd_max',      label: 'Actual Live DD' },
  { key: 'actual_live_dd_avg',      label: 'Avg Actual Live DD' },
  { key: 'car_mdd_live',            label: 'CAR/MDD Live' },
  { key: 'positive_outlier_1',      label: '+ve Outlier 1' },
  { key: 'negative_outlier_1',      label: '-ve Outlier 1' },
  { key: 'outlier_dd_1',            label: 'Actual Live DD Without Outlier 1' },
  { key: 'outlier_dd_1_avg',        label: 'Avg Actual Live DD Without Outlier 1' },
  { key: 'positive_outlier_2',      label: '+ve Outlier 2' },
  { key: 'negative_outlier_2',      label: '-ve Outlier 2' },
  { key: 'outlier_dd_2',            label: 'Actual Live DD Without Outlier 2' },
  { key: 'outlier_dd_2_avg',        label: 'Avg Actual Live DD Without Outlier 2' },
  { key: 'positive_outlier_3',      label: '+ve Outlier 3' },
  { key: 'negative_outlier_3',      label: '-ve Outlier 3' },
  { key: 'outlier_dd_3',            label: 'Actual Live DD Without Outlier 3' },
  { key: 'outlier_dd_3_avg',        label: 'Avg Actual Live DD Without Outlier 3' },
  { key: 'ce_pe_pnl_pct_without_top_1_outliers', label: 'CE + PE + P&L % Without Top 1 Outliers' },
  { key: 'ce_pe_pnl_pct_without_top_2_outliers', label: 'CE + PE + P&L % Without Top 2 Outliers' },
  { key: 'ce_pe_pnl_pct_without_top_3_outliers', label: 'CE + PE + P&L % Without Top 3 Outliers' },
  { key: 'ce_pnl_total',            label: 'CE P&L',           conditional: 'hasCE' },
  { key: 'ce_pnl_pct',              label: 'CE P&L %',         conditional: 'hasCE' },
  { key: 'pe_pnl_total',            label: 'PE P&L',           conditional: 'hasPE' },
  { key: 'pe_pnl_pct',              label: 'PE P&L %',         conditional: 'hasPE' },
  { key: 'long_spot_pnl',           label: 'Long Spot P&L',    conditional: 'hasSpot' },
  { key: 'long_spot_pnl_pct',       label: 'Long Spot P&L %',  conditional: 'hasSpot' },
];
