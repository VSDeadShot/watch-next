"use client";

import { useCallback, useRef, useState } from "react";
import { apiRequest, errorMessage } from "@/lib/api";
import type { ResolveSummary } from "@/lib/types";

/**
 * Running the matcher over the library, in pieces somebody can watch.
 *
 * The pass paces itself at one request a second against an unofficial API, by
 * our own choice rather than theirs, so a real history is minutes of work. Done
 * as a single request that would be minutes of a spinner with no way out; done
 * in batches it is a number that goes down and a button that stops it.
 *
 * {@link BATCH} is what one request is allowed to spend, and it is chosen for
 * how long somebody waits rather than for throughput: at a second a search it
 * is also roughly how long "stop" takes to take effect, because a batch already
 * in flight is not abandoned. Ten seconds of latency on a stop is tolerable and
 * twenty-five is not, which is the whole argument for the number.
 *
 * **Stopping when nothing is progressing matters as much as stopping on
 * demand.** A search that fails stores no answer, so the same title is retried
 * by the next batch and `remaining` does not fall -- which means a loop that
 * only watched for zero would spin for as long as the API stayed down. Every
 * search failing is treated as the outage it is.
 */
const BATCH = 10;

type Progress = {
  searched: number;
  matched: number;
  needsDeciding: number;
  remaining: number;
};

const NOTHING: Progress = { searched: 0, matched: 0, needsDeciding: 0, remaining: 0 };

export default function ResolveRunner({ onFinished }: { onFinished: () => void }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  // A ref rather than state: the loop below reads it between batches and would
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
        const batch = await apiRequest<ResolveSummary>(
          `/api/titles/resolve?limit=${BATCH}`,
          { method: "POST" },
        );

        total.searched += batch.searched;
        total.matched += batch.resolved;
        total.needsDeciding += batch.unresolved;
        total.remaining = batch.remaining;
        setProgress({ ...total });

        if (batch.remaining === 0) break;

        // Nothing was asked although something is outstanding. Should not
        // happen, and looping on it would be an unbreakable spin, so it ends
        // the run rather than being trusted not to occur.
        if (batch.searched === 0) break;

        if (batch.failed === batch.searched) {
          setError(
            "JustWatch is not answering. Nothing was lost — what has been matched is saved, and the rest is still waiting.",
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
      onFinished();
    }
  }, [onFinished]);

  const done = progress !== null && !running;

  return (
    <section className="border border-line bg-panel px-4 py-5 sm:px-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-medium">Matching</h2>
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

      <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">
        Every title in your history is looked up in the catalogue once. It runs a
        title a second on purpose — this is somebody else&apos;s API and we are
        not a paying customer of it — so a long history takes a while. You can
        stop and pick it up later; nothing is asked about twice.
      </p>

      {progress !== null && (
        <div className="mt-4">
          <Bar done={progress.searched} left={progress.remaining} />
          <p className="mt-2 text-sm">
            {progress.matched.toLocaleString()} matched
            {progress.needsDeciding > 0 && (
              <span className="text-muted">
                , {progress.needsDeciding.toLocaleString()} need you
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
        className="mt-4 bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {running
          ? "Matching…"
          : done && progress.remaining === 0
            ? "Run it again"
            : progress !== null
              ? "Carry on"
              : "Start matching"}
      </button>
    </section>
  );
}

/**
 * How far along the run is.
 *
 * The denominator is what has been done plus what is left, so it grows as the
 * run discovers how much there was -- there is no way to know the total before
 * the first batch comes back, and inventing one would mean a bar that jumped.
 */
function Bar({ done, left }: { done: number; left: number }) {
  const total = done + left;
  const share = total === 0 ? 1 : done / total;

  return (
    <div
      className="h-1.5 w-full bg-raised"
      role="progressbar"
      aria-valuenow={done}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-label="Titles looked up"
    >
      <div
        className="h-full bg-muted transition-[width] duration-500"
        style={{ width: `${Math.round(share * 100)}%` }}
      />
    </div>
  );
}
