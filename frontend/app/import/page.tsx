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

export default function ImportPage() {
  const [source, setSource] = useState<Source>(SOURCES[0]);
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
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
      setError(errorMessage(caught));
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
    setError(null);
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
          />
        </div>

        {error && (
          <div
            role="alert"
            className="mt-4 border border-line bg-panel px-4 py-3 text-sm"
          >
            <p className="text-white">Could not read that file</p>
            <p className="mt-1 max-w-[65ch] leading-relaxed text-muted">
              {error}
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

        <p className="mt-4 max-w-[65ch] text-sm leading-relaxed text-dim">
          Your file is read once and never leaves your machine beyond the local
          backend. Safe to upload the same export again later &mdash; rows
          already held are counted, not duplicated.
        </p>
      </section>
    </div>
  );
}
