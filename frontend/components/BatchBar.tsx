/**
 * How far along a batched pass is.
 *
 * The denominator is what has been done plus what is left, so it grows as the
 * run discovers how much there was -- there is no way to know the total before
 * the first batch comes back, and inventing one would mean a bar that jumped.
 *
 * Shared rather than copied, because this app now has two passes with exactly
 * this shape -- the matcher and the availability refresh -- and both spend a
 * request a second against the same unofficial API while learning their own
 * size as they go. A second copy would be a second place to get the empty run
 * wrong.
 */
export default function BatchBar({
  done,
  left,
  label,
}: {
  done: number;
  left: number;
  label: string;
}) {
  const total = done + left;
  // Nothing to do is drawn as finished, not as not-started. A pass that found
  // no work is complete, and an empty bar would say the opposite of that.
  const share = total === 0 ? 1 : done / total;

  return (
    <div
      className="h-1.5 w-full bg-raised"
      role="progressbar"
      aria-valuenow={done}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-label={label}
    >
      <div
        className="h-full bg-muted transition-[width] duration-500"
        style={{ width: `${Math.round(share * 100)}%` }}
      />
    </div>
  );
}
