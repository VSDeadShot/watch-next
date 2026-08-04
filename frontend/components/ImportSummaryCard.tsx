"use client";

import Link from "next/link";
import type { ImportSummary } from "@/lib/types";

/**
 * What the upload did, in numbers that add up.
 *
 * `imported + duplicates + skipped` equals the row count of the file, and the
 * layout says so on purpose. An importer that quietly drops rows is one nobody
 * should trust with their history, so every row is accounted for and every
 * exclusion is named -- including the boring ones.
 */
const SKIP_REASONS: Record<string, string> = {
  supplemental_video: "trailers and recaps",
  too_short: "watched for under a minute",
  missing_title: "no title in the row",
  bad_timestamp: "unreadable date",
};

export default function ImportSummaryCard({
  summary,
  onAgain,
}: {
  summary: ImportSummary;
  onAgain: () => void;
}) {
  const nothingNew = summary.imported === 0 && summary.duplicates > 0;

  return (
    <section className="border border-line bg-panel">
      <header className="border-b border-line px-5 py-4">
        <h2 className="text-base font-medium">
          {nothingNew ? "Already had all of that" : "History imported"}
        </h2>
        <p className="mt-1 text-sm text-muted">
          {summary.filename ?? "Your export"} &middot;{" "}
          {summary.export_format === "full"
            ? "the complete export"
            : "titles and dates"}
        </p>
      </header>

      <dl className="grid grid-cols-2 divide-line sm:grid-cols-4 sm:divide-x">
        <Figure
          label="Rows in the file"
          value={summary.total_rows}
          tone="muted"
        />
        <Figure label="Added" value={summary.imported} tone="loud" />
        <Figure label="Already had" value={summary.duplicates} tone="muted" />
        <Figure label="Skipped" value={summary.skipped} tone="muted" />
      </dl>

      {(summary.skipped > 0 || summary.assumptions.length > 0) && (
        <div className="space-y-3 border-t border-line px-5 py-4 text-sm">
          {summary.skipped > 0 && (
            <p className="text-muted">
              <span className="text-white">Skipped:</span>{" "}
              {describeSkips(summary.skipped_by_reason)}.
            </p>
          )}
          {summary.assumptions.map((assumption) => (
            <p key={assumption} className="text-muted">
              <span className="text-white">Assumed:</span> {assumption}
            </p>
          ))}
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line px-5 py-4">
        <Link
          href="/settings"
          className="bg-white px-4 py-1.5 text-sm font-medium text-ink transition-opacity hover:opacity-90"
        >
          Pick your services
        </Link>
        <button
          type="button"
          onClick={onAgain}
          className="text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
        >
          Import another file
        </button>
      </footer>
    </section>
  );
}

function Figure({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "loud" | "muted";
}) {
  return (
    <div className="border-t border-line px-5 py-4 first:border-t-0 sm:border-t-0">
      <dt className="text-sm text-dim">{label}</dt>
      <dd
        className={`mt-1 text-2xl tabular-nums ${
          tone === "loud" ? "text-white" : "text-muted"
        }`}
      >
        {value.toLocaleString()}
      </dd>
    </div>
  );
}

function describeSkips(byReason: Record<string, number>): string {
  const parts = Object.entries(byReason).map(
    ([reason, count]) => `${count} ${SKIP_REASONS[reason] ?? reason}`,
  );
  return parts.length ? parts.join(", ") : "no reason recorded";
}
