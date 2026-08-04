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
 */
const ROUTES = [
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
] as const;

export default function ImportInstructions() {
  const [open, setOpen] = useState<string>(ROUTES[0].id);

  return (
    <div>
      <div
        role="tablist"
        aria-label="Ways to get your Netflix history"
        className="flex gap-1 border-b border-line"
      >
        {ROUTES.map((route) => {
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

      {ROUTES.map((route) => (
        <div
          key={route.id}
          role="tabpanel"
          id={`route-${route.id}`}
          aria-labelledby={`tab-${route.id}`}
          hidden={route.id !== open}
          className="pt-5"
        >
          <p className="text-sm text-muted">{route.lede}</p>

          <ol className="mt-5 space-y-3.5">
            {route.steps.map((step, index) => (
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
            {route.caveat}
          </p>
        </div>
      ))}
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
