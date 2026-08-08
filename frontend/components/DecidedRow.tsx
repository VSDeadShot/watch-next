"use client";

import { useState } from "react";
import CandidatePicker from "@/components/CandidatePicker";
import { monthLabel } from "@/lib/format";
import type { ResolvedTitle } from "@/lib/types";

/**
 * Something already decided by hand, offered back in case it was decided wrong.
 *
 * A manual answer leaves the queue the moment it is given, which is right --
 * it is answered -- but it also means a mistake is invisible from then on. The
 * mistake this flow invites is a specific one: the queue asks about a title
 * precisely when two catalogue entries looked equally plausible, so the times
 * somebody is most likely to pick wrong are exactly the times they were least
 * sure. Picking the 1984 Dune when they meant the 2021 one is the whole reason
 * this list exists.
 *
 * Collapsed by default, because this is a footnote to the queue rather than a
 * second queue. Opening it offers the same choice again, from the same
 * rejected candidates the matcher had -- and the one in force is marked, so
 * changing your mind starts from what you actually chose.
 */
export default function DecidedRow({
  decision,
  busy,
  onPick,
}: {
  decision: ResolvedTitle;
  busy: boolean;
  onPick: (nodeId: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <li className="border border-line bg-panel px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-sm">
          <span className="text-dim">{decision.query_title}</span>
          <span className="text-dim"> → </span>
          <span>{decision.title}</span>
          {decision.release_year !== null && (
            <span className="text-dim"> {decision.release_year}</span>
          )}
        </p>
        <button
          type="button"
          onClick={() => setOpen((shown) => !shown)}
          aria-expanded={open}
          className="text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
        >
          {open ? "Leave it" : "Change"}
        </button>
      </div>

      <p className="mt-0.5 text-xs text-dim">
        Decided {monthLabel(decision.resolved_at)}
      </p>

      {open && (
        <CandidatePicker
          candidates={decision.candidates}
          kind={decision.kind}
          busy={busy}
          onPick={onPick}
          picked={decision.jw_node_id}
        />
      )}
    </li>
  );
}

