"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import BarList from "@/components/BarList";
import MonthBars from "@/components/MonthBars";
import StatTile from "@/components/StatTile";
import { apiRequest, errorMessage } from "@/lib/api";
import { count, monthLabel, watchTime } from "@/lib/format";
import type { Stats, TopTitle } from "@/lib/types";

/**
 * What the history adds up to.
 *
 * A description of somebody's viewing, and deliberately not a scoreboard. The
 * temptation on a page like this is a wall of tiles, because the data supports
 * one and every number looks like an achievement -- but this app's whole
 * argument is that a wall of things to read is the problem. So each panel here
 * has to answer a question somebody would actually ask: what do I watch, when
 * do I watch it, how old is it, what did I go back to.
 *
 * Nothing here is ranked by anything the reader cannot see. Every bar carries
 * its own number, every chart has its values in text underneath, and the two
 * figures that rest on part of the history say so rather than presenting
 * themselves as totals.
 *
 * The one panel that is allowed to be absent is what somebody came back to. A
 * "most watched" list padded out with films watched exactly once is the vanity
 * wall this page is trying not to be, so it draws only what was returned to and
 * says plainly when that is nothing.
 */
export default function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;

    apiRequest<Stats>("/api/stats")
      .then((found) => {
        if (!live) return;
        setStats(found);
        setError(null);
      })
      .catch((caught) => live && setError(errorMessage(caught)));

    return () => {
      live = false;
    };
  }, [attempt]);

  const history = stats?.history;
  const youtube = stats?.youtube;
  const returnedTo = history?.top_titles.filter((entry) => entry.sessions > 1) ?? [];
  const nothingAtAll =
    stats !== null &&
    stats.history.sessions === 0 &&
    stats.youtube.views === 0 &&
    stats.unresolved_sessions === 0;

  return (
    <div>
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Your viewing
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          {stats === null ? "What your history adds up to." : summarise(stats)}
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

      {stats === null && !error && <Skeleton />}

      {nothingAtAll && <Empty />}

      {stats !== null && stats.unresolved_sessions > 0 && (
        <Unresolved
          sessions={stats.unresolved_sessions}
          nothingMatched={stats.history.sessions === 0}
        />
      )}

      {history && history.sessions > 0 && (
        // The spacing between these blocks is owned here rather than by
        // each panel. A margin on a child adds to the gap of any grid it
        // sits in, which is how the two side-by-side panels below ended up
        // 32px apart on a phone while every other pair on the page was 16.
        <section className="mt-8 space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <StatTile
              label="Titles"
              value={history.titles.toLocaleString()}
              // "series" is already its own plural, so it does not go
              // through the naive pluraliser.
              hint={`${count(history.movies, "film")}, ${history.series} series`}
            />
            <StatTile
              label="Times you sat down"
              value={history.sessions.toLocaleString()}
              hint="An episode is a sitting. A binge is one title and many of these."
            />
            <StatTile
              wide
              label="Time watched"
              value={
                history.minutes_watched === null
                  ? "—"
                  : watchTime(history.minutes_watched)
              }
              hint={timeHint(
                history.minutes_watched,
                history.sessions_timed,
                history.sessions,
              )}
            />
          </div>

          <Panel title="When you watched">
            <MonthBars entries={history.by_month} unit="sitting" />
          </Panel>

          <div className="grid gap-4 sm:grid-cols-2">
            {history.top_genres.length > 0 && (
              <Panel title="What you watch" note="Counted once per title.">
                <BarList entries={history.top_genres} unit="title" />
              </Panel>
            )}
            {history.decades.length > 0 && (
              <Panel title="When it was made">
                <BarList entries={history.decades} unit="title" />
              </Panel>
            )}
          </div>

          <Panel title="What you came back to">
            {returnedTo.length > 0 ? (
              <ul className="space-y-2.5">
                {returnedTo.map((entry) => (
                  <li
                    key={entry.title_id}
                    className="flex items-baseline justify-between gap-4 border-b border-line/60 pb-2.5 last:border-0 last:pb-0"
                  >
                    <span className="text-sm">{entry.title}</span>
                    <span className="shrink-0 text-sm text-dim">
                      {returns(entry)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">
                Nothing yet — everything here was watched once. This fills up with
                series you worked through and films you went back to.
              </p>
            )}
          </Panel>
        </section>
      )}

      {youtube && youtube.views > 0 && (
        // Set apart by a rule and the ordinary section gap rather than by a
        // bigger number than anything else here uses. The separation is the
        // point -- YouTube is a different kind of thing, and never a
        // recommendation -- but a magic 48px said so in a language nothing
        // else on this page speaks.
        <section className="mt-8 border-t border-line pt-8">
          <h2 className="text-lg font-medium">YouTube</h2>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">
            Kept apart from the rest on purpose. This is a signal about what you
            like, never something the app will tell you to watch — nobody needs an
            app to suggest YouTube.
          </p>

          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <StatTile
              label="Videos watched"
              value={youtube.views.toLocaleString()}
              hint="Counting every time, not every video."
            />
            <StatTile
              label="Different videos"
              value={youtube.videos.toLocaleString()}
              hint={rewatchHint(youtube.views, youtube.videos)}
            />
            <StatTile
              wide
              label="Channels"
              value={youtube.channels.toLocaleString()}
            />
          </div>

          <div className="mt-4 space-y-4">
            {youtube.top_channels.length > 0 && (
              <Panel title="Who you watch">
                <BarList entries={youtube.top_channels} unit="view" />
              </Panel>
            )}

            <Panel title="When you watched it">
              <MonthBars entries={youtube.by_month} unit="view" />
            </Panel>
          </div>
        </section>
      )}
    </div>
  );
}

function Panel({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border border-line bg-panel px-4 py-5 sm:px-5">
      <div className="mb-4 flex items-baseline justify-between gap-3">
        <h2 className="text-base font-medium">{title}</h2>
        {note && <p className="text-xs text-dim">{note}</p>}
      </div>
      {children}
    </section>
  );
}

/** The header line: the span of the history, which no tile shows. */
function summarise(stats: Stats): string {
  const { history, youtube } = stats;
  if (history.sessions === 0 && youtube.views === 0) {
    return "What your history adds up to.";
  }

  const from = history.first_watched ?? youtube.first_watched;
  const to = history.last_watched ?? youtube.last_watched;
  if (!from || !to) {
    return "What your history adds up to.";
  }

  const span =
    monthLabel(from) === monthLabel(to)
      ? monthLabel(from)
      : `${monthLabel(from)} to ${monthLabel(to)}`;
  return `Everything imported so far, ${span}.`;
}

/**
 * What the time figure rests on.
 *
 * A total assembled from part of the history is a lower bound, and saying so is
 * the difference between a statistic and a claim. Netflix records how long each
 * session ran; nothing else does.
 */
function timeHint(
  minutes: number | null,
  timed: number,
  sessions: number,
): string {
  if (minutes === null) {
    return "Nothing in this history recorded how long it ran.";
  }
  if (timed < sessions) {
    return `Measured on ${timed.toLocaleString()} of ${sessions.toLocaleString()} sittings, so at least this much.`;
  }
  return "Measured, not worked out from runtimes.";
}

function rewatchHint(views: number, videos: number): string | undefined {
  if (views <= videos) return undefined;
  const average = (views / videos).toFixed(1);
  return `About ${average} views each — how often you go back is most of what this says.`;
}

/** Twelve sittings means two different things, so it is worded two ways. */
function returns(entry: TopTitle): string {
  return entry.object_type === "SHOW"
    ? count(entry.sessions, "episode")
    : `watched ${entry.sessions} times`;
}

function Unresolved({
  sessions,
  nothingMatched,
}: {
  sessions: number;
  nothingMatched: boolean;
}) {
  return (
    <div className="mt-8 border border-edge bg-panel px-4 py-5 sm:px-5">
      <p className="text-[15px]">
        {nothingMatched
          ? `${count(sessions, "sitting")} imported, and none of them are matched to a title yet.`
          : `${count(sessions, "sitting")} could not be matched to a title.`}
      </p>
      <p className="mt-2 max-w-xl text-sm leading-relaxed text-muted">
        {nothingMatched
          ? "Until they are matched there is nothing to count: the genres, runtimes and dates all come from the catalogue rather than from the export."
          : "They are in none of the numbers above, so this page is describing slightly less than you have watched. The rest were matched to the catalogue and are counted."}
      </p>
      <Link
        href="/resolve"
        className="mt-4 inline-block bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90"
      >
        {nothingMatched ? "Match them" : "Sort them out"}
      </Link>
    </div>
  );
}

function Empty() {
  return (
    <div className="mt-8 border border-line bg-panel px-5 py-8 text-center">
      <p className="text-[15px]">Nothing imported yet.</p>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
        Netflix and YouTube both let you download your own history. Drop the file
        in and this page fills itself in — what you watch, when you watch it, and
        what you keep going back to.
      </p>
      <Link
        href="/import"
        className="mt-5 inline-block bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90"
      >
        Import a history
      </Link>
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden className="mt-8 space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {Array.from({ length: 3 }, (_, index) => (
          <div
            key={index}
            className="h-[104px] animate-pulse border border-line bg-panel"
          />
        ))}
      </div>
      <div className="h-[232px] animate-pulse border border-line bg-panel" />
    </div>
  );
}
