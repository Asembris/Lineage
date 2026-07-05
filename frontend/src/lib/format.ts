/*
 * Small, deterministic formatters for the data-first console. Every number, id,
 * and timestamp in the UI passes through here so the mono record reads uniformly.
 *
 * Timestamps are sliced straight from the server's ISO-8601 string (not parsed
 * through the local timezone) so the record shows exactly what the cluster stored
 * — a forensic log, not a locale-shifted view.
 */

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

const COUNT = new Intl.NumberFormat("en-US");

/** "$1,742.05" — merchant amounts, mono, right-aligned in the feed. */
export function formatAmount(amount: number): string {
  return USD.format(amount);
}

/** "3,842" — thousands-separated counts for the header / fleet summary. */
export function formatCount(n: number): string {
  return COUNT.format(n);
}

/** Confidence as a fixed 2-decimal value ("0.95"); the raw model number. */
export function formatConfidence(confidence: number): string {
  return confidence.toFixed(2);
}

/** First 6 chars of a UUID — the console's node/agent identity convention. */
export function fragId(id: string): string {
  return id.slice(0, 6);
}

/** Split an ISO-8601 instant into { date: "2026-06-14", time: "08:22:01" }. */
export function splitInstant(iso: string): { date: string; time: string } {
  const t = iso.indexOf("T");
  if (t === -1) return { date: iso, time: "" };
  return { date: iso.slice(0, t), time: iso.slice(t + 1, t + 9) };
}

/** "2026-06-14" — the date portion of an ISO-8601 instant. */
export function formatDate(iso: string): string {
  return splitInstant(iso).date;
}
