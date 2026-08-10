"use client";

import { useEffect, useMemo, useState } from "react";
import AvailabilityRunner from "@/components/AvailabilityRunner";
import ProviderGrid from "@/components/ProviderGrid";
import { apiRequest, errorMessage } from "@/lib/api";
import type { ProviderCatalogue, ProviderRefresh, Subscriptions } from "@/lib/types";

/**
 * Which streaming services the user actually has.
 *
 * The most consequential page in the app and the least eventful, which is the
 * right way round. Everything here feeds one hard filter: a title is only ever
 * recommended if it is streaming on something in this list, or free to
 * everybody. Get it wrong and the app either recommends nothing or recommends
 * things you cannot watch.
 *
 * Saving is explicit. The backend replaces the whole set in one PUT, so a save
 * per click would mean a burst of requests that each rewrite everything, and
 * one failure part-way through a series of toggles would leave the stored
 * settings somewhere nobody chose.
 */
export default function SettingsPage() {
  const [catalogue, setCatalogue] = useState<ProviderCatalogue | null>(null);
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // Bumped to ask for a reload. The effect body starts the two requests and
  // nothing else: every write lands in a callback, so the mount does not
  // cascade a second render before either answer is back.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;

    // Both at once. They are independent, and the page cannot show a picker
    // without the catalogue or a selection without the subscriptions, so
    // waiting for them in series would only make it slower to become useful.
    Promise.all([
      apiRequest<ProviderCatalogue>("/api/providers"),
      apiRequest<Subscriptions>("/api/providers/mine"),
    ])
      .then(([listed, mine]) => {
        if (!live) return;
        setCatalogue(listed);
        setSaved(new Set(mine.short_names));
        setPicked(new Set(mine.short_names));
      })
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

  const changed = useMemo(
    () =>
      picked.size !== saved.size ||
      [...picked].some((name) => !saved.has(name)),
    [picked, saved],
  );

  function toggle(shortName: string) {
    setNote(null);
    setPicked((current) => {
      const next = new Set(current);
      if (!next.delete(shortName)) {
        next.add(shortName);
      }
      return next;
    });
  }

  async function refresh() {
    setRefreshing(true);
    setError(null);
    setNote(null);
    try {
      const summary = await apiRequest<ProviderRefresh>(
        "/api/providers/refresh",
        { method: "POST" },
      );
      const listed = await apiRequest<ProviderCatalogue>("/api/providers");
      setCatalogue(listed);
      setNote(describeRefresh(summary));
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setRefreshing(false);
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const stored = await apiRequest<Subscriptions>("/api/providers/mine", {
        method: "PUT",
        body: JSON.stringify({ short_names: [...picked] }),
      });
      // Trust what came back rather than what was sent: the backend sorts and
      // deduplicates, and the saved set is what "unchanged" is measured against.
      setSaved(new Set(stored.short_names));
      setPicked(new Set(stored.short_names));
      setNote("Saved.");
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  const providers = useMemo(() => catalogue?.providers ?? [], [catalogue]);

  // JustWatch lists over a hundred services for a country, most of them
  // channels nobody has heard of, and they arrive alphabetically -- so the
  // first screenful is obscure Amazon add-ons and Netflix is somewhere past the
  // fold. Without a filter this picker is technically complete and practically
  // unusable.
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return providers;
    return providers.filter((provider) =>
      provider.name.trim().toLowerCase().includes(needle),
    );
  }, [providers, query]);

  return (
    <div className="pb-24">
      <header>
        <h1 className="text-2xl font-medium tracking-[-0.02em] sm:text-3xl">
          Settings
        </h1>
        <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-muted">
          Which services you have decides everything you get recommended.
          Nothing is ever suggested that you would have to subscribe to
          something new to watch.
        </p>
      </header>

      <section className="mt-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-b border-line pb-3">
          <h2 className="text-base font-medium">
            Your services
            {catalogue && (
              <span className="ml-2 font-normal text-dim">
                in {catalogue.country}
              </span>
            )}
          </h2>
          <p className="text-sm text-dim">
            {picked.size} of {providers.length} picked
          </p>
        </div>

        {loading ? (
          <SkeletonGrid />
        ) : providers.length === 0 ? (
          <EmptyCatalogue onRefresh={() => void refresh()} busy={refreshing} />
        ) : (
          <>
            <div className="mt-5">
              <label htmlFor="provider-search" className="sr-only">
                Search services
              </label>
              <input
                id="provider-search"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search — Netflix, Prime, JioHotstar…"
                className="w-full border border-line bg-panel px-3.5 py-2.5 text-sm text-white placeholder:text-dim focus:border-edge focus:outline-none sm:max-w-sm"
              />
            </div>

            <div className="mt-4">
              {shown.length === 0 ? (
                <p className="border border-line bg-panel px-4 py-6 text-center text-sm text-muted">
                  Nothing here matches &ldquo;{query.trim()}&rdquo;. It may not
                  be available in {catalogue?.country}.
                </p>
              ) : (
                <ProviderGrid
                  providers={shown}
                  selected={picked}
                  onToggle={toggle}
                />
              )}
            </div>
          </>
        )}

        {providers.length > 0 && (
          <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-line pt-5">
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={refreshing}
              className="border border-edge px-3.5 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50"
            >
              {refreshing ? "Refreshing…" : "Refresh the list"}
            </button>
            <p className="max-w-md text-sm text-dim">
              Ask JustWatch which services exist in {catalogue?.country} again.
              Worth doing a couple of times a year, not weekly.
            </p>
          </div>
        )}
      </section>

      {note && !error && (
        <p role="status" className="mt-6 text-sm text-muted">
          {note}
        </p>
      )}

      {error && (
        <div
          role="alert"
          className="mt-6 border border-line bg-panel px-4 py-3 text-sm"
        >
          <p>{error}</p>
          <button
            onClick={retry}
            className="mt-2 text-muted underline underline-offset-4 transition-colors hover:text-white"
          >
            Try again
          </button>
        </div>
      )}

      {/* Below the picker rather than on its own page, because it is the same
          subject read the other way round: that section decides which services
          count, this one decides whether what we believe about them is still
          true. Both feed the one hard filter, and neither is worth a route in a
          nav that was already at its measured width. */}
      <AvailabilityRunner />

      {changed && (
        <SaveBar
          count={picked.size}
          saving={saving}
          onSave={() => void save()}
          onDiscard={() => setPicked(new Set(saved))}
        />
      )}
    </div>
  );
}

