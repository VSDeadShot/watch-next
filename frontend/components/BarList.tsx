import type { LabelledCount } from "@/lib/types";

/**
 * A short ranked list, drawn as bars.
 *
 * Bars because the job is comparing magnitude, and length is the one encoding
 * everybody reads the same way. One fill for every bar, never darker-where-
 * bigger: a value ramp would spend the only free channel restating the length
 * the bar already shows, and on genres -- which have no natural order -- it
 * would imply one.
 *
 * No legend. There is a single series here, so the heading above already names
 * what is plotted and a box with one swatch in it would only repeat the
 * heading and take up room.
 *
 * The order is whatever the backend sent and is never re-sorted here. It means
 * something different per list -- genres arrive commonest-first, decades arrive
 * oldest-first, and sorting a decade histogram by size would destroy the only
 * axis it has.
 *
 * Every value is written next to its bar, so nothing on this page is readable
 * only by hovering or only by comparing lengths by eye.
 */
export default function BarList({
  entries,
  unit,
}: {
  entries: LabelledCount[];
  /** Singular noun for the tooltip, e.g. "title" -> "4 titles". */
  unit: string;
}) {
  // Scaled against the largest bar rather than the total. The question these
  // answer is "which of these is the big one", and against a total every bar in
  // a long tail would be a stub too short to compare.
  const largest = Math.max(...entries.map((entry) => entry.count), 1);

  return (
    <ul className="space-y-2.5">
      {entries.map((entry) => (
        <li
          key={entry.label}
          title={`${entry.label}: ${entry.count} ${entry.count === 1 ? unit : `${unit}s`}`}
          // Two rows on a phone, one from `sm` up. Splitting the width by
          // percentage gave the bar the larger share at every phone size, and
          // the labels that lost the argument -- "Action & Adventure",
          // "Technology Connections" -- were the ones worth reading. The bar
          // encodes a number that is also printed beside it; the label encodes
          // nothing else anywhere. So the label gets the line.
          className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-1.5 sm:grid-cols-[30%_minmax(0,1fr)_auto] sm:items-center"
        >
          <span className="truncate text-sm">{entry.label}</span>

          {/* Tabular here, unlike a stat tile: this is a column of numbers that
              has to line up down the right edge. Last in the row from `sm` up,
              beside the label below it -- a number that has floated to the far
              side of a phone is no longer next to the thing it counts. */}
          <span className="w-8 text-right text-sm text-dim tabular-nums sm:order-3">
            {entry.count}
          </span>

          {/* The track is a lighter step of the same monochrome ramp, so a
              short bar still reads as a short bar rather than as missing. */}
          <span className="col-span-2 h-2 bg-raised sm:order-2 sm:col-span-1">
            <span
              className="block h-full rounded-r-[3px] bg-muted"
              style={{ width: `${Math.max((entry.count / largest) * 100, 2)}%` }}
            />
          </span>
        </li>
      ))}
    </ul>
  );
}
