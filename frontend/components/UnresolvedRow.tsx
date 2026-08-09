"use client";

import CandidatePicker from "@/components/CandidatePicker";
import { count } from "@/lib/format";
import type { UnresolvedTitle } from "@/lib/types";

/**
 * One title the matcher would not guess at, waiting for a person.
 *
 * The count of sittings is the most important thing on the row after the title
 * itself, because it is what makes one chore worth more than another: deciding
 * a show somebody watched eighty episodes of fixes eighty rows, and the film
 * they half-watched once fixes one. The queue arrives in that order and the row
 * says why.
 *
 * The matcher's own reason is shown rather than summarised. It was written for
 * a person to read, and "two titles matched equally well" tells somebody
 * something quite different from "nothing came back at all" -- the first is a
 * choice to make and the second means searching for it yourself.
 */
export default function UnresolvedRow({
  title,
  busy,
  onPick,
}: {
  title: UnresolvedTitle;
  busy: boolean;
  onPick: (nodeId: string) => void;
}) {
  return (
    <li className="border border-line bg-panel px-4 py-5 sm:px-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-[15px] font-medium">{title.query_title}</h3>
        <p className="text-sm text-dim">
          {count(title.event_count, "sitting")} waiting
        </p>
      </div>

      <p className="mt-1.5 text-sm text-muted">
        {title.reason || "The matcher could not decide."}
      </p>

      <CandidatePicker
        candidates={title.candidates}
        kind={title.kind}
        busy={busy}
        onPick={onPick}
      />
    </li>
  );
}
