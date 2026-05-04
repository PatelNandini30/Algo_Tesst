import React from "react";

/**
 * Intraday-specific strategy fields.
 * Props:
 *   entryTime            string  e.g. "09:20"
 *   squareOffTime        string  e.g. "15:15"
 *   legs                 Array   leg objects from parent state
 *   onEntryTimeChange    fn(value)
 *   onSquareOffChange    fn(value)
 *   onLegSlChange        fn(legIdx, slObject | null)
 *   onLegTargetChange    fn(legIdx, targetObject | null)
 */
export default function IntradayFields({
  entryTime,
  squareOffTime,
  legs,
  onEntryTimeChange,
  onSquareOffChange,
  onLegSlChange,
  onLegTargetChange,
}) {
  return (
    <div className="intraday-fields">
      <div className="field-row">
        <label>Entry time</label>
        <input
          type="time"
          value={entryTime}
          min="09:15"
          max="15:14"
          step="60"
          onChange={(e) => onEntryTimeChange(e.target.value)}
        />
      </div>
      <div className="field-row">
        <label>Square-off time</label>
        <input
          type="time"
          value={squareOffTime}
          min="09:16"
          max="15:30"
          step="60"
          onChange={(e) => onSquareOffChange(e.target.value)}
        />
      </div>

      {legs.map((leg, idx) => (
        <div key={idx} className="leg-exit-fields">
          <span className="leg-label">
            Leg {idx + 1} ({leg.opt_type} {leg.action})
          </span>
          <div className="field-row">
            <label>SL type</label>
            <select
              value={leg.sl ? leg.sl.type : "none"}
              onChange={(e) => {
                if (e.target.value === "none") {
                  onLegSlChange(idx, null);
                } else {
                  onLegSlChange(idx, { type: e.target.value, value: leg.sl?.value ?? 50 });
                }
              }}
            >
              <option value="none">None</option>
              <option value="percent">% of premium</option>
              <option value="points">Points</option>
            </select>
            {leg.sl && (
              <input
                type="number"
                min="0"
                step="1"
                value={leg.sl.value}
                onChange={(e) =>
                  onLegSlChange(idx, { ...leg.sl, value: parseFloat(e.target.value) })
                }
              />
            )}
          </div>
          <div className="field-row">
            <label>Target type</label>
            <select
              value={leg.target ? leg.target.type : "none"}
              onChange={(e) => {
                if (e.target.value === "none") {
                  onLegTargetChange(idx, null);
                } else {
                  onLegTargetChange(idx, {
                    type: e.target.value,
                    value: leg.target?.value ?? 50,
                  });
                }
              }}
            >
              <option value="none">None</option>
              <option value="percent">% of premium</option>
              <option value="points">Points</option>
            </select>
            {leg.target && (
              <input
                type="number"
                min="0"
                step="1"
                value={leg.target.value}
                onChange={(e) =>
                  onLegTargetChange(idx, { ...leg.target, value: parseFloat(e.target.value) })
                }
              />
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
