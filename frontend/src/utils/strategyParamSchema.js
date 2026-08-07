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
        // Only meaningful for a pct_of_atm / atm_straddle_prem_pct leg — both
        // put the swept value in strike_selection.value.
        path: 'legs[I].strike_selection.value',
        label: 'Strike offset (pct_of_atm value)',
        unit: '%',
        strikeModes: ['PCT_OF_ATM', 'ATM_STRADDLE_PREM_PCT'],
        ...RANGE(-5, 5, 0.5),
      },
      {
        // Relative-to-Leg (Iron Condor wing): sweep the wing's offset in
        // strike-gaps from its parent leg. Only meaningful for a rel_leg leg.
        path: 'legs[I].strike_selection.offset',
        label: 'Wing offset (relative-leg gaps)',
        unit: 'gaps',
        discrete: true,
        strikeModes: ['REL_LEG'],
        ...RANGE(1, 6, 1),
      },
      {
        // ATM/ITM/OTM ladder — only for a strike_type / synthetic_future leg.
        path: 'legs[I].strike_selection.strike_type',
        label: 'Strike type (enum)',
        strikeModes: ['STRIKE_TYPE', 'SYNTHETIC_FUTURE'],
        ...ENUM([
          'ATM',
          'ITM1', 'ITM2', 'ITM3', 'ITM4', 'ITM5', 'ITM6', 'ITM7', 'ITM8', 'ITM9', 'ITM10',
          'OTM1', 'OTM2', 'OTM3', 'OTM4', 'OTM5', 'OTM6', 'OTM7', 'OTM8', 'OTM9', 'OTM10',
        ]),
      },
      {
        // Straddle Width: strike = nearest ATM ± (multiplier × ATM straddle
        // premium), snapped to strike interval. Only meaningful for a
        // straddle_width leg.
        path: 'legs[I].strike_selection.straddle_multiplier',
        label: 'Straddle Width — multiplier',
        unit: 'x straddle',
        strikeModes: ['STRADDLE_WIDTH'],
        ...RANGE(0.25, 2, 0.25),
      },
      {
        // "+" = OTM (away from ATM: up for CE, down for PE); "-" = ITM.
        path: 'legs[I].strike_selection.straddle_direction',
        label: 'Straddle Width — direction (OTM/ITM)',
        strikeModes: ['STRADDLE_WIDTH'],
        ...ENUM(['+', '-']),
      },
      {
        // Time Value: pick the strike whose (close − intrinsic) is nearest to /
        // ≥ / ≤ the target. The target shares the premium input box, so it lands
        // in strike_selection.premium — same field the engine reads.
        path: 'legs[I].strike_selection.premium',
        label: 'Time value target',
        unit: 'pts',
        strikeModes: ['TIME_VALUE', 'TIME_VALUE_GTE', 'TIME_VALUE_LTE'],
        ...RANGE(20, 200, 20),
      },
      {
        // Which side of the money the time-value search may pick from.
        path: 'legs[I].strike_selection.moneyness',
        label: 'Time value — side (OTM/ITM/ATM)',
        strikeModes: ['TIME_VALUE', 'TIME_VALUE_GTE', 'TIME_VALUE_LTE'],
        ...ENUM(['ATM', 'OTM', 'ITM']),
      },
      {
        // How far from spot the time-value walk may step: |strike/spot - 1| in %.
        path: 'legs[I].strike_selection.tv_range_pct',
        label: 'Time value — range cap',
        unit: '%',
        strikeModes: ['TIME_VALUE', 'TIME_VALUE_GTE', 'TIME_VALUE_LTE'],
        ...RANGE(1, 5, 1),
      },
      {
        // Unit the time-value target is expressed in: index points, or a share
        // of spot ((close - intrinsic) / entry_spot * 100).
        path: 'legs[I].strike_selection.tv_units',
        label: 'Time value — unit (pts / %)',
        strikeModes: ['TIME_VALUE', 'TIME_VALUE_GTE', 'TIME_VALUE_LTE'],
        ...ENUM(['points', 'percent']),
      },
      {
        // Expiry is not strike-mode specific — always available per leg.
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
      // Slippage % sweep removed — slippage is per-leg now (each leg's own
      // slippage_pct), not a strategy-level value, so there's nothing global
      // left to sweep. Every combo already inherits each leg's own slippage
      // from the base payload automatically (apply_combo_for_optim deep-
      // copies the whole payload, including legs[].slippage_pct).
    ],
  },
  {
    group: 'Global — Spot Adjustment',
    forLeg: false,
    items: [
      {
        // Threshold is swept in whichever unit the %/pts toggle on this row is
        // set to (OptimizePanel sends it as spot_adjustment_units on the base
        // payload). unitOptions drives that toggle, and unitDefaults supplies
        // the range preset each unit switches to.
        path: 'spot_adjustment_pct',
        label: 'Spot Adjustment',
        unit: '%',
        ...RANGE(0.5, 5, 0.5),
        unitPayloadPath: 'spot_adjustment_units',
        unitOptions: [
          { key: 'percent', unit: '%', ...RANGE(0.5, 5, 0.5) },
          { key: 'points', unit: 'pts', ...RANGE(50, 500, 50) },
        ],
      },
      {
        path: 'spot_adjustment_direction',
        label: 'Spot Adjustment direction',
        ...ENUM(['rise', 'fall', 'both']),
      },
      {
        path: 'spot_adjustment_enabled',
        label: 'No Adjustment',
        ...ENUM([false, true]),
        valueLabels: { false: 'No Adj', true: 'With Adj' },
      },
    ],
  },
  {
    // Per-leg ("own") spot adjustment — sweeps EACH leg's own breach threshold,
    // direction, and %/pts unit independently. Templated per leg
    // (legs[I].spot_adjustment.*) — the exact shape the engine reads
    // (engine_rust.py::_resolve_leg_sa). `optionOnly` hides it on a futures leg.
    // apply_combo_for_optim forces spot_adjustment.enabled=True on any leg the
    // sweep touches (unless the `enabled` axis is itself swept, e.g. an on/off run).
    group: 'Per-Leg — Spot Adjustment',
    forLeg: true,
    optionOnly: true,
    // Only shown for a leg whose OWN spot adjustment is already turned on in the
    // Strategy Builder (leg.spot_adjustment.enabled). You opt the leg in there,
    // then sweep its threshold / direction / unit here — mirroring how the Strike
    // group only shows the axes valid for the leg's chosen strike mode.
    requiresLegSpotAdj: true,
    items: [
      {
        path: 'legs[I].spot_adjustment.pct',
        label: 'Own Spot Adjustment',
        unit: '%',
        ...RANGE(0.5, 5, 0.5),
        unitPayloadPath: 'legs[I].spot_adjustment.units',
        unitOptions: [
          { key: 'percent', unit: '%', ...RANGE(0.5, 5, 0.5) },
          { key: 'points', unit: 'pts', ...RANGE(100, 2000, 100) },
        ],
      },
      {
        path: 'legs[I].spot_adjustment.direction',
        label: 'Own Spot Adjustment direction',
        ...ENUM(['rise', 'fall', 'both']),
      },
      {
        path: 'legs[I].spot_adjustment.enabled',
        label: 'Own Spot Adjustment on/off',
        ...ENUM([false, true]),
        valueLabels: { false: 'No Adj', true: 'With Adj' },
      },
    ],
  },
  {
    // Cross-index Midcap spot adjustment — only shown when a Midcap leg is in
    // the strategy (filtered in OptimizePanel via midcapOnly). Sweeps the
    // NIFTYMIDCAP100 breach threshold + direction, like the NIFTY one.
    group: 'Global — Midcap Spot Adjustment',
    forLeg: false,
    midcapOnly: true,
    items: [
      {
        // Same %/pts toggle as the NIFTY spot adjustment axis above.
        path: 'midcap_spot_adjustment.pct',
        label: 'Midcap Spot Adjustment',
        unit: '%',
        ...RANGE(0.5, 5, 0.5),
        unitPayloadPath: 'midcap_spot_adjustment.units',
        unitOptions: [
          { key: 'percent', unit: '%', ...RANGE(0.5, 5, 0.5) },
          { key: 'points', unit: 'pts', ...RANGE(50, 500, 50) },
        ],
      },
      {
        path: 'midcap_spot_adjustment.direction',
        label: 'Midcap Spot Adjustment direction',
        ...ENUM(['rise', 'fall', 'both']),
      },
    ],
  },
  {
    // Cross-index MIDCPNIFTY spot adjustment — only shown when the strategy
    // actually holds a MIDCPNIFTY leg (filtered in OptimizePanel via
    // midcpniftyOnly). Sweeps the MIDCPNIFTY breach threshold + direction,
    // exactly like the NIFTY and Midcap100 axes above. MIDCPNIFTY is a TRADED
    // index here (not the Midcap100 overlay), so this is gated on a real leg
    // rather than on midcap_legs.
    group: 'Global — MIDCPNIFTY Spot Adjustment',
    forLeg: false,
    midcpniftyOnly: true,
    items: [
      {
        path: 'midcpnifty_spot_adjustment.pct',
        label: 'MIDCPNIFTY Spot Adjustment',
        unit: '%',
        ...RANGE(0.5, 5, 0.5),
        unitPayloadPath: 'midcpnifty_spot_adjustment.units',
        unitOptions: [
          { key: 'percent', unit: '%', ...RANGE(0.5, 5, 0.5) },
          { key: 'points', unit: 'pts', ...RANGE(50, 500, 50) },
        ],
      },
      {
        path: 'midcpnifty_spot_adjustment.direction',
        label: 'MIDCPNIFTY Spot Adjustment direction',
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
        out.push({ ...item, group: grp.group, midcapOnly: Boolean(grp.midcapOnly),
                   midcpniftyOnly: Boolean(grp.midcpniftyOnly) });
      }
      continue;
    }
    for (let i = 0; i < nLegs; i += 1) {
      for (const item of grp.items) {
        out.push({
          ...item,
          path: item.path.replace('[I]', `[${i}]`),
          // Resolve the per-leg unit toggle path too (e.g. the %/pts toggle on the
          // per-leg spot-adjustment axis writes legs[i].spot_adjustment.units).
          ...(item.unitPayloadPath
            ? { unitPayloadPath: item.unitPayloadPath.replace('[I]', `[${i}]`) }
            : {}),
          label: `${item.label} (Leg ${i + 1})`,
          group: grp.group,
          optionOnly: Boolean(grp.optionOnly),
          requiresLegSpotAdj: Boolean(grp.requiresLegSpotAdj),
          legIndex: i,
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
  { key: 'avg_final_mae',           label: 'Avg Combined Final MAE', conditional: 'hasMidcap' },
  { key: 'avg_final_mae',           label: 'Avg Final MAE',          conditional: 'notMidcap', dup: true },
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
  // Midcap cross-index overlay (shown only when a combo has Midcap data; the
  // headline metrics above are already COMBINED in that case).
  { key: 'midcap_leg_pnl_sum',      label: 'Midcap Leg P&L',         conditional: 'hasMidcap' },
  { key: 'midcap_leg_pnl_pct_sum',  label: 'Midcap Leg P&L %',       conditional: 'hasMidcap' },
  { key: 'combined_pnl_sum',        label: 'Combined Net P&L',       conditional: 'hasMidcap' },
  { key: 'combined_pnl_pct_sum',    label: 'Combined Net P&L %',     conditional: 'hasMidcap' },
];
