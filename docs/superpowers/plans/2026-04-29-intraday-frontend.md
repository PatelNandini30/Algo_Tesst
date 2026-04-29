# Intraday Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `Intraday` mode toggle to the existing `StrategyBuilder.jsx`, show intraday-specific form fields, decode Arrow IPC tradesheet responses, surface entry/exit times in results, and show a slow-path warning banner.

**Architecture:** Mode (`eod` | `intraday`) is stored on the strategy object. The frontend calls `/api/backtest` for EOD and `/api/intraday/backtest` for intraday. Arrow IPC bytes from the intraday endpoint are decoded client-side using `apache-arrow`. The existing `ResultsPanel.jsx` gains two new columns. No existing EOD components are changed in ways that break current behaviour.

**Tech Stack:** React 18, Vite, `apache-arrow` (npm), existing `StrategyBuilder.jsx` + `ResultsPanel.jsx`.

**Prerequisite:** Plan C complete — the `/api/intraday/backtest` endpoint exists and returns Arrow IPC bytes.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `frontend/package.json` | add `apache-arrow` dependency |
| Create | `frontend/src/utils/arrowDecoder.js` | deserialise Arrow IPC → JS array of row objects |
| Modify | `frontend/src/components/StrategyBuilder.jsx` | mode toggle + intraday field section |
| Create | `frontend/src/components/IntradayFields.jsx` | entry_time, square_off_time, per-leg SL/target |
| Create | `frontend/src/components/IntradaySlowPathWarning.jsx` | yellow warning banner |
| Modify | `frontend/src/components/ResultsPanel.jsx` | entry_time / exit_time columns |
| Modify | `frontend/src/api.js` (or equivalent API client) | route to correct endpoint by mode |

---

### Task 1: Install `apache-arrow`

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Install the package**

```bash
cd /home/user/Algo_Test_Software/frontend
npm install apache-arrow@latest --save 2>&1 | tail -5
```
Expected: `apache-arrow` added to `package.json` under `dependencies`.

- [ ] **Step 2: Verify it resolves**

```bash
node -e "const a = require('apache-arrow'); console.log(a.Table ? 'ok' : 'fail')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add apache-arrow for intraday tradesheet decode"
```

---

### Task 2: Arrow IPC decoder utility

**Files:**
- Create: `frontend/src/utils/arrowDecoder.js`

- [ ] **Step 1: Create `frontend/src/utils/arrowDecoder.js`**

```js
import { tableFromIPC } from "apache-arrow";

/**
 * Decode an Arrow IPC stream (ArrayBuffer or Uint8Array) into an array of
 * plain JS row objects.  Column names match the backend _TRADESHEET_SCHEMA.
 *
 * @param {ArrayBuffer|Uint8Array} buffer
 * @returns {{ date, symbol, expiry, strike, opt_type, action,
 *             entry_time, entry_price, exit_time, exit_price,
 *             exit_reason, quantity, pnl, mae, mfe }[]}
 */
export function decodeTradesheet(buffer) {
  const table = tableFromIPC(buffer);
  const rows = [];
  for (let i = 0; i < table.numRows; i++) {
    const row = {};
    for (const field of table.schema.fields) {
      row[field.name] = table.getChild(field.name).get(i);
    }
    rows.push(row);
  }
  return rows;
}
```

- [ ] **Step 2: Verify it bundles without error**

```bash
cd /home/user/Algo_Test_Software/frontend
npx vite build --mode development 2>&1 | grep -E "error|warning|built in" | head -10
```
Expected: no errors; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/utils/arrowDecoder.js
git commit -m "feat(frontend): Arrow IPC decoder utility for intraday tradesheet"
```

---

### Task 3: Intraday-specific fields component

**Files:**
- Create: `frontend/src/components/IntradayFields.jsx`

- [ ] **Step 1: Create `frontend/src/components/IntradayFields.jsx`**

```jsx
import React from "react";

