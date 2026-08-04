"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest, errorMessage } from "@/lib/api";
import type { Subscriptions } from "@/lib/types";

/**
 * The front door, in the state it is in before there is anything to recommend.
 *
 * This is not a placeholder for the recommend page -- it is that page's empty
 * state, built first. The reveal needs a library to draw from and a list of
 * services to filter against, and somebody arriving with neither should be told
 * exactly what is missing rather than shown an apologetic blank.
 */
export default function Home() {
  const [subscriptions, setSubscriptions] = useState<Subscriptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Bumped to ask for a reload. The effect body starts the request and nothing
  // else: every write lands in a callback, so the mount does not cascade a
  // second render before the answer is even back.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;

    apiRequest<Subscriptions>("/api/providers/mine")
      .then((mine) => live && setSubscriptions(mine))
      .catch((caught) => live && setError(errorMessage(caught)))
      .finally(() => live && setLoading(false));

    return () => {
      live = false;
    };
  }, [attempt]);

  function retry() {
    setLoading(true);
    setError(null);
    setAttempt((n) => n + 1);
  }

  const picked = subscriptions?.short_names.length ?? 0;

  return (
    // Centred rather than flush left, unlike every other page. This one is
    // mostly empty by nature -- there is nothing to recommend yet -- and a
    // narrow column of copy pinned to the left of a wide screen reads as a
    // layout that broke rather than one that is waiting.
    <div className="mx-auto max-w-2xl">
      <h1 className="text-3xl font-medium tracking-[-0.03em] text-balance sm:text-4xl">
        One thing to watch tonight.
      </h1>
      <p className="mt-4 text-[15px] leading-relaxed text-muted">
        Not a feed to scroll. One title, chosen from what you have actually
        watched, filtered to the services you actually pay for. Two things have
        to happen first.
      </p>

      <ol className="mt-10 border-t border-line">
        <SetupStep
          number={1}
          title="Bring in your watch history"
          href="/import"
          action="Import"
        >
          Netflix will hand you a CSV of everything you have watched. That is
          what the taste profile is built from &mdash; nothing here is guessed
          from a genre you ticked once.
        </SetupStep>

        <SetupStep
          number={2}
          title="Say which services you have"
          href="/settings"
          action={picked ? "Change" : "Pick services"}
          status={
            loading
              ? "Checking…"
              : error
                ? null
                : picked
                  ? `${picked} ${picked === 1 ? "service" : "services"} picked`
                  : "Nothing picked yet"
          }
        >
          The hard filter. Nothing is ever recommended that you would have to
          subscribe to something new to watch &mdash; so with nothing picked,
          nothing qualifies.
        </SetupStep>
      </ol>

      {error && (
        <div
          role="alert"
          className="mt-8 border border-line bg-panel px-4 py-3 text-sm"
        >
          <p className="text-white">{error}</p>
          <button
            onClick={retry}
            className="mt-2 text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            Try again
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * One step, as a row rather than a card.
 *
 * Numbered because the order is real: there is nothing to filter until history
 * has been imported, so doing these the other way round leaves somebody staring
 * at a picker that changes nothing they can see.
 */
function SetupStep({
  number,
  title,
  href,
  action,
  status,
  children,
}: {
  number: number;
  title: string;
  href: string;
  action: string;
  status?: string | null;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-4 border-b border-line py-6 sm:gap-6">
      <span
        aria-hidden
        className="mt-0.5 font-mono text-sm text-dim tabular-nums"
      >
        {number}
      </span>

      <div className="min-w-0 flex-1">
        <h2 className="text-base font-medium">{title}</h2>
        <p className="mt-1.5 text-sm leading-relaxed text-muted">{children}</p>

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <Link
            href={href}
            className="border border-edge px-3.5 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised"
          >
            {action}
          </Link>
          {status && <span className="text-sm text-dim">{status}</span>}
        </div>
      </div>
    </li>
  );
}
