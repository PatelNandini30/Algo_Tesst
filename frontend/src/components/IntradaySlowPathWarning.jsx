import React from "react";

/**
 * Shows a yellow warning when the API indicates a slow-path backtest.
 * Props:
 *   visible   boolean
 *   reason    string (optional)
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
