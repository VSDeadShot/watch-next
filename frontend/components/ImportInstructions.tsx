"use client";

import { useState } from "react";

/**
 * How to get the file, because that is the part that actually stops people.
 *
 * Netflix has two exports and they are not equivalent. The one you can have in
 * two minutes carries only a title and a date; the one with durations, devices
 * and profiles has to be requested and takes a day or two to arrive. Both work
 * here, so the honest thing is to show both and say what each costs and buys,
 * rather than picking one and leaving somebody to discover the trade later.
 *
 * YouTube has one route and it has a step nobody guesses: Takeout hands out
 * HTML unless you go and change it, and the HTML is useless here. That step gets
 * its own emphasis for the same reason the Netflix routes get a comparison —
 * the instructions are the product on this page.
 */
export type Route = {
  id: string;
  label: string;
  lede: string;
  steps: React.ReactNode[];
  caveat: string;
};

export const NETFLIX_ROUTES: readonly Route[] = [
  {
    id: "quick",
    label: "Right now",
    lede: "Two minutes. Titles and dates only, for the profile you are signed in as.",
    steps: [
      <>
        Open{" "}
        <ExternalLink href="https://www.netflix.com/viewingactivity">
          netflix.com/viewingactivity
        </ExternalLink>{" "}
        and pick the profile you want.
      </>,
      <>Scroll to the very bottom of the list.</>,
      <>
        Click <Strong>Download all</Strong>. You get{" "}
        <Path>NetflixViewingHistory.csv</Path>.
      </>,
      <>Drop that file in below.</>,
    ],
    caveat:
      "One profile at a time — repeat it for each person if you share the account. There is no watch duration in this export, so nothing can be filtered out as an accidental thirty-second start.",
  },
  {
    id: "full",
    label: "The complete export",
    lede: "A day or two to arrive. Everything: durations, devices, profiles, countries.",
    steps: [
      <>
        Open{" "}
        <ExternalLink href="https://www.netflix.com/account/getmyinfo">
          netflix.com/account/getmyinfo
        </ExternalLink>
        .
      </>,
      <>
        Click <Strong>Submit request</Strong> and confirm it by email.
      </>,
      <>
        Netflix emails a download link when it is ready, usually within a couple
        of days.
      </>,
      <>
        Drop the <Path>.zip</Path> in below exactly as it downloaded — no need to
        unpack it or go hunting for{" "}
        <Path>CONTENT_INTERACTION/ViewingActivity.csv</Path>.
      </>,
    ],
    caveat:
      "Worth the wait if you can: durations are what separate something you watched from something you started and abandoned, and every profile on the account comes in at once.",
  },
];

export const YOUTUBE_ROUTES: readonly Route[] = [
  {
    id: "takeout",
    label: "Google Takeout",
    lede: "Minutes to a day, depending on how much history you have. One file at the end of it.",
    steps: [
      <>
        Open{" "}
        <ExternalLink href="https://takeout.google.com">
          takeout.google.com
        </ExternalLink>{" "}
        and click <Strong>Deselect all</Strong>.
      </>,
      <>
        Scroll down and tick <Strong>YouTube and YouTube Music</Strong> only.
      </>,
      <>
        Click <Strong>All YouTube data included</Strong> and leave just{" "}
        <Strong>history</Strong> ticked. The rest is comments, playlists and
        subscriptions, none of which this reads.
      </>,
      <>
        Click <Strong>Multiple formats</Strong> and change history from{" "}
        <Strong>HTML</Strong> to <Strong>JSON</Strong>. This is the step everyone
        misses, and HTML cannot be read here.
      </>,
      <>
        Export once, wait for the email, and unzip what it sends you. The file is
        at{" "}
        <Path>Takeout/YouTube and YouTube Music/history/watch-history.json</Path>
        .
      </>,
      <>Drop that one file in below.</>,
    ],
    caveat:
      "The file itself, not the archive it came in — unlike the Netflix export. A Takeout archive can run to gigabytes and Google splits large ones across several downloads, so sending the whole thing to reach one file inside it would cost you a long upload for nothing.",
  },
];

export default function ImportInstructions({
  routes,
  label,
}: {
  routes: readonly Route[];
  label: string;
}) {
  const [open, setOpen] = useState<string>(routes[0].id);
  const current = routes.find((route) => route.id === open) ?? routes[0];

  return (
    <div>
      {/* One route needs no tabs. Netflix has two genuinely different exports
          and YouTube has one way in, and a tablist with a single tab is a
          control that asks a question with no answers. */}
      {routes.length > 1 && (
        <div
          role="tablist"
          aria-label={label}
          className="flex gap-1 border-b border-line"
        >
          {routes.map((route) => {
            const selected = route.id === open;
            return (
              <button
                key={route.id}
                role="tab"
                type="button"
                aria-selected={selected}
                aria-controls={`route-${route.id}`}
                id={`tab-${route.id}`}
                onClick={() => setOpen(route.id)}
                className={`-mb-px border-b px-3 py-2.5 text-sm transition-colors ${
                  selected
                    ? "border-white text-white"
                    : "border-transparent text-muted hover:text-white"
                }`}
              >
                {route.label}
              </button>
            );
          })}
        </div>
      )}

      <div
        role={routes.length > 1 ? "tabpanel" : undefined}
        id={`route-${current.id}`}
        aria-labelledby={routes.length > 1 ? `tab-${current.id}` : undefined}
        className={routes.length > 1 ? "pt-5" : undefined}
      >
        <p className="text-sm text-muted">{current.lede}</p>

        <ol className="mt-5 space-y-3.5">
          {current.steps.map((step, index) => (
            <li key={index} className="flex gap-3.5">
              <span
                aria-hidden
                className="mt-px font-mono text-xs text-dim tabular-nums"
              >
                {index + 1}
              </span>
              <span className="max-w-[65ch] text-[15px] leading-relaxed">
                {step}
              </span>
            </li>
          ))}
        </ol>

        <p className="mt-5 max-w-[65ch] border-l border-line pl-4 text-sm leading-relaxed text-dim">
          {current.caveat}
        </p>
      </div>
    </div>
  );
}

function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="underline underline-offset-4 transition-colors hover:text-muted"
    >
      {children}
    </a>
  );
}

function Strong({ children }: { children: React.ReactNode }) {
  return <span className="font-medium">{children}</span>;
}

function Path({ children }: { children: React.ReactNode }) {
  // Monospace for a literal filename, which is a thing to be typed or
  // recognised character for character, not a costume for looking technical.
  return (
    <code className="font-mono text-[13px] text-muted">{children}</code>
  );
}
