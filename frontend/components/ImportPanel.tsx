"use client";

import { useState } from "react";
import ImportDropzone from "@/components/ImportDropzone";
import ImportInstructions, {
  NETFLIX_ROUTES,
  YOUTUBE_ROUTES,
  type Route,
} from "@/components/ImportInstructions";
import ImportSummaryCard from "@/components/ImportSummaryCard";
import { apiRequest, errorMessage } from "@/lib/api";
import { megabytes } from "@/lib/limits";
import type { ImportSummary } from "@/lib/types";

/**
 * Getting a watch history in.
 *
 * The upload is three lines of code and the instructions are most of the page,
 * which is the right proportion: nobody has ever been stopped by a file input,
 * and plenty of people have been stopped by not knowing Netflix hides the export
 * behind a link at the bottom of a very long list, or that Takeout gives you
 * HTML unless you go and change it.
 *
 * Uploading the same file twice is safe and is expected -- both exports are
 * cumulative, so re-uploading is how you catch up -- and the summary says how
 * many rows were already held rather than treating a repeat as a mistake.
 *
 * Rendered by `app/import/page.tsx`, which is a server component for one
 * reason: `limit` is a fact about the host rather than about this app, and the
 * server is the only side that can read it. See `lib/limits.ts`.
 */
const SOURCES = [
  {
    id: "netflix",
    label: "Netflix",
    endpoint: "/api/imports/netflix",
    routes: NETFLIX_ROUTES,
    heading: "Getting the file from Netflix",
    lede: (
      <>
        Netflix will give you everything you have watched as a file. That file is
        what your taste is read from &mdash; the app works out what you like from
        what you actually finished, not from genres you ticked once.
      </>
    ),
    accept: ".csv,.zip",
    hint: "The .zip exactly as Netflix sent it, or the .csv on its own",
  },
  {
    id: "youtube",
    label: "YouTube",
    endpoint: "/api/imports/youtube",
    routes: YOUTUBE_ROUTES,
    heading: "Getting the file from Google Takeout",
    lede: (
      <>
        YouTube history sharpens what the app knows about you, but it never
        becomes a recommendation &mdash; nobody needs an app to tell them to watch
        YouTube. It is read as a signal and kept separate from everything that
        can be suggested.
      </>
    ),
    accept: ".json",
    hint: "watch-history.json, unzipped from the Takeout archive",
  },
] as const;

type Source = (typeof SOURCES)[number];

/** Why nothing was imported. The heading is carried with the detail because the
 *  two failures are not the same event: one file was read and could not be
 *  understood, the other was never sent at all. */
type Problem = { heading: string; detail: string };

export default function ImportPanel({
  /** The largest upload the host in front of this app will carry, or null if
   *  nothing the frontend knows about would stop one. */
  limit,
}: {
  limit: number | null;
}) {
  const [source, setSource] = useState<Source>(SOURCES[0]);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File) {
    // Refused here rather than sent and refused there, because "there" is the
    // platform and not this app: it answers 413 with a plain-text page before
    // any of our code runs, so the alternative to this message is the bare
    // line "413 Request Entity Too Large" and no idea what to do about it.
    if (limit !== null && file.size > limit) {
      setSummary(null);
      setProblem({
        heading: "That file is too big to send from here",
        detail:
          `It is ${megabytes(file.size)}, and the host this app is deployed ` +
          `on refuses any upload over ${megabytes(limit)} before it reaches ` +
          `the app at all. Nothing was sent. ` +
          (source.id === "netflix"
            ? "ViewingActivity.csv on its own is usually small enough — try " +
              "that rather than the whole zip."
            : "A full watch-history.json is usually well over that, and there " +
              "is no smaller version of it to send."),
      });
      return;
    }

    setBusy(true);
    setProblem(null);
    setSummary(null);

    const body = new FormData();
    body.append("file", file);

    try {
      setSummary(
        await apiRequest<ImportSummary>(source.endpoint, {
          method: "POST",
          body,
        }),
      );
    } catch (caught) {
      setProblem({
        heading: "Could not read that file",
        detail: errorMessage(caught),
      });
    } finally {
      setBusy(false);
    }
  }

  function choose(next: Source) {
    // A summary belongs to the file it described. Leaving a Netflix result
    // sitting under YouTube instructions would read as though it had just
    // happened.
    setSource(next);
    setSummary(null);
    setProblem(null);
  }

  return (
    <div className="max-w-3xl">
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Import your history
        </h1>
        <p className="mt-3 max-w-[65ch] text-[15px] leading-relaxed text-muted">
          {source.lede}
        </p>
      </header>

      <div
        role="tablist"
        aria-label="Where your history is coming from"
        className="mt-8 flex gap-1 border-b border-line"
      >
        {SOURCES.map((option) => {
          const selected = option.id === source.id;
          return (
            <button
              key={option.id}
              role="tab"
              type="button"
              aria-selected={selected}
              onClick={() => choose(option)}
              className={`-mb-px border-b px-4 py-2.5 text-sm transition-colors ${
                selected
                  ? "border-white text-white"
                  : "border-transparent text-muted hover:text-white"
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>

      <section className="mt-10">
        <h2 className="text-base font-medium">{source.heading}</h2>
        <div className="mt-4">
          {/* Keyed on the source so the route tabs inside reset rather than
              holding a selection that belongs to the other service. */}
          <ImportInstructions
            key={source.id}
            routes={source.routes as readonly Route[]}
            label={`Ways to get your ${source.label} history`}
          />
        </div>
      </section>

      <section className="mt-12">
        <h2 className="text-base font-medium">Upload it</h2>
        <div className="mt-4">
          <ImportDropzone
            onFile={(file) => void upload(file)}
            busy={busy}
            accept={source.accept}
            hint={source.hint}
            limit={limit}
          />
        </div>

        {problem && (
          <div
            role="alert"
            className="mt-4 border border-line bg-panel px-4 py-3 text-sm"
          >
            <p className="text-white">{problem.heading}</p>
            <p className="mt-1 max-w-[65ch] leading-relaxed text-muted">
              {problem.detail}
            </p>
          </div>
        )}

        {summary && (
          <div className="mt-6">
            <ImportSummaryCard
              summary={summary}
              onAgain={() => setSummary(null)}
            />
          </div>
        )}

        {/* Said "never leaves your machine beyond the local backend", which
            stopped being true the day this was deployed -- the file goes to the
            backend on Render. The true half is worth keeping and the false half
            was the more reassuring one, which is exactly why it had to go. */}
        <p className="mt-4 max-w-[65ch] text-sm leading-relaxed text-dim">
          Your file is read once and not kept &mdash; only the rows parsed out
          of it are stored. Safe to upload the same export again later &mdash;
          rows already held are counted, not duplicated.
        </p>
      </section>
    </div>
  );
}
