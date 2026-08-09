"use client";

import type { KindPreference, Mood } from "@/lib/types";

/**
 * The question, in three parts.
 *
 * Every one has a defensible default, so somebody who touches none of them
 * still gets an answer -- that is the promise, and a form standing between a
 * person and their recommendation would be the decision paralysis this app
 * exists to remove, wearing a different hat.
 */
const MOODS: { value: Mood; label: string }[] = [
  { value: "surprise_me", label: "Surprise me" },
  { value: "laugh", label: "Laugh" },
  { value: "thrill", label: "Thrill" },
  { value: "think", label: "Think" },
  { value: "comfort", label: "Comfort" },
  { value: "moved", label: "Move me" },
  { value: "escape", label: "Escape" },
];

/**
 * Chunks rather than a slider.
 *
 * Nobody has forty-seven minutes; they have "about an hour" or "no rush". The
 * runtime filter allows ten percent over anyway, so precision finer than these
 * steps is noise somebody would have to fiddle with a slider to express.
 */
const BUDGETS: { value: number | null; label: string }[] = [
  { value: null, label: "No rush" },
  { value: 30, label: "30 min" },
  { value: 60, label: "1 hour" },
  { value: 90, label: "90 min" },
  { value: 120, label: "2 hours" },
];

const KINDS: { value: KindPreference; label: string }[] = [
  { value: "any", label: "Either" },
  { value: "movie", label: "A film" },
  { value: "series", label: "A series" },
];

function labelOf<T>(table: { value: T; label: string }[], value: T): string {
  return table.find((option) => option.value === value)?.label ?? "";
}

/**
 * The question in one line, for a phone that has already been answered.
 *
 * Three rows of chips are 460px on a 414px screen, which pushed the title and
 * the watch button clean off the bottom -- so opening the app showed artwork
 * and hid the answer. On a wide screen the chips cost nothing and stay put.
 */
export function AskSummary({
  mood,
  minutes,
  kind,
  onExpand,
}: {
  mood: Mood;
  minutes: number | null;
  kind: KindPreference;
  onExpand: () => void;
}) {
  const parts = [
    labelOf(MOODS, mood),
    labelOf(BUDGETS, minutes),
    labelOf(KINDS, kind),
  ].filter(Boolean);

  return (
    <div className="flex items-center justify-between gap-4 border-b border-line pb-4 sm:hidden">
      <p className="min-w-0 truncate text-sm text-muted">
        {parts.join("  ·  ")}
      </p>
      <button
        type="button"
        onClick={onExpand}
        className="shrink-0 text-sm underline underline-offset-4"
      >
        Change
      </button>
    </div>
  );
}

export default function AskControls({
  mood,
  minutes,
  kind,
  onMood,
  onMinutes,
  onKind,
  disabled,
}: {
  mood: Mood;
  minutes: number | null;
  kind: KindPreference;
  onMood: (value: Mood) => void;
  onMinutes: (value: number | null) => void;
  onKind: (value: KindPreference) => void;
  disabled: boolean;
}) {
  // The question is three rows of chips and the answer is the point, so time
  // and kind share a row on anything wider than a phone. Three stacked rows
  // pushed the recommendation most of a laptop screen down, which is a strange
  // thing for an app whose promise is that you open it and are told.
  return (
    <div className="space-y-4">
      <Row label="In the mood for">
        {MOODS.map((option) => (
          <Chip
            key={option.value}
            active={option.value === mood}
            disabled={disabled}
            onClick={() => onMood(option.value)}
          >
            {option.label}
          </Chip>
        ))}
      </Row>

      <div className="flex flex-wrap gap-x-8 gap-y-4">
        <Row label="Time you have">
          {BUDGETS.map((option) => (
            <Chip
              key={String(option.value)}
              active={option.value === minutes}
              disabled={disabled}
              onClick={() => onMinutes(option.value)}
            >
              {option.label}
            </Chip>
          ))}
        </Row>

        <Row label="Film or series">
          {KINDS.map((option) => (
            <Chip
              key={option.value}
              active={option.value === kind}
              disabled={disabled}
              onClick={() => onKind(option.value)}
            >
              {option.label}
            </Chip>
          ))}
        </Row>
      </div>
    </div>
  );
}

/**
 * A label and its chips, on one line.
 *
 * The label used to have a line to itself, and the two of them were 39% of the
 * height this question spent before the answer began -- for words nobody needs
 * to read. "Surprise me / Laugh / Thrill" is obviously a mood and "No rush / 30
 * min" is obviously a time; the label is orientation rather than information,
 * so it sits beside the chips rather than above them. The chips themselves are
 * untouched: they are what a thumb presses on a phone and they are already
 * smaller than the guidance likes.
 *
 * The `legend` stays and goes to screen readers, because it is what makes these
 * buttons a group rather than seven loose ones. The visible copy is a `span` on
 * the chip line instead: a `legend` is laid out by the fieldset's own rules and
 * cannot be put inline without fighting them.
 */
function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="sr-only">{label}</legend>
      {/* 6px between chips rather than 8, and `mr-1` on the label so it still
          stands a little further off than they stand from each other. The two
          pixels are not cosmetic: with 8px the mood row needed 698 and a 768px
          tablet gives it 689, so seven chips became eight lines' worth of
          height for the sake of nine pixels. */}
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
        <span aria-hidden className="mr-1 text-[13px] text-dim">
          {label}
        </span>
        {children}
      </div>
    </fieldset>
  );
}

function Chip({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={`border px-3 py-1.5 text-[13px] transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:px-3.5 sm:text-sm ${
        active
          ? "border-white bg-white text-ink"
          : "border-line bg-panel text-muted hover:border-edge hover:text-white"
      }`}
    >
      {children}
    </button>
  );
}
