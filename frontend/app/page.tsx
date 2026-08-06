"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import AskControls, { AskSummary } from "@/components/AskControls";
import RecommendationCard from "@/components/RecommendationCard";
import { apiRequest, errorMessage } from "@/lib/api";
import type {
  KindPreference,
  Mood,
  Recommendation,
  RecommendationRequestBody,
  WatchlistItem,
} from "@/lib/types";

/**
 * The product.
 *
 * It asks on arrival rather than waiting to be told to. Every part of the
 * question has a defensible default, so a page that loaded and then sat there
 * behind a Go button would be putting a form between somebody and the one
 * thing this app promises -- which is the decision paralysis it exists to
 * remove, wearing a different hat. The controls are for changing your mind,
 * not for earning an answer.
 *
 * Asking again inside half an hour is free: the backend treats a repeat within
 * one sitting as the same question and does not write it down twice, so a
 * refresh cannot exhaust the pool or make the app forget what it just said.
 */
export default function Home() {
  const [mood, setMood] = useState<Mood>("surprise_me");
  const [minutes, setMinutes] = useState<number | null>(null);
  const [kind, setKind] = useState<KindPreference>("any");

  const [result, setResult] = useState<Recommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [rejecting, setRejecting] = useState(false);

  // Everything turned down in this sitting. State rather than a ref because it
  // is shown as well as sent -- and it is always set in the same handler that
  // bumps `attempt`, so the effect below sees the new list on the render the
  // bump causes.
  const [rejected, setRejected] = useState<number[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);

  // Kept by title id rather than as a flag, so a re-roll cannot arrive wearing
  // the last answer's "Saved" label -- and so that turning something down and
  // being shown it again next week still remembers it was saved.
  const [saved, setSaved] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);
  // Kept apart from `error`, which stands for "there is no answer to show". A
  // watchlist that would not accept a title is no reason to take the title off
  // the screen -- it is still the answer, and it is still watchable tonight.
  const [saveError, setSaveError] = useState<string | null>(null);

  const ask = useCallback(
    (body: RecommendationRequestBody) =>
      apiRequest<Recommendation>("/api/recommend", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    [],
  );

  useEffect(() => {
    let live = true;

    ask({
      mood,
      minutes_available: minutes,
      kind,
      exclude_ids: rejected,
    })
      .then((answer) => {
        if (!live) return;
        setResult(answer);
        setError(null);
      })
      .catch((caught) => live && setError(errorMessage(caught)))
      .finally(() => {
        if (!live) return;
        setBusy(false);
        setRejecting(false);
      });

    return () => {
      live = false;
    };
    // Deliberately keyed on `attempt` alone. Changing a chip bumps it, so the
    // question is re-asked exactly once per change rather than once per field
    // that happened to update in the same render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt]);

  function change<T>(set: (value: T) => void) {
    return (value: T) => {
      set(value);
      // A different question deserves a clean slate: the titles turned down for
      // "a laugh in thirty minutes" were not turned down for "something moving
      // with no rush", and holding them against the new question would quietly
      // shrink the pool for a reason nobody could see.
      setRejected([]);
      setBusy(true);
      setAttempt((n) => n + 1);
    };
  }

  function reject() {
    const shown = result?.title?.title_id;
    if (shown !== undefined) {
      setRejected((current) => [...current, shown]);
    }
    setRejecting(true);
    setAttempt((n) => n + 1);
  }

  /**
   * Keep this one for another evening.
   *
   * Deliberately not a rejection: the title stays in play for tonight and the
   * card does not change. Saving is safe to repeat -- the backend treats a
   * second add of the same title as the same decision rather than a new entry
   * -- so pressing it after a refresh costs nothing.
   */
  async function save() {
    const shown = result?.title?.title_id;
    if (shown === undefined) return;

    setSaving(true);
    setSaveError(null);
    try {
      await apiRequest<WatchlistItem>("/api/watchlist", {
        method: "POST",
        body: JSON.stringify({ title_id: shown }),
      });
      setSaved((current) => new Set(current).add(shown));
    } catch (caught) {
      setSaveError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      {/* Collapsed on a phone until asked for, expanded always from `sm` up.
          Both branches are in the DOM and switched with a media query rather
          than by measuring the viewport, so the server and the first client
          render agree. */}
      {!expanded && (
        <AskSummary
          mood={mood}
          minutes={minutes}
          kind={kind}
          onExpand={() => setExpanded(true)}
        />
      )}

      <div className={expanded ? "block" : "hidden sm:block"}>
        <AskControls
          mood={mood}
          minutes={minutes}
          kind={kind}
          onMood={change(setMood)}
          onMinutes={change(setMinutes)}
          onKind={change(setKind)}
          disabled={busy}
        />
      </div>

      <div className="mt-8 sm:mt-10">
        {error ? (
          <Problem message={error} onRetry={() => setAttempt((n) => n + 1)} />
        ) : busy && !result ? (
          <Thinking />
        ) : result?.title ? (
          <RecommendationCard
            key={result.title.title_id}
            title={result.title}
            onReject={reject}
            onSave={() => void save()}
            rejecting={rejecting}
            // Either saved in this sitting or already waiting when it arrived.
            saved={saved.has(result.title.title_id) || result.title.on_watchlist}
            saving={saving}
          />
        ) : result ? (
          <NothingToSay result={result} turnedDown={rejected.length} />
        ) : null}

        {saveError && (
          <p role="alert" className="mt-4 text-sm text-muted">
            Could not save that: {saveError}
          </p>
        )}
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <div
      role="status"
      className="grid animate-pulse overflow-hidden border border-line bg-panel sm:grid-cols-[minmax(0,240px)_1fr]"
    >
      {/* Matches the card's poster exactly, so the answer replaces the
          placeholder rather than shunting the page when it lands. */}
      <div className="h-[38vh] max-h-[420px] min-h-[200px] bg-raised sm:h-auto sm:max-h-none sm:aspect-[2/3]" />
      <div className="space-y-4 p-6 sm:p-8">
        <div className="h-10 w-2/3 bg-raised" />
        <div className="h-4 w-1/3 bg-raised" />
        <div className="h-4 w-1/2 bg-raised" />
      </div>
      <span className="sr-only">Finding something to watch</span>
    </div>
  );
}

/**
 * No answer, which is information rather than a failure.
 *
 * The sentence comes from the backend, which knows which of four different
 * problems this is and writes it to be acted on. The counts underneath say
 * where the search collapsed, and the two links are the two things that
 * actually change the outcome.
 */
function NothingToSay({
  result,
  turnedDown,
}: {
  result: Recommendation;
  turnedDown: number;
}) {
  const { pool, available, eligible } = result.considered;

  return (
    <section className="border border-line bg-panel p-6 sm:p-8">
      <h2 className="text-xl font-medium">Nothing tonight.</h2>
      <p className="mt-3 max-w-[60ch] text-[15px] leading-relaxed text-muted">
        {result.reason ||
          "Nothing matched what you asked for. Try a different mood."}
      </p>

      <dl className="mt-6 flex flex-wrap gap-x-8 gap-y-3 border-t border-line pt-5 text-sm">
        <Count label="In the pool" value={pool} />
        <Count label="You can watch" value={available} />
        <Count label="Fit the question" value={eligible} />
        {turnedDown > 0 && <Count label="Turned down" value={turnedDown} />}
      </dl>

      <div className="mt-6 flex flex-wrap gap-x-4 gap-y-2 text-sm">
        <Link
          href="/settings"
          className="border border-edge px-3.5 py-1.5 transition-colors hover:border-white hover:bg-raised"
        >
          Pick your services
        </Link>
        <Link
          href="/import"
          className="border border-edge px-3.5 py-1.5 transition-colors hover:border-white hover:bg-raised"
        >
          Import more history
        </Link>
      </div>
    </section>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-dim">{label}</dt>
      <dd className="mt-0.5 text-lg tabular-nums">{value.toLocaleString()}</dd>
    </div>
  );
}

function Problem({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="border border-line bg-panel p-6">
      <p className="text-[15px]">Could not get a recommendation</p>
      <p className="mt-1.5 max-w-[60ch] text-sm leading-relaxed text-muted">
        {message}
      </p>
      <button
        onClick={onRetry}
        className="mt-4 border border-edge px-3.5 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised"
      >
        Try again
      </button>
    </div>
  );
}