/**
 * Intraday-specific strategy fields.
 * Props:
 *   entryTime        string  e.g. "09:20"
 *   squareOffTime    string  e.g. "15:15"
 *   legs             Array   leg objects from parent state
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
          <span className="leg-label">Leg {idx + 1} ({leg.opt_type} {leg.action})</span>
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
                  onLegTargetChange(idx, { type: e.target.value, value: leg.target?.value ?? 50 });
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
```

- [ ] **Step 2: Build to verify no syntax errors**

```bash
cd /home/user/Algo_Test_Software/frontend
npx vite build --mode development 2>&1 | grep -E "^.*(error|Error)" | head -5
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/IntradayFields.jsx
git commit -m "feat(frontend): IntradayFields component for entry/sqoff/SL/target"
```

---

### Task 4: Slow-path warning banner

**Files:**
- Create: `frontend/src/components/IntradaySlowPathWarning.jsx`

- [ ] **Step 1: Create `frontend/src/components/IntradaySlowPathWarning.jsx`**

```jsx
import React from "react";

/**
 * Shows a yellow warning when the API indicates a slow-path backtest.
 * Props:
 *   visible   boolean
 *   reason    string (optional, e.g. "Strike offset > ±5 falls back to full Parquet")
 */
export default function IntradaySlowPathWarning({ visible, reason }) {
  if (!visible) return null;
  return (
    <div
      style={{
        background: "#fffbe6",
        border: "1px solid #ffe58f",
        borderRadius: 4,
        padding: "8px 12px",
        marginBottom: 12,
        color: "#614700",
        fontSize: 13,
      }}
    >
      <strong>Slow backtest:</strong>{" "}
      {reason || "This strategy uses far-OTM strikes; backtest may take 20–60 seconds."}
    </div>
  );
}
```

- [ ] **Step 2: Build**

```bash
cd /home/user/Algo_Test_Software/frontend
npx vite build --mode development 2>&1 | grep -i error | head -5
```
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/IntradaySlowPathWarning.jsx
git commit -m "feat(frontend): slow-path warning banner for far-OTM strategies"
```

---

### Task 5: Wire mode toggle into `StrategyBuilder.jsx` and update API call

**Files:**
- Modify: `frontend/src/components/StrategyBuilder.jsx`
- Modify: `frontend/src/api.js` (or wherever the backtest API call lives — check with `grep -r "api/backtest" frontend/src/`)

- [ ] **Step 1: Find the API call**

```bash
grep -rn "api/backtest\|fetch.*backtest\|axios.*backtest" /home/user/Algo_Test_Software/frontend/src/ | head -10
```
Note the file and line number.

- [ ] **Step 2: Read the top of `StrategyBuilder.jsx` to understand current state shape**

```bash
head -80 /home/user/Algo_Test_Software/frontend/src/components/StrategyBuilder.jsx
```

- [ ] **Step 3: Add mode toggle state to `StrategyBuilder.jsx`**

Inside the component, add mode state near the top of existing state declarations:
```jsx
const [mode, setMode] = React.useState("eod"); // "eod" | "intraday"
const [entryTime, setEntryTime] = React.useState("09:20");
const [squareOffTime, setSquareOffTime] = React.useState("15:15");
```

- [ ] **Step 4: Add mode toggle UI at the top of the form return**

In the JSX return, before the existing strategy fields, add:
```jsx
{/* Mode toggle */}
<div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
  {["eod", "intraday"].map((m) => (
    <button
      key={m}
      onClick={() => setMode(m)}
      style={{
        padding: "4px 16px",
        borderRadius: 4,
        border: "1px solid #ccc",
        background: mode === m ? "#1890ff" : "#fff",
        color: mode === m ? "#fff" : "#333",
        cursor: "pointer",
        fontWeight: mode === m ? 600 : 400,
        textTransform: "capitalize",
      }}
    >
      {m === "eod" ? "EOD" : "Intraday"}
    </button>
  ))}
</div>

{/* Intraday-specific fields */}
{mode === "intraday" && (
  <IntradayFields
    entryTime={entryTime}
    squareOffTime={squareOffTime}
    legs={legs}
    onEntryTimeChange={setEntryTime}
    onSquareOffChange={setSquareOffTime}
    onLegSlChange={(idx, sl) => updateLeg(idx, { sl })}
    onLegTargetChange={(idx, target) => updateLeg(idx, { target })}
  />
)}
```

Add the import at the top of the file:
```jsx
import IntradayFields from "./IntradayFields";
import IntradaySlowPathWarning from "./IntradaySlowPathWarning";
```

Add slow-path warning state and render it:
```jsx
const [slowPath, setSlowPath] = React.useState(false);
// In the JSX, below the mode toggle:
<IntradaySlowPathWarning visible={slowPath} />
```

- [ ] **Step 5: Update the API call to route by mode**

