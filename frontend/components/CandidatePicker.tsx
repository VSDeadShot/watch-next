"use client";

import { useId, useState } from "react";
import { apiRequest, errorMessage } from "@/lib/api";
import type { CatalogueSearchBody, TitleCandidate } from "@/lib/types";

/**
 * Choosing which catalogue entry a title actually is.
 *
 * Two ways to answer, in the order they are worth trying. The stored
 * candidates come first and cost nothing: they are what the matcher already
 * weighed and rejected, and the commonest refusal is one where the right answer
 * was in that list but no single option beat the runner-up by enough. Searching
 * is the way out of the other case -- a title misspelled in the export, or one
 * known by a different name here -- which the matcher could never have got
 * right, because it only ever had the exported spelling to go on.
 *
 * **The search runs when it is asked to, never as somebody types.** Every
 * search is a real request against an unofficial API that we pace at one a
 * second by choice, and search-as-you-type would spend a request per keystroke
 * to answer a question nobody had finished asking.
 *
 * The kind filter starts on whatever the row was parsed as and can be turned
 * off, because the parser's reading is itself one of the reasons a row lands
 * here -- a two-part title with no season marker is guessed to be a film, and a
 * filter built on that guess would hide every series it got wrong.
 *
 * **The field is folded away when there are candidates, and open when there are
 * not.** An empty input, a button and a checkbox outweigh two small chips
 * however the chips are styled, so leaving the search standing made the
 * expensive path look like the intended one on every row in the queue. Folding
 * it costs one click on the rarer case and nothing on the commoner one. The
 * exception is the row the matcher found nothing for: there are no chips to
 * choose from, searching is the only thing left, and hiding the only available
 * action behind a disclosure would be a worse lie than the one this fixes.
 */
export default function CandidatePicker({
  candidates,
  kind,
  onPick,
  busy,
  picked,
}: {
  candidates: TitleCandidate[];
  /** The row's parsed kind, `"movie"` or `"episode"`. */
  kind: string;
  onPick: (nodeId: string) => void;
  busy: boolean;
  /** Already the answer, so it can be shown as the current one. */
  picked?: string;
}) {
  const fieldId = useId();
  // No candidates means the matcher came back with nothing, so the search is
  // not the fallback here -- it is the whole row.
  const [open, setOpen] = useState(candidates.length === 0);
  const [query, setQuery] = useState("");
  const [narrow, setNarrow] = useState(true);
  const [results, setResults] = useState<TitleCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    const wanted = query.trim();
    if (wanted.length < 2) return;

    setSearching(true);
    setError(null);
    try {
      // The term goes in the body, which is why a read is a POST here: what
      // gets typed into this box is a title somebody watched, and a query
      // string is copied into an access log by everything it passes through.
      const body: CatalogueSearchBody = narrow
        ? { q: wanted, kind }
        : { q: wanted };
      const found = await apiRequest<TitleCandidate[]>("/api/titles/search", {
        method: "POST",
        body: JSON.stringify(body),
      });
      setResults(found);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSearching(false);
    }
  }

  return (
    // Set apart from the row's description above it: everything higher up says
    // what this title is, and everything from here down is the answer.
    <div className="mt-5">
      {candidates.length > 0 && (
        <Options
          heading="What the matcher was choosing between"
          options={candidates}
          onPick={onPick}
          busy={busy}
          picked={picked}
        />
      )}

      {!open ? (
        // A quiet text action rather than a button, because this is the second
        // thing to try and the app says "second thing to try" with an
        // underline everywhere else.
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-4 text-sm text-muted underline underline-offset-4 transition-colors hover:text-white"
        >
          None of these? Search by name
        </button>
      ) : (
        <form
          onSubmit={search}
          // Capped, because a title is a few words and a field the width of the
          // page invites the belief that something longer was wanted.
          className="mt-4 flex max-w-lg flex-wrap items-center gap-2"
        >
          <label htmlFor={fieldId} className="sr-only">
            Search the catalogue
          </label>
          <input
            id={fieldId}
            type="search"
            value={query}
            // Mirrors `SEARCH_TERM_LIMIT`, so the field stops where the API
            // does instead of letting somebody type into a 422.
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Another name"
            autoFocus={candidates.length > 0}
            // A floor rather than `min-w-0`: with nothing to stop it the field
            // shrank instead of wrapping, and at 320px it was 68 pixels wide
            // and showing "Ano". Below that width the checkbox wraps to its
            // own line, which is what the wrapping was there for.
            className="min-w-[10rem] flex-1 border border-line bg-raised px-3 py-2 text-sm placeholder:text-dim focus:border-edge focus:outline-none"
          />
          <button
            type="submit"
            disabled={searching || query.trim().length < 2}
            className="border border-edge px-3 py-2 text-sm transition-colors hover:border-white hover:bg-raised disabled:opacity-50"
          >
            {searching ? "Searching…" : "Search"}
          </button>
          <label className="flex items-center gap-2 text-xs text-dim">
            <input
              type="checkbox"
              checked={narrow}
              onChange={(event) => setNarrow(event.target.checked)}
              className="accent-white"
            />
            {kind === "episode" ? "Series only" : "Films only"}
          </label>
        </form>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-muted">
          {error}
        </p>
      )}

      {results !== null && !error && (
        <div className="mt-3">
          {results.length === 0 ? (
            <p className="text-sm text-muted">
              Nothing under that name.{" "}
              {narrow
                ? "Try again with the filter off — the export's idea of what this is may be wrong."
                : "Try a different spelling."}
            </p>
          ) : (
            <Options
              heading="From the catalogue"
              options={results}
              onPick={onPick}
              busy={busy}
              picked={picked}
            />
          )}
        </div>
      )}
    </div>
  );
}

function Options({
  heading,
  options,
  onPick,
  busy,
  picked,
}: {
  heading: string;
  options: TitleCandidate[];
  onPick: (nodeId: string) => void;
  busy: boolean;
  picked?: string;
}) {
  return (
    <div>
      <p className="text-xs text-dim">{heading}</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => {
          const current = option.node_id === picked;
          return (
            <li key={option.node_id}>
              <button
                type="button"
                disabled={busy || current}
                onClick={() => onPick(option.node_id)}
                aria-current={current ? "true" : undefined}
                className={`border px-3 py-1.5 text-sm transition-colors disabled:opacity-50 ${
                  current
                    ? "border-edge bg-raised text-white"
                    : "border-line hover:border-edge hover:bg-raised"
                }`}
              >
                {option.title}
                {option.release_year !== null && (
                  <span className="text-dim"> {option.release_year}</span>
                )}
                <span className="text-dim">
                  {" "}
                  · {option.object_type === "SHOW" ? "series" : "film"}
                </span>
                {current && <span className="text-dim"> · chosen</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
