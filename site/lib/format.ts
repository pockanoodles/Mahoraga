// Formatting for the engine's numbers. It holds no data, so a client component
// can import it without pulling the whole export (lib/data.ts) into the bundle:
// a client component gets its numbers as props from the server-rendered page.

export const pct = (x: number, digits = 0) => `${(x * 100).toFixed(digits)}%`;

export function usd(x: number, digits = 0): string {
  const abs = Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return x < 0 ? `−$${abs}` : `$${abs}`;
}

/** A per-task cost, which can be a fraction of a cent: enough digits to show it isn't zero. */
export function usdSmall(x: number): string {
  if (x >= 0.01) return `$${x.toFixed(2)}`;
  const digits = Math.min(6, 1 - Math.floor(Math.log10(x)));
  return `$${x.toFixed(digits)}`;
}

/** A cloud price per task, as the slider shows it. */
export function priceText(x: number): string {
  return x < 1 ? `$${x.toFixed(2)}` : Number.isInteger(x) ? `$${x}` : `$${x.toFixed(2)}`;
}
