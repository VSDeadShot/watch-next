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
          className="flex items-center gap-3"
        >
          <span className="w-[38%] shrink-0 truncate text-sm sm:w-[30%]">
            {entry.label}
          </span>

          {/* The track is a lighter step of the same monochrome ramp, so a
              short bar still reads as a short bar rather than as missing. */}
          <span className="h-2 flex-1 bg-raised">
            <span
              className="block h-full rounded-r-[3px] bg-muted"
              style={{ width: `${Math.max((entry.count / largest) * 100, 2)}%` }}
            />
          </span>

          {/* Tabular here, unlike a stat tile: this is a column of numbers that
              has to line up down the right edge. */}
          <span className="w-8 shrink-0 text-right text-sm text-dim tabular-nums">
            {entry.count}
          </span>
        </li>
      ))}
    </ul>
  );
}
