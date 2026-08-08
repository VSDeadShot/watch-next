import { monthLabel } from "@/lib/format";
import type { MonthCount } from "@/lib/types";

/**
 * How much watching happened, month by month.
 *
 * Columns rather than a line, because the value is a count of things that
 * happened in a bucket rather than a quantity that existed continuously between
 * one month and the next -- a line between two months would draw a slope
 * through days it knows nothing about.
 *
 * **Emphasis rather than a ramp.** Every column is the same recessive grey and
 * only the busiest one is white. Shading each bar by its own height would spend
 * the only channel left restating the length, and the useful thing a reader
 * wants from a two-year series is "when was the peak".
 *
 * **Windowed, and it says so.** A long history returns a month for every month
 * it spans -- thirty years is 366 of them, which is four pixels a column on a
 * phone and not a chart any more. The recent shape is what somebody came here
 * for, so the chart draws the last {@link MONTHS_SHOWN} and the caption says
 * how many it left out. Nothing is hidden: the header states the full span and
 * every month is in the table underneath.
 *
 * The empty months are drawn as empty slots rather than skipped. A gap in
 * somebody's viewing is information, and a series that closed up its gaps would
 * draw a lie about the shape of a year.
 */
const MONTHS_SHOWN = 24;

export default function MonthBars({
  entries,
  unit,
}: {
  entries: MonthCount[];
  /** Singular noun for the counts, e.g. "sitting" -> "8 sittings". */
  unit: string;
}) {
  if (entries.length === 0) return null;

  const shown = entries.slice(-MONTHS_SHOWN);
  const hidden = entries.length - shown.length;
  const busiest = shown.reduce((best, entry) =>
    entry.count > best.count ? entry : best,
  );
  // Guarded so a run of empty months cannot divide by zero. A series of all
  // zeroes draws as an empty track, which is the truth about it.
  const tallest = Math.max(busiest.count, 1);

  return (
    <div>
      <div
        className="flex h-28 items-end gap-[2px]"
        role="img"
        aria-label={`${unit}s per month from ${monthLabel(shown[0].month)} to ${monthLabel(
          shown[shown.length - 1].month,
        )}. Busiest was ${monthLabel(busiest.month)} with ${busiest.count}.`}
      >
        {shown.map((entry) => (
          // The track is the hover target as well as the slot, so an empty
          // month can still be pointed at and named.
          <div
            key={entry.month}
            title={`${monthLabel(entry.month)}: ${entry.count} ${
              entry.count === 1 ? unit : `${unit}s`
            }`}
            className="flex h-full flex-1 justify-center"
          >
            {/* Capped at 24px and centred, so a short series does not become a
                row of thick blocks. The leftover in each slot is air rather
                than more bar -- the data is the only thing allowed to be loud,
                and a wide fill reads louder than the same number does. */}
            <div className="flex h-full w-full max-w-6 items-end bg-raised">
              <div
                className={`w-full rounded-t-[3px] ${
                  entry.month === busiest.month ? "bg-white" : "bg-muted"
                }`}
                style={{ height: `${(entry.count / tallest) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Only the two ends are labelled on the axis. At two dozen columns a
          label per column would collide at any width a phone has, and the
          months in between are named in the table below. */}
      <div className="mt-2 flex justify-between text-xs text-dim">
        <span>{monthLabel(shown[0].month)}</span>
        <span>{monthLabel(shown[shown.length - 1].month)}</span>
      </div>

      <p className="mt-3 text-sm text-muted">
        Busiest was {monthLabel(busiest.month)} — {busiest.count}{" "}
        {busiest.count === 1 ? unit : `${unit}s`}.
        {hidden > 0 && (
          <span className="text-dim">
            {" "}
            Showing the last {MONTHS_SHOWN} months of {entries.length}.
          </span>
        )}
      </p>

      {/* Every value in text, so nothing here is readable only by hovering or
          only by comparing one column's height against another by eye. */}
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-dim underline underline-offset-4 transition-colors hover:text-white">
          Show the numbers
        </summary>
        <table className="mt-3 w-full max-w-xs text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-dim">
              <th scope="col" className="py-1.5 font-normal">
                Month
              </th>
              <th scope="col" className="py-1.5 text-right font-normal">
                {unit[0].toUpperCase() + unit.slice(1)}s
              </th>
            </tr>
          </thead>
          <tbody>
            {[...entries].reverse().map((entry) => (
              <tr key={entry.month} className="border-b border-line/50">
                <td className="py-1.5">{monthLabel(entry.month)}</td>
                <td className="py-1.5 text-right tabular-nums text-muted">
                  {entry.count}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

