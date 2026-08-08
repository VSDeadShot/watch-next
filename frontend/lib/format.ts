/**
 * Turning the numbers the backend sends into words worth reading.
 *
 * Shared rather than kept beside the one component that needed it first,
 * because the month formatter carries a trap that must not be re-fallen into
 * once per file. See {@link monthLabel}.
 */

const MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * `"2026-01-01"` or `"2026-01-05T20:00:00Z"` -> `"Jan 2026"`.
 *
 * Parsed by hand, and deliberately never through `Date`. An ISO date is read as
 * UTC midnight, so formatting it in local time anywhere west of Greenwich
 * renders the month before -- which would shift every bucket of a chart by one
 * for half the world, silently and only for them. The backend counts these
 * months in UTC, so reading them in UTC is also the only way the label agrees
 * with the number beside it.
 */
export function monthLabel(iso: string): string {
  const [year, month] = iso.slice(0, 7).split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

/**
 * Minutes as the largest honest unit.
 *
 * Hours once there is more than one, because "5,532 minutes" is a number nobody
 * has a feel for and "92 hours" is one they do. Rounded down for the same reason
 * the backend floors it: claiming a minute nobody watched is a small lie, and
 * this figure is already a lower bound.
 */
export function watchTime(minutes: number): string {
  if (minutes < 60) {
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  }
  const hours = Math.floor(minutes / 60);
  return `${hours.toLocaleString()} ${hours === 1 ? "hour" : "hours"}`;
}

/** `3, "sitting"` -> `"3 sittings"`. Naive plural; every caller passes a word it works for. */
export function count(n: number, word: string): string {
  return `${n.toLocaleString()} ${n === 1 ? word : `${word}s`}`;
}
