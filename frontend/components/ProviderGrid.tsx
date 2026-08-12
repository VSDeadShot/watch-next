"use client";

import type { Provider } from "@/lib/types";
import { imageUrl } from "@/lib/urls";

/**
 * The services picker.
 *
 * Logos stay at full strength whether or not a service is picked. Draining the
 * unpicked ones to grey was the first thing tried and it reads well in theory --
 * colour meaning "you have this" -- but the logo is how somebody finds Netflix
 * in a list of a hundred, and a greyed logo on a near-black panel is close to
 * no logo at all. Selection is carried by the border, the background and the
 * checkmark instead, which is three signals and none of them cost scanning.
 *
 * One column on a phone, because these names do not fit two: "Amazon Prime
 * Video" and "Amazon Prime Video with Ads" truncate to the same stub, and a
 * picker where two rows are indistinguishable is worse than a longer one.
 */
export default function ProviderGrid({
  providers,
  selected,
  onToggle,
}: {
  providers: Provider[];
  selected: Set<string>;
  onToggle: (shortName: string) => void;
}) {
  // `grid-cols-1` is not redundant. Tailwind's grid-cols-* resolve to
  // minmax(0, 1fr); with no column set at all the implicit track is `auto` and
  // sizes to its content, so "AP International South Cinema Amazon Channel"
  // widens the track past the viewport and the whole page scrolls sideways.
  return (
    <div
      role="group"
      aria-label="Streaming services"
      className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3"
    >
      {providers.map((provider) => {
        const isSelected = selected.has(provider.short_name);
        const name = provider.name.trim();

        return (
          <button
            key={provider.short_name}
            type="button"
            role="checkbox"
            aria-checked={isSelected}
            onClick={() => onToggle(provider.short_name)}
            className={`flex w-full items-center gap-3 border px-3 py-3 text-left transition-colors ${
              isSelected
                ? "border-white bg-raised"
                : "border-line bg-panel hover:border-edge hover:bg-raised"
            }`}
          >
            <ProviderIcon provider={provider} name={name} />

            <span
              className={`min-w-0 flex-1 truncate text-sm ${
                isSelected ? "text-white" : "text-muted"
              }`}
              title={name}
            >
              {name}
            </span>

            <Check shown={isSelected} />
          </button>
        );
      })}
    </div>
  );
}

function ProviderIcon({
  provider,
  name,
}: {
  provider: Provider;
  name: string;
}) {
  // Checked, not just read. This is the one image in the app that does not go
  // through `next/image`, so its optimiser's host allowlist never sees it --
  // making `imageUrl` the only thing standing between a catalogue string and a
  // request the browser makes on its own.
  const icon = imageUrl(provider.icon_url);

  if (!icon) {
    // The catalogue can arrive without one, and now also arrives without one if
    // it came from somewhere unexpected. Two letters of the service's own name
    // beat a broken image frame or a generic placeholder glyph.
    return (
      <span
        aria-hidden
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-line text-[11px] font-medium text-muted"
      >
        {name.slice(0, 2).toUpperCase()}
      </span>
    );
  }

  // A fixed-size 32px third-party logo from a host that is not known at build
  // time, so there is no layout shift to prevent and nothing for the optimiser
  // to save -- next/image would only add a proxy hop per tile.
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={icon}
      alt=""
      width={32}
      height={32}
      loading="lazy"
      className="h-8 w-8 shrink-0 rounded-md object-cover"
    />
  );
}

function Check({ shown }: { shown: boolean }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 20 20"
      className={`h-4 w-4 shrink-0 transition-opacity ${
        shown ? "opacity-100" : "opacity-0"
      }`}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 10.5l4 4 8-9" />
    </svg>
  );
}
