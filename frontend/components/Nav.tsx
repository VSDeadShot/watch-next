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
 * Only routes that exist are listed. The library, watchlist and stats pages
 * arrive with the steps that build them; a nav that links to a 404 to look
 * finished is worse than a short nav.
 */
const LINKS = [
  { href: "/", label: "Tonight" },
  { href: "/import", label: "Import" },
  { href: "/settings", label: "Settings" },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-ink/90 backdrop-blur-sm">
      <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-6 px-5 py-4 sm:px-8">
        <Link
          href="/"
          className="text-[15px] font-medium tracking-[-0.03em] whitespace-nowrap"
        >
          <span className="text-dim">watch</span>{" "}
          <span className="text-white">next</span>
        </Link>

        <nav className="flex items-center gap-1 sm:gap-2">
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
                className={`relative px-2.5 py-1.5 text-sm transition-colors sm:px-3 ${
                  active ? "text-white" : "text-muted hover:text-white"
                }`}
              >
                {link.label}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2.5 -bottom-[17px] h-px bg-white sm:inset-x-3"
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
