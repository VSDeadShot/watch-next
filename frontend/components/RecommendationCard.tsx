"use client";

import Image from "next/image";
import type { RecommendedTitle } from "@/lib/types";
import { webUrl } from "@/lib/urls";

/**
 * The answer. The only thing in this app allowed to be loud.
 *
 * Every other screen was kept deliberately quiet so that this one can use the
 * display size, the full width and the only large piece of artwork anywhere.
 * The poster bleeds to the card's edge rather than sitting inside padding,
 * because a picture with a margin around it is an illustration and a picture
 * that touches the edge is the thing itself.
 *
 * There is exactly one of these on the page and no component that renders a
 * list of them. The API cannot express a second answer and neither can this.
 */
export default function RecommendationCard({
  title,
  onReject,
  onSave,
  rejecting,
  saved,
  saving,
}: {
  title: RecommendedTitle;
  onReject: () => void;
  onSave: () => void;
  rejecting: boolean;
  saved: boolean;
  saving: boolean;
}) {
  const isSeries = title.object_type === "SHOW";

  return (
    <article
      // Keyed on the title upstream, so a re-roll replays the reveal rather
      // than swapping the text under a card that never moved.
      className="animate-reveal grid overflow-hidden border border-line bg-panel sm:grid-cols-[minmax(0,240px)_1fr]"
    >
      <Poster title={title} />

      <div className="flex min-w-0 flex-col p-6 sm:p-8">
        <h2 className="text-display font-medium text-balance">{title.title}</h2>

        <p className="mt-3 text-sm text-muted">
          {[
            title.release_year,
            isSeries ? "Series" : "Film",
            title.runtime_minutes &&
              `${title.runtime_minutes} min${isSeries ? " an episode" : ""}`,
            title.imdb_score && `IMDb ${title.imdb_score}`,
          ]
            .filter(Boolean)
            .join("  ·  ")}
        </p>

        {title.genres.length > 0 && (
          <p className="mt-1.5 text-sm text-dim">{title.genres.join(", ")}</p>
        )}

        {title.reasons.length > 0 && (
          <ul className="mt-6 space-y-2">
            {title.reasons.map((reason) => (
              <li
                key={reason}
                className="flex gap-2.5 text-[15px] leading-relaxed"
              >
                <span aria-hidden className="mt-2 h-px w-3 shrink-0 bg-edge" />
                <span>{reason}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-auto pt-8">
          <WatchOn title={title} />

          {/* Two ways to say no, and they mean opposite things. "Not this one"
              throws it back for this evening; saving keeps it for an evening
              when there is time. Without the second, the only thing somebody
              could do with a title they liked but could not watch tonight was
              try to remember it. */}
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
            <button
              type="button"
              onClick={onReject}
              disabled={rejecting}
              className="text-muted underline underline-offset-4 transition-colors hover:text-white disabled:opacity-50"
            >
              {rejecting ? "Finding another…" : "Not this one"}
            </button>

            <button
              type="button"
              onClick={onSave}
              disabled={saving || saved}
              className="text-muted underline underline-offset-4 transition-colors hover:text-white disabled:no-underline disabled:opacity-70"
            >
              {saved
                ? "Saved to your list"
                : saving
                  ? "Saving…"
                  : "Save for later"}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}

function Poster({ title }: { title: RecommendedTitle }) {
  if (!title.poster_url) {
    return (
      <div
        aria-hidden
        className="hidden aspect-[2/3] items-center justify-center border-r border-line bg-raised sm:flex"
      >
        <span className="px-6 text-center text-sm text-dim">
          No artwork for this one
        </span>
      </div>
    );
  }

  // Capped on a phone. At full width a 2:3 poster is over 600px tall, which
  // pushed the title and the watch button off the bottom of the screen -- so
  // opening the app showed artwork and hid the answer. Anchored to the top of
  // the image because that is where a poster puts its face; the bottom is
  // usually the title treatment, which the heading beside it already says.
  return (
    <div className="relative h-[38vh] max-h-[420px] min-h-[200px] w-full bg-raised sm:h-auto sm:max-h-none sm:aspect-[2/3]">
      <Image
        src={title.poster_url}
        alt={`Poster for ${title.title}`}
        fill
        // The largest thing on the page and the reason this reads as a reveal
        // rather than a search result, so it is never lazy.
        priority
        sizes="(min-width: 640px) 240px, 100vw"
        className="object-cover object-top"
      />
    </div>
  );
}

/**
 * Where to press play, as a real link rather than a label.
 *
 * The whole product is that this title is watchable right now, so the last
 * step had better not be "go and find it yourself". Free offers say so
 * explicitly -- somebody should not assume they need a subscription they do
 * not have.
 */
function WatchOn({ title }: { title: RecommendedTitle }) {
  if (title.watch_on.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {title.watch_on.map((option) => {
        const label = option.requires_subscription
          ? `Watch on ${option.name}`
          : `Free on ${option.name}`;
        // The backend drops an unusable link on the way out, so this is the
        // second of two checks rather than the only one. See lib/urls.
        const href = webUrl(option.url);

        return href ? (
          <a
            key={option.short_name}
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="bg-white px-4 py-2 text-sm font-medium text-ink transition-opacity hover:opacity-90"
          >
            {label}
          </a>
        ) : (
          <span
            key={option.short_name}
            className="border border-edge px-4 py-2 text-sm"
          >
            {label}
          </span>
        );
      })}
    </div>
  );
}
