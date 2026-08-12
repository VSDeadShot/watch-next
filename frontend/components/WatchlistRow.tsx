"use client";

import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";
import type { WatchlistItem } from "@/lib/types";
import { webUrl } from "@/lib/urls";

/**
 * One thing somebody meant to watch.
 *
 * A row rather than a card, and a small poster rather than a large one. The
 * display size and the full-bleed artwork belong to the recommendation and
 * nothing else may spend them -- a watchlist of ten posters competing for
 * attention is a feed, which is the thing this app was built instead of.
 *
 * The line that matters most is the availability one. A list of titles with no
 * word on whether any of them can be watched tonight is a list somebody has to
 * check by hand, four apps at a time, which is the errand the whole product
 * exists to run for them.
 */
export default function WatchlistRow({
  item,
  onWatched,
  onRemove,
  onNote,
  busy,
}: {
  item: WatchlistItem;
  onWatched: (watched: boolean) => void;
  onRemove: () => void;
  onNote: (note: string | null) => void;
  busy: boolean;
}) {
  const isSeries = item.object_type === "SHOW";
  const seen = item.watched_at !== null;
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const meta = [
    item.release_year,
    isSeries ? "Series" : "Film",
    item.runtime_minutes &&
      `${item.runtime_minutes} min${isSeries ? " an episode" : ""}`,
    item.imdb_score && `IMDb ${item.imdb_score}`,
  ]
    .filter(Boolean)
    .join("  ·  ");

  return (
    <article
      className={`flex gap-4 border border-line bg-panel p-4 sm:gap-5 sm:p-5 ${
        seen ? "opacity-60" : ""
      }`}
    >
      <Poster item={item} />

      <div className="flex min-w-0 flex-1 flex-col">
        <h3 className="text-[17px] leading-tight font-medium text-balance">
          {item.title}
        </h3>
        {meta && <p className="mt-1.5 text-sm text-muted">{meta}</p>}
        {item.genres.length > 0 && (
          <p className="mt-1 truncate text-sm text-dim">
            {item.genres.join(", ")}
          </p>
        )}

        <div className="mt-3">
          <Where item={item} seen={seen} />
        </div>

        {editing ? (
          <NoteEditor
            note={item.note}
            busy={busy}
            onSave={(next) => {
              onNote(next);
              setEditing(false);
            }}
            onCancel={() => setEditing(false)}
          />
        ) : (
          item.note && (
            <p className="mt-3 border-l border-edge pl-3 text-sm leading-relaxed text-muted">
              {item.note}
            </p>
          )
        )}

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <button
            type="button"
            onClick={() => onWatched(!seen)}
            disabled={busy}
            className="text-muted underline underline-offset-4 transition-colors hover:text-white disabled:opacity-50"
          >
            {seen ? "Not seen after all" : "I've watched this"}
          </button>

          {!editing && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={busy}
              className="text-muted underline underline-offset-4 transition-colors hover:text-white disabled:opacity-50"
            >
              {item.note ? "Edit note" : "Add a note"}
            </button>
          )}

          {/* Two presses rather than a dialog. Removing is the one action here
              that cannot be undone -- ticking off can be un-ticked -- and a
              browser confirm() would be a modal in an app that has none. */}
          <button
            type="button"
            onClick={() => (confirming ? onRemove() : setConfirming(true))}
            onBlur={() => setConfirming(false)}
            disabled={busy}
            className="text-muted underline underline-offset-4 transition-colors hover:text-white disabled:opacity-50"
          >
            {confirming ? "Sure? Remove it" : "Remove"}
          </button>
        </div>
      </div>
    </article>
  );
}

/**
 * The line the page is for.
 *
 * Empty `watch_on` means nowhere at no additional cost -- the same meaning it
 * has on a recommendation -- and saying so plainly is more use than leaving a
 * gap somebody has to interpret. A title already ticked off says nothing:
 * where to watch something you have watched is not a question.
 *
 * One button, not one per service. The recommendation card lists them all
 * because it is the only thing on that screen; ten rows doing the same is a
 * wall of forty buttons. The options arrive best first -- what somebody already
 * pays for, then free, then free with advertising -- so the first is the one
 * they would have picked anyway, and the rest are named quietly rather than
 * dropped.
 */
function Where({ item, seen }: { item: WatchlistItem; seen: boolean }) {
  if (seen) {
    return <p className="text-sm text-dim">Watched.</p>;
  }

  const [best, ...rest] = item.watch_on;

  if (!best) {
    return (
      <p className="text-sm text-dim">
        Not on your services — nothing to watch it on right now.
      </p>
    );
  }

  const label = best.requires_subscription
    ? `Watch on ${best.name}`
    : `Free on ${best.name}`;

  // The backend drops an unusable link on the way out; this is the second of
  // two checks rather than the only one. See lib/urls.
  const href = webUrl(best.url);

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="border border-edge px-3 py-1.5 text-sm transition-colors hover:border-white hover:bg-raised"
        >
          {label}
        </a>
      ) : (
        <span className="border border-line px-3 py-1.5 text-sm text-muted">
          {label}
        </span>
      )}

      {rest.length > 0 && (
        <span className="text-sm text-dim">
          also on {list(rest.map((option) => option.name))}
        </span>
      )}
    </div>
  );
}

/** "Netflix", "Netflix and MX Player", "Netflix, MX Player and Prime Video". */
function list(names: string[]): string {
  if (names.length === 1) return names[0];
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function NoteEditor({
  note,
  busy,
  onSave,
  onCancel,
}: {
  note: string | null;
  busy: boolean;
  onSave: (note: string | null) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(note ?? "");
  const field = useRef<HTMLInputElement>(null);
  // Two rows can be open at once, and two inputs sharing an id would point
  // both labels at whichever one the browser found first.
  const id = useId();

  useEffect(() => field.current?.focus(), []);

  function save() {
    // Empty means "delete this", not "I had nothing to say" -- somebody
    // clearing the box is looking straight at it. The backend reads null the
    // same way.
    const trimmed = draft.trim();
    onSave(trimmed === "" ? null : trimmed);
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <label className="sr-only" htmlFor={id}>
        Note
      </label>
      <input
        ref={field}
        id={id}
        value={draft}
        maxLength={500}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") save();
          if (event.key === "Escape") onCancel();
        }}
        placeholder="Why this one?"
        className="min-w-0 flex-1 border border-line bg-raised px-3 py-1.5 text-sm text-white placeholder:text-dim focus:border-edge focus:outline-none"
      />
      <button
        type="button"
        onClick={save}
        disabled={busy}
        className="bg-white px-3 py-1.5 text-sm font-medium text-ink transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        Save
      </button>
      <button
        type="button"
        onClick={onCancel}
        className="px-2 py-1.5 text-sm text-muted transition-colors hover:text-white"
      >
        Cancel
      </button>
    </div>
  );
}

function Poster({ item }: { item: WatchlistItem }) {
  if (!item.poster_url) {
    return (
      <div
        aria-hidden
        className="hidden aspect-[2/3] w-[72px] shrink-0 items-center justify-center border border-line bg-raised sm:flex"
      >
        <span className="px-1 text-center text-[11px] leading-tight text-dim">
          No artwork
        </span>
      </div>
    );
  }

  return (
    <div className="relative hidden aspect-[2/3] w-[72px] shrink-0 bg-raised sm:block">
      <Image
        src={item.poster_url}
        alt=""
        fill
        sizes="72px"
        className="object-cover"
      />
    </div>
  );
}
