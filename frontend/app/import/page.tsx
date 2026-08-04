"use client";

import { useState } from "react";
import ImportDropzone from "@/components/ImportDropzone";
import ImportInstructions from "@/components/ImportInstructions";
import ImportSummaryCard from "@/components/ImportSummaryCard";
import { apiRequest, errorMessage } from "@/lib/api";
import type { ImportSummary } from "@/lib/types";

/**
 * Getting a watch history in.
 *
 * The upload is three lines of code and the instructions are most of the page,
 * which is the right proportion: nobody has ever been stopped by a file input,
 * and plenty of people have been stopped by not knowing Netflix hides the export
 * behind a link at the bottom of a very long list.
 *
 * Uploading the same file twice is safe and is expected -- the export is
 * cumulative, so re-uploading is how you catch up -- and the summary says how
 * many rows were already held rather than treating a repeat as a mistake.
 */
export default function ImportPage() {
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
        await apiRequest<ImportSummary>("/api/imports/netflix", {
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

  return (
    <div className="max-w-3xl">
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Import your history
        </h1>
        <p className="mt-3 max-w-[65ch] text-[15px] leading-relaxed text-muted">
          Netflix will give you everything you have watched as a file. That file
          is what your taste is read from &mdash; the app works out what you like
          from what you actually finished, not from genres you ticked once.
        </p>
      </header>

      <section className="mt-10">
        <h2 className="text-base font-medium">Getting the file from Netflix</h2>
        <div className="mt-4">
          <ImportInstructions />
        </div>
      </section>

      <section className="mt-12">
        <h2 className="text-base font-medium">Upload it</h2>
        <div className="mt-4">
          <ImportDropzone onFile={(file) => void upload(file)} busy={busy} />
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
