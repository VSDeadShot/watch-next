/**
 * One number worth reading on its own.
 *
 * A tile rather than a chart, because a single value drawn as a one-bar bar
 * chart is a chart that says nothing the number did not: there is no comparison
 * on the page for that bar to be long or short against.
 *
 * The value is deliberately not display-sized. `--text-display` belongs to the
 * recommendation card alone and nothing else in this app may spend it -- a
 * statistic shouting louder than the one thing somebody was told to watch would
 * have the product's priorities the wrong way round. So there is no hero figure
 * on this page, by choice rather than by omission.
 *
 * `hint` is where a number admits what it rests on. A total that came from only
 * some of the history is a lower bound, and a lower bound presented as a total
 * is the kind of small lie this app is built not to tell.
 */
export default function StatTile({
  label,
  value,
  hint,
  wide = false,
}: {
  label: string;
  value: string;
  hint?: string;
  /**
   * Take the whole row in a two-column grid.
   *
   * Three tiles into two columns leaves the last one with an empty half beside
   * it, which reads as a gap rather than a layout. Spanning the row below `sm`
   * makes it deliberate -- and the odd-one-out is usually the number people
   * came to look at.
   */
  wide?: boolean;
}) {
  return (
    <div
      className={`h-full border border-line bg-panel px-4 py-4 sm:px-5 ${
        wide ? "col-span-2 sm:col-span-1" : ""
      }`}
    >
      <p className="text-[13px] text-dim">{label}</p>
      {/* Proportional figures, not tabular: at this size equal-width digits
          make a number like 121 read loose. Tabular is for columns that have
          to line up vertically, which this is not. */}
      <p className="mt-1.5 text-2xl leading-none font-medium tracking-[-0.02em]">
        {value}
      </p>
      {hint && (
        <p className="mt-2 text-xs leading-relaxed text-dim">{hint}</p>
      )}
    </div>
  );
}
