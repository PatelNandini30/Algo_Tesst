import { tableFromIPC } from "apache-arrow";

/**
 * Decode an Arrow IPC stream (ArrayBuffer or Uint8Array) into an array of
 * plain JS row objects. Column names match the backend tradesheet schema:
 * date, symbol, expiry, strike, opt_type, action, entry_time, entry_price,
 * exit_time, exit_price, exit_reason, quantity, pnl, mae, mfe
 *
 * @param {ArrayBuffer|Uint8Array} buffer
 * @returns {object[]}
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
