"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * A top bar rather than the sidebar the sibling projects use.
 *
 * This app is one page with a few places to set it up, not a dashboard with
 * sections. A sidebar would spend a permanent column of every screen advertising
 * navigation nobody needs once their services are picked -- and the page that
 * matters wants the width.
 *
 * Only routes that exist are listed. A nav that links to a 404 to look finished
 * is worse than a short nav.
 *
 * **Two rows below `sm`, one row above it.** Four one-word labels beside the
 * wordmark was the measured ceiling at 360px, and a fifth pushed the bar past
 * the edge of the screen. Giving the links their own line under the wordmark
 * lifts the ceiling instead of working around it: no label has to be shortened
 * past meaning, nothing goes behind a menu somebody has to discover, and the
 * whole row is still reachable with one thumb. The cost is about thirty pixels
 * of sticky header on a phone, which is the cheapest of the options that do not
 * hide something.
 *
 * The active underline lands on the header's bottom border, which means its
 * offset depends on the bottom padding -- and that padding differs between the
 * two layouts. Hence the two values rather than one; both are measured, not
 * guessed.
 */
const LINKS = [
  { href: "/", label: "Tonight" },
  { href: "/watchlist", label: "Saved" },
  { href: "/stats", label: "Stats" },
  { href: "/import", label: "Import" },
  { href: "/settings", label: "Settings" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ink/90 backdrop-blur-sm">
      <div className="mx-auto w-full max-w-5xl px-5 py-3 sm:flex sm:items-center sm:justify-between sm:gap-6 sm:px-8 sm:py-4">
        <Link
          href="/"
          className="inline-block text-[15px] font-medium tracking-[-0.03em] whitespace-nowrap"
        >
          <span className="text-dim">watch</span>{" "}
          <span className="text-white">next</span>
        </Link>

        {/* The negative margin cancels the first link's own padding, so the row
            of labels starts on the same left edge as the wordmark above it. */}
        <nav className="-mx-2 mt-1.5 flex items-center sm:mx-0 sm:mt-0 sm:gap-2">
          {LINKS.map((link) => {
            const active =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);

            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={`relative px-2 py-1.5 text-[13px] whitespace-nowrap transition-colors sm:px-3 sm:text-sm ${
                  active ? "text-white" : "text-muted hover:text-white"
                }`}
              >
                {link.label}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2 -bottom-[13px] h-px bg-white sm:inset-x-3 sm:-bottom-[17px]"
                  />
                )}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