In the file identified in Step 1, find the existing fetch/axios call to `/api/backtest` and update it to:
```js
const endpoint = mode === "intraday" ? "/api/intraday/backtest" : "/api/backtest";

const response = await fetch(endpoint, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

if (mode === "intraday") {
  // Arrow IPC response
  const buffer = await response.arrayBuffer();
  setSlowPath(response.headers.get("X-Slow-Path") === "true");
  const { decodeTradesheet } = await import("../utils/arrowDecoder.js");
  const trades = decodeTradesheet(buffer);
  setResults(trades); // existing results state setter
} else {
  // Existing EOD JSON path — unchanged
  const data = await response.json();
  setResults(data);
}
```

(Adapt to the exact variable names used in the existing component — `setResults` may be named differently.)

- [ ] **Step 6: Build and verify**

```bash
cd /home/user/Algo_Test_Software/frontend
npm run build 2>&1 | tail -10
```
Expected: build succeeds with no errors.

- [ ] **Step 7: Start dev server and manually test**

```bash
cd /home/user/Algo_Test_Software/frontend
npm run dev &
# Open http://localhost:5173 in browser
# Verify: EOD toggle shows existing UI; Intraday toggle shows new fields
# Verify: EOD backtest still works (no regressions)
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/StrategyBuilder.jsx frontend/src/
git commit -m "feat(frontend): intraday mode toggle + IntradayFields + routing by mode"
```

---

### Task 6: Entry/exit time columns in `ResultsPanel.jsx`

**Files:**
- Modify: `frontend/src/components/ResultsPanel.jsx`

- [ ] **Step 1: Read the existing columns in `ResultsPanel.jsx`**

```bash
grep -n "entry\|exit\|column\|header\|th>" /home/user/Algo_Test_Software/frontend/src/components/ResultsPanel.jsx | head -20
```

- [ ] **Step 2: Add `entry_time` and `exit_time` columns**

Find the table header row in `ResultsPanel.jsx`. After the existing `entry_price` / `exit_price` headers, add:
```jsx
{rows[0]?.entry_time !== undefined && <th>Entry Time</th>}
{rows[0]?.exit_time !== undefined && <th>Exit Time</th>}
```

In the table body row, after the corresponding price cells, add:
```jsx
{row.entry_time !== undefined && <td>{row.entry_time}</td>}
{row.exit_time !== undefined && <td>{row.exit_time}</td>}
```

The `!== undefined` guard means these columns only appear for intraday results, not EOD — so existing EOD results table is unaffected.

- [ ] **Step 3: Build**

```bash
cd /home/user/Algo_Test_Software/frontend && npm run build 2>&1 | tail -5
```
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ResultsPanel.jsx
git commit -m "feat(frontend): show entry_time/exit_time columns for intraday results"
```

---

### Task 7: Production build + smoke test

**Files:**
- Modify: `frontend/dist/` (auto-generated — never hand-edit)

- [ ] **Step 1: Build production frontend**

```bash
cd /home/user/Algo_Test_Software/frontend
npm run build 2>&1 | tail -5
```
Expected: `dist/` updated, no errors, bundle size reasonable.

- [ ] **Step 2: Start full stack and manually test intraday UI**

```bash
./start.sh
# Wait for health checks to pass, then open http://localhost:3000
```

Test checklist:
- [ ] Toggle to Intraday → entry time / square-off / SL fields appear
- [ ] Toggle back to EOD → intraday fields disappear, existing EOD form intact
- [ ] Run an EOD backtest → results display normally (no regression)
- [ ] Run an intraday backtest with real snapshot data → results show `entry_time` / `exit_time` columns

- [ ] **Step 3: Commit production build**

```bash
git add frontend/dist/
git commit -m "feat(frontend): production build with intraday mode toggle and fields"
```

---

## Self-Review

**Spec coverage:**
- §8.1 Mode toggle EOD | Intraday: Task 5 ✓
- §8.1 Extra fields (entry_time, square_off_time, per-leg SL/target, 4-symbol dropdown): Task 3 + Task 5 ✓
- §8.2 Slow-path warning banner: Task 4 + Task 5 (`X-Slow-Path` header) ✓
- §8.3 entry_time / exit_time columns: Task 6 ✓
- §5.4 Arrow IPC decode on frontend with `apache-arrow`: Task 1 + Task 2 ✓
- EOD non-regression: Task 5 (`mode === "eod"` path unchanged) + Task 7 manual test ✓

**No placeholders found.**

**Symbol dropdown** — spec §8.1 says "Symbol dropdown limited to the 4 supported indexes." Task 5 adds the mode toggle but relies on the existing symbol dropdown. If the existing dropdown shows all symbols (including stocks), it should be conditionally filtered when `mode === "intraday"` to show only `["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]`. Add this filter in Task 5 Step 4 when adapting to the actual component state.
