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

const DECIMAL = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * "$1,742.05" for a card decision; "444.20 Euro" for an AML one.
 *
 * `currency` is null for Phase-2 card rows (dollars by construction) and carries the real IBM
 * currency name for AML rows — the extract spans 14 of them, so formatting every amount as USD
 * would print a Euro transfer with a dollar sign. IBM's names ("Euro", "Yuan", "Saudi Riyal") are
 * NOT ISO 4217 codes, so they are appended verbatim rather than mapped through a lookup table we
 * would have to invent.
 */
export function formatAmount(amount: number, currency?: string | null): string {
  if (currency == null) return USD.format(amount);
  if (currency === "US Dollar") return USD.format(amount);
  return `${DECIMAL.format(amount)} ${currency}`;
}

/** "3,842" — thousands-separated counts for the header / fleet summary. */
export function formatCount(n: number): string {
  return COUNT.format(n);
}

/**
 * Confidence as a fixed 2-decimal value ("0.95"); the raw model number.
 *
 * Null for AML decisions — the structural witness is DETERMINISTIC (a witness either exists or it
 * does not), so there is no confidence to report and none is invented. Rendered as an em dash so
 * the absence is visible rather than silently shown as 0.00.
 */
export function formatConfidence(confidence: number | null): string {
  if (confidence == null) return "—";
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
