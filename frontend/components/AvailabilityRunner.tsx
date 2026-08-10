"use client";

import { useCallback, useRef, useState } from "react";
import BatchBar from "@/components/BatchBar";
import { apiRequest, errorMessage } from "@/lib/api";
import type { AvailabilityRefresh } from "@/lib/types";

/**
 * Asking the catalogue again where things stream.
 *
 * Availability is a hard filter rather than a ranking signal, so a stale answer
 * here does not make the recommendation worse -- it makes it wrong, confidently,
 * by offering something that left the service months ago. Nothing announces a
 * title leaving Netflix, so the only way to know is to ask again.
 *
 * Shaped like {@link ResolveRunner} because it has the same problem: one request
 * per title, paced at a second each by our own choice against somebody else's
 * API, which is minutes of work for a real library. As one request that is a
 * spinner with no way out; in batches it is a number going down and a button
 * that stops it.
 *
 * {@link BATCH} is chosen for how long a stop takes to take effect rather than
 * for throughput -- a batch already in flight is not abandoned, so at a second
 * a title this is also roughly the latency on "Stop".
 */
const BATCH = 10;

type Progress = {
  checked: number;
  failed: number;
  // Not the count of titles that stream somewhere: one title on three services
  // is three of these. Shown anyway, and named as places rather than as offers,
  // because it is the only evidence on screen that the pass learned anything --
  // "40 titles checked" alone reads the same whether the catalogue answered
  // fully or answered with nothing at all.
  places: number;
  remaining: number;
};

const NOTHING: Progress = { checked: 0, failed: 0, places: 0, remaining: 0 };

export default function AvailabilityRunner() {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  // A ref rather than state: the loop reads it between batches and would
  // otherwise close over whatever the flag was when the run started.
  const halt = useRef(false);

  const run = useCallback(async () => {
    halt.current = false;
    setRunning(true);
    setStopped(false);
    setError(null);

    const total = { ...NOTHING };
    try {
      for (;;) {
        const batch = await apiRequest<AvailabilityRefresh>(
          `/api/offers/refresh?limit=${BATCH}`,
          { method: "POST" },
        );

        const attempted = batch.refreshed + batch.failed;
        total.checked += batch.refreshed;
        total.failed += batch.failed;
        total.places += batch.offers_stored;
        total.remaining = batch.remaining;
        setProgress({ ...total });

        if (batch.remaining === 0) break;

        // Nothing was asked although something is outstanding. Should not
        // happen, and looping on it would be an unbreakable spin, so it ends
        // the run rather than being trusted not to occur.
        if (attempted === 0) break;

        // Every request failed, so nothing was learned and nothing was stored
        // -- which means the same titles are still stale and `remaining` has
        // not moved. Watching only for zero would spin here for as long as
        // JustWatch stayed down.
        if (batch.failed === attempted) {
          setError(
            "JustWatch is not answering. Nothing was lost — what was checked is saved, and the rest still has its old answer.",
          );
          break;
        }

        if (halt.current) {
          setStopped(true);
          break;
        }
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRunning(false);
    }
  }, []);

  const done = progress !== null && !running;
  const foundNothingToDo =
    progress !== null &&
    progress.checked === 0 &&
    progress.failed === 0 &&
    progress.remaining === 0;

  return (
    <section className="mt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-3">
        <h2 className="text-base font-medium">Where things are streaming</h2>
        {running && (
          <button
            type="button"
            onClick={() => {
              halt.current = true;
            }}
            className="text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            Stop
          </button>
        )}
      </div>

      <p className="mt-4 max-w-xl text-sm leading-relaxed text-muted">
        A title leaves a service and nothing announces it. Answers older than a
        week are asked again here, oldest first — a title a second, because this
        is somebody else&apos;s API and we are not a paying customer of it. Stop
        whenever; it carries on from where it got to.
      </p>

      {progress !== null && (
        <div className="mt-5">
          <BatchBar
            done={progress.checked + progress.failed}
            left={progress.remaining}
            label="Titles re-checked"
          />
          <p className="mt-2 text-sm">
            {foundNothingToDo ? (
              <span className="text-muted">
                Everything was already up to date.
              </span>
            ) : (
              <>
                {progress.checked.toLocaleString()}{" "}
                {progress.checked === 1 ? "title" : "titles"} checked
                <span className="text-muted">
                  , {progress.places.toLocaleString()}{" "}
                  {progress.places === 1 ? "place" : "places"} to watch
                </span>
                {progress.failed > 0 && (
                  <span className="text-muted">
                    {/* Named as "kept" rather than "failed": nothing was lost,
                        the old answer simply stands until the next pass. */}
                    , {progress.failed.toLocaleString()} kept for later
                  </span>
                )}
                {progress.remaining > 0 ? (
                  <span className="text-dim">
                    {" "}
                    · {progress.remaining.toLocaleString()} to go
                  </span>
                ) : (
                  <span className="text-dim"> · finished</span>
                )}
              </>
            )}
          </p>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm">
          {error}
        </p>
      )}

      {stopped && !error && (
        <p className="mt-3 text-sm text-muted">
          Stopped. Start again whenever — it carries on from here.
        </p>
      )}

      <button
        type="button"
        onClick={() => void run()}
        disabled={running}
        className="mt-5 border border-edge px-3.5 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50"
      >
        {running
          ? "Checking…"
          : done && progress.remaining === 0
            ? "Check again"
            : progress !== null
              ? "Carry on"
              : "Check availability"}
      </button>
    </section>
  );
}
