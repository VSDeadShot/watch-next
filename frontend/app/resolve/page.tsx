"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import DecidedRow from "@/components/DecidedRow";
import ResolveRunner from "@/components/ResolveRunner";
import UnresolvedRow from "@/components/UnresolvedRow";
import { apiRequest, errorMessage } from "@/lib/api";
import { count } from "@/lib/format";
import type {
  ManualResolution,
  ResolvedTitle,
  UnresolvedPage,
} from "@/lib/types";

/**
 * Turning a history into things the catalogue recognises.
 *
 * Two jobs in a fixed order, and the order is not a layout choice. Nothing can
 * be decided by hand until a pass has asked, because a refusal only exists once
 * something has been refused -- straight after an import there are no
 * unresolved *answers*, only unlinked rows. A page that led with the queue
 * would show an empty list to somebody whose entire library was unmatched, and
 * they would reasonably conclude the app was broken.
 *
 * Not in the nav, on purpose. This is a chore rather than a place: it exists
 * while there is something wrong and never again once the list is empty, and a
 * permanent link advertising an empty queue is the sort of navigation the top
 * bar is deliberately short of. It is reached from the two screens that already
 * know there is a problem -- the import summary, and the stats page.
 *
 * Every change is sent to the server and the lists are read back rather than
 * patched in place. Deciding one title can link eighty rows and move an entry
 * from one list to the other, and guessing at that locally would mean showing
 * somebody a page that quietly disagrees with what is stored.
 */
const PAGE_SIZE = 25;

export default function ResolvePage() {
  const [queue, setQueue] = useState<UnresolvedPage | null>(null);
  const [decided, setDecided] = useState<ResolvedTitle[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;

    Promise.all([
      apiRequest<UnresolvedPage>(
        `/api/titles/unresolved?limit=${PAGE_SIZE}&offset=${offset}`,
      ),
      apiRequest<ResolvedTitle[]>("/api/titles/resolutions"),
    ])
      .then(([page, history]) => {
        if (!live) return;
        setQueue(page);
        setDecided(history);
        setError(null);
      })
      .catch((caught) => live && setError(errorMessage(caught)));

    return () => {
      live = false;
    };
  }, [offset, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  const decide = useCallback(
    async (resolutionId: number, nodeId: string) => {
      setBusy(resolutionId);
      setError(null);
      setNote(null);
      try {
        const fixed = await apiRequest<ManualResolution>(
          `/api/titles/resolutions/${resolutionId}`,
          { method: "PUT", body: JSON.stringify({ node_id: nodeId }) },
        );
        setNote(
          `${fixed.title} — ${count(fixed.linked_events, "sitting")} now counted.`,
        );
        reload();
      } catch (caught) {
        setError(errorMessage(caught));
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  const waiting = queue?.total ?? 0;
  const shown = queue?.items.length ?? 0;

  return (
    <div>
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Match your history
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          An export gives us the name of a thing and nothing else. Everything the
          app does with your history — what you watch, how long for, what to
          suggest tonight — comes from matching those names against a catalogue.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-8 border border-line bg-panel px-4 py-3 text-sm"
        >
          <p>{error}</p>
          <button
            onClick={reload}
            className="mt-2 text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            Try again
          </button>
        </div>
      )}

      <div className="mt-8">
        <ResolveRunner onFinished={reload} />
      </div>

      {note && (
        <p role="status" className="mt-4 text-sm text-muted">
          {note}
        </p>
      )}

      <section className="mt-10">
        <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-line pb-3">
          <h2 className="text-base font-medium">Needs you</h2>
          {queue !== null && waiting > shown && (
            // A range rather than a count. "Showing 1 of 26" on the last page
            // is true and reads as though twenty-five went missing; saying
            // which ones they are cannot be misread.
            <p className="text-sm text-dim">
              {(offset + 1).toLocaleString()}–
              {(offset + shown).toLocaleString()} of {waiting.toLocaleString()}
            </p>
          )}
        </div>

        {queue === null && !error ? (
          <Skeleton />
        ) : waiting === 0 ? (
          <Nothing />
        ) : (
          <>
            <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted">
              These are the ones the matcher would not guess at. The most
              consequential are first — deciding a series you worked through
              fixes every episode of it at once.
            </p>
            <ul className="mt-4 space-y-3">
              {queue?.items.map((title) => (
                <UnresolvedRow
                  key={title.resolution_id}
                  title={title}
                  busy={busy === title.resolution_id}
                  onPick={(nodeId) => void decide(title.resolution_id, nodeId)}
                />
              ))}
            </ul>
            <Pager
              offset={offset}
              shown={shown}
              total={waiting}
              onMove={setOffset}
            />
          </>
        )}
      </section>

      {decided !== null && decided.length > 0 && (
        <section className="mt-10">
          <h2 className="border-b border-line pb-3 text-base font-medium">
            Decided by hand
          </h2>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted">
            The recent ones, in case any of them went the wrong way. You are
            asked about a title precisely when two entries look alike, so this is
            where the 1984 Dune gets swapped for the 2021 one.
          </p>
          <ul className="mt-4 space-y-2">
            {decided.map((decision) => (
              <DecidedRow
                key={decision.resolution_id}
                decision={decision}
                busy={busy === decision.resolution_id}
                onPick={(nodeId) => void decide(decision.resolution_id, nodeId)}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Pager({
  offset,
  shown,
  total,
  onMove,
}: {
  offset: number;
  shown: number;
  total: number;
  onMove: (next: number) => void;
}) {
  const more = offset + shown < total;
  if (offset === 0 && !more) return null;

  return (
    <div className="mt-5 flex items-center justify-between">
      <button
        type="button"
        onClick={() => onMove(Math.max(offset - PAGE_SIZE, 0))}
        disabled={offset === 0}
        className="border border-edge px-3 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised disabled:opacity-50"
      >
        Back
      </button>
      <button
        type="button"
        onClick={() => onMove(offset + PAGE_SIZE)}
        disabled={!more}
        className="border border-edge px-3 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised disabled:opacity-50"
      >
        More
      </button>
    </div>
  );
}

/**
 * Nothing in the queue, which means one of two quite different things.
 *
 * Either the matching has not been run yet, or it has and it decided
 * everything. Both look identical from here -- the queue is empty either way --
 * so this says both rather than picking one and risking telling somebody their
 * library is fine when nothing has looked at it.
 */
function Nothing() {
  return (
    <div className="mt-4 border border-line bg-panel px-5 py-8 text-center">
      <p className="text-[15px]">Nothing waiting on you.</p>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
        Either everything matched, or the matching has not been run over this
        history yet. If you have just imported something, start it above.
      </p>
      <Link
        href="/stats"
        className="mt-5 inline-block text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
      >
        See what your history adds up to
      </Link>
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden className="mt-4 space-y-3">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          key={index}
          className="h-[132px] animate-pulse border border-line bg-panel"
        />
      ))}
    </div>
  );
}