/**
 * Only on screen when there is something to save.
 *
 * A bar that is always there is a bar nobody reads; one that appears the moment
 * a tile is toggled is the reminder that the change has not been stored yet.
 */
function SaveBar({
  count,
  saving,
  onSave,
  onDiscard,
}: {
  count: number;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/95 backdrop-blur-sm">
      <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-3 px-5 py-3.5 sm:px-8">
        <p className="text-sm text-muted">
          {count === 0
            ? "No services picked — nothing will qualify."
            : `${count} ${count === 1 ? "service" : "services"} selected, not saved yet.`}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onDiscard}
            disabled={saving}
            className="px-3 py-1.5 text-sm text-muted transition-colors hover:text-white disabled:opacity-50"
          >
            Discard
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving}
            className="bg-white px-4 py-1.5 text-sm font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function EmptyCatalogue({
  onRefresh,
  busy,
}: {
  onRefresh: () => void;
  busy: boolean;
}) {
  return (
    <div className="mt-5 border border-line bg-panel px-5 py-8 text-center">
      <p className="text-[15px]">No services listed yet.</p>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
        The list comes from JustWatch and is stored locally, so this only needs
        doing once.
      </p>
      <button
        type="button"
        onClick={onRefresh}
        disabled={busy}
        className="mt-5 bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {busy ? "Fetching…" : "Fetch the list"}
      </button>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div
      aria-hidden
      className="mt-9 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3"
    >
      {Array.from({ length: 9 }, (_, index) => (
        <div
          key={index}
          className="h-[58px] animate-pulse border border-line bg-panel"
        />
      ))}
    </div>
  );
}

function describeRefresh(summary: ProviderRefresh): string {
  if (summary.fetched === 0) {
    // The backend keeps the stored catalogue when JustWatch lists nothing,
    // because an empty picker means no subscriptions and therefore nothing
    // available at all. Say so, rather than reporting a successful no-op.
    return "JustWatch listed nothing this time, so the stored list was kept.";
  }
  const changes = [
    summary.added && `${summary.added} added`,
    summary.updated && `${summary.updated} updated`,
    summary.removed && `${summary.removed} removed`,
  ].filter(Boolean);

  return changes.length
    ? `${summary.fetched} services: ${changes.join(", ")}.`
    : `${summary.fetched} services, nothing changed.`;
}
