import React from "react";

const STRIKE_OFFSETS = [
  { value: -5, label: "ATM-5" },
  { value: -4, label: "ATM-4" },
  { value: -3, label: "ATM-3" },
  { value: -2, label: "ATM-2" },
  { value: -1, label: "ATM-1" },
  { value:  0, label: "ATM"   },
  { value:  1, label: "ATM+1" },
  { value:  2, label: "ATM+2" },
  { value:  3, label: "ATM+3" },
  { value:  4, label: "ATM+4" },
  { value:  5, label: "ATM+5" },
];

const inputCls   = "h-8 px-2 border border-default rounded text-sm bg-surface text-primary focus:outline-none focus:border-accent";
const labelCls   = "block text-xs font-medium text-secondary mb-1";
const sectionCls = "bg-surface border border-default rounded-md p-3 mt-3";

export default function IntradayFields({
  entryTime,
  squareOffTime,
  legs,
  onEntryTimeChange,
  onSquareOffChange,
  onLegSlChange,
  onLegTargetChange,
  onLegExpiryChange,
  onLegStrikeOffsetChange,
}) {
  return (
    <div className="space-y-3">
      {/* Entry / Exit time row — strategy-level settings, side by side */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Entry Time</label>
          <input
            type="time"
            className={`${inputCls} w-full`}
            value={entryTime}
            min="09:15"
            max="15:14"
            step="60"
            onChange={(e) => onEntryTimeChange(e.target.value)}
          />
        </div>
        <div>
          <label className={labelCls}>Square-off Time</label>
          <input
            type="time"
            className={`${inputCls} w-full`}
            value={squareOffTime}
            min="09:16"
            max="15:30"
            step="60"
            onChange={(e) => onSquareOffChange(e.target.value)}
          />
        </div>
      </div>

      {/* Per-leg controls — Expiry, Strike, SL, Target */}
      {legs.map((leg, idx) => (
        <div key={idx} className={sectionCls}>
          <div className="text-xs font-semibold text-primary mb-2">
            Leg {idx + 1} <span className="text-secondary">({leg.opt_type} {leg.action})</span>
          </div>

          <div className="grid grid-cols-2 gap-3 mb-2">
            <div>
              <label className={labelCls}>Expiry</label>
              <select
                className={`${inputCls} w-full`}
                value={leg.expiry_type || "WEEKLY"}
                onChange={(e) => onLegExpiryChange && onLegExpiryChange(idx, e.target.value)}
              >
                <option value="WEEKLY">Weekly</option>
                <option value="MONTHLY">Monthly</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Strike</label>
              <select
                className={`${inputCls} w-full`}
                value={leg.strike_offset ?? 0}
                onChange={(e) => onLegStrikeOffsetChange && onLegStrikeOffsetChange(idx, parseInt(e.target.value, 10))}
              >
                {STRIKE_OFFSETS.map(({ value, label }) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>Stop Loss</label>
              <div className="flex gap-1">
                <select
                  className={`${inputCls} flex-1`}
                  value={leg.sl ? leg.sl.type : "none"}
                  onChange={(e) => {
                    if (e.target.value === "none") onLegSlChange(idx, null);
                    else onLegSlChange(idx, { type: e.target.value, value: leg.sl?.value ?? 50 });
                  }}
                >
                  <option value="none">None</option>
                  <option value="percent">% of premium</option>
                  <option value="points">Points</option>
                </select>
                {leg.sl && (
                  <input
                    type="number" min="0" step="1"
                    className={`${inputCls} w-20`}
                    value={leg.sl.value}
                    onChange={(e) => onLegSlChange(idx, { ...leg.sl, value: parseFloat(e.target.value) })}
                  />
                )}
              </div>
            </div>
            <div>
              <label className={labelCls}>Target</label>
              <div className="flex gap-1">
                <select
                  className={`${inputCls} flex-1`}
                  value={leg.target ? leg.target.type : "none"}
                  onChange={(e) => {
                    if (e.target.value === "none") onLegTargetChange(idx, null);
                    else onLegTargetChange(idx, { type: e.target.value, value: leg.target?.value ?? 50 });
                  }}
                >
                  <option value="none">None</option>
                  <option value="percent">% of premium</option>
                  <option value="points">Points</option>
                </select>
                {leg.target && (
                  <input
                    type="number" min="0" step="1"
                    className={`${inputCls} w-20`}
                    value={leg.target.value}
                    onChange={(e) => onLegTargetChange(idx, { ...leg.target, value: parseFloat(e.target.value) })}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
