"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import WatchlistRow from "@/components/WatchlistRow";
import { apiRequest, errorMessage } from "@/lib/api";
import type { WatchlistItem } from "@/lib/types";

/**
 * The things somebody chose.
 *
 * This is the only page in the app showing a list, and the only one entitled
 * to. Everywhere else a list would mean the app offering options and asking
 * somebody to decide, which is the paralysis the whole product exists to
 * remove. These are not offers -- every row is here because a person put it
 * here, and a list of your own decisions is not a decision to make.
 *
 * The order is the order they were decided in, newest first, and availability
 * deliberately does not reorder it. Sorting the watchable ones to the top would
 * be the app quietly overruling somebody's own ranking of what they want to
 * see; the count in the header says how many are watchable without touching
 * what they chose.
 */
export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[] | null>(null);
  const [showWatched, setShowWatched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;

    apiRequest<WatchlistItem[]>(`/api/watchlist?include_watched=${showWatched}`)
      .then((list) => {
        if (!live) return;
        setItems(list);
        setError(null);
      })
      .catch((caught) => live && setError(errorMessage(caught)));

    return () => {
      live = false;
    };
  }, [showWatched, attempt]);

  /**
   * Every change goes back to the server and the row is redrawn from the reply.
   *
   * Not optimistic. A watchlist is small and every action here is one click a
   * minute at most, so there is nothing to win by guessing -- and guessing
   * wrong would show somebody a list that disagrees with the one that is
   * stored, which is worse on this page than on any other in the app.
   */
  const act = useCallback(
    async (titleId: number, work: () => Promise<WatchlistItem | null>) => {
      setBusy(titleId);
      setError(null);
      try {
        const updated = await work();
        setItems((current) => {
          if (current === null) return current;
          if (updated === null) {
            return current.filter((item) => item.title_id !== titleId);
          }
          // Ticking something off when watched entries are hidden takes it out
          // of the list rather than leaving a row the filter says is not there.
          if (updated.watched_at !== null && !showWatched) {
            return current.filter((item) => item.title_id !== titleId);
          }
          return current.map((item) =>
            item.title_id === titleId ? updated : item,
          );
        });
      } catch (caught) {
        setError(errorMessage(caught));
      } finally {
        setBusy(null);
      }
    },
    [showWatched],
  );

  const waiting = items?.filter((item) => item.watched_at === null) ?? [];
  const watchable = waiting.filter((item) => item.watch_on.length > 0).length;

  return (
    <div>
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Saved
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          {items === null
            ? "Everything you decided you wanted to watch."
            : summarise(waiting.length, watchable)}
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="mt-8 border border-line bg-panel px-4 py-3 text-sm"
        >
          <p>{error}</p>
          <button
            onClick={() => setAttempt((n) => n + 1)}
            className="mt-2 text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            Try again
          </button>
        </div>
      )}

      {items !== null && (items.length > 0 || showWatched) && (
        <div className="mt-8 flex items-center justify-between border-b border-line pb-3">
          <h2 className="text-base font-medium">
            {showWatched ? "Everything" : "Still waiting"}
          </h2>
          <button
            type="button"
            onClick={() => setShowWatched((on) => !on)}
            className="text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            {showWatched ? "Hide what I've watched" : "Show what I've watched"}
          </button>
        </div>
      )}

      <div className="mt-5 space-y-3">
        {items === null && !error ? (
          <Skeleton />
        ) : items?.length === 0 ? (
          <Empty showingWatched={showWatched} />
        ) : (
          items?.map((item) => (
            <WatchlistRow
              key={item.title_id}
              item={item}
              busy={busy === item.title_id}
              onWatched={(watched) =>
                void act(item.title_id, () =>
                  apiRequest<WatchlistItem>(`/api/watchlist/${item.title_id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ watched }),
                  }),
                )
              }
              onNote={(note) =>
                void act(item.title_id, () =>
                  apiRequest<WatchlistItem>(`/api/watchlist/${item.title_id}`, {
                    method: "PATCH",
                    body: JSON.stringify({ note }),
                  }),
                )
              }
              onRemove={() =>
                void act(item.title_id, async () => {
                  await apiRequest<void>(`/api/watchlist/${item.title_id}`, {
                    method: "DELETE",
                  });
                  return null;
                })
              }
            />
          ))
        )}
      </div>
    </div>
  );
}

/** The header line, which is the only place availability is counted up. */
function summarise(waiting: number, watchable: number): string {
  if (waiting === 0) {
    return "Everything you decided you wanted to watch.";
  }
  const things = `${waiting} ${waiting === 1 ? "thing" : "things"} waiting`;
  if (watchable === 0) {
    return `${things}, and none of them are on your services right now.`;
  }
  if (watchable === waiting) {
    return waiting === 1
      ? `${things}, and you can watch it tonight.`
      : `${things}, and you can watch all of them tonight.`;
  }
  return `${things}. ${watchable} you can watch tonight.`;
}

function Empty({ showingWatched }: { showingWatched: boolean }) {
  return (
    <div className="border border-line bg-panel px-5 py-10 text-center">
      <p className="text-[15px]">
        {showingWatched ? "Nothing here at all yet." : "Nothing waiting."}
      </p>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
        Ask what to watch tonight, and save anything you like the look of but do
        not have time for now.
      </p>
      <Link
        href="/"
        className="mt-5 inline-block bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90"
      >
        Find something
      </Link>
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden className="space-y-3">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          key={index}
          className="h-[148px] animate-pulse border border-line bg-panel"
        />
      ))}
    </div>
  );
}
