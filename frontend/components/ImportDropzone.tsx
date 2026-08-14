"use client";

import { useRef, useState } from "react";
import { megabytes } from "@/lib/limits";

/**
 * Where the export file goes in.
 *
 * A real `<input type="file">` does the work and the drop target is decoration
 * over the top of it. Doing it the other way round -- a div with drag handlers
 * and a hidden input triggered by a click -- loses keyboard access and the
 * native file picker's own affordances, which is a poor trade for a slightly
 * tidier DOM.
 */
export default function ImportDropzone({
  onFile,
  busy,
  accept,
  hint,
  limit,
}: {
  onFile: (file: File) => void;
  busy: boolean;
  accept: string;
  hint: string;
  /** Stated up front where there is one, so the ceiling is something a person
   *  reads before choosing a file rather than after being refused one. Null
   *  where nothing the frontend knows about would stop an upload. */
  limit: number | null;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function take(file: File | undefined) {
    if (file && !busy) {
      onFile(file);
    }
    // Cleared so that picking the same file twice in a row still fires a change
    // event -- re-uploading the same export is a normal thing to do here, since
    // both exports are cumulative and imports are idempotent.
    if (input.current) {
      input.current.value = "";
    }
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        take(event.dataTransfer.files[0]);
      }}
      className={`border border-dashed transition-colors ${
        over ? "border-white bg-raised" : "border-edge bg-panel"
      } ${busy ? "opacity-60" : ""}`}
    >
      <label
        className={`flex flex-col items-center gap-3 px-6 py-12 text-center ${
          busy ? "cursor-progress" : "cursor-pointer"
        }`}
      >
        <input
          ref={input}
          type="file"
          accept={accept}
          disabled={busy}
          onChange={(event) => take(event.target.files?.[0])}
          className="sr-only"
        />

        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="h-6 w-6 text-dim"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12 15V3m0 0L8 7m4-4l4 4" />
          <path d="M3 15v4a2 2 0 002 2h14a2 2 0 002-2v-4" />
        </svg>

        <span className="text-[15px]">
          {busy ? (
            "Reading your file…"
          ) : (
            <>
              <span className="underline underline-offset-4">
                Choose a file
              </span>{" "}
              <span className="text-muted">or drop it here</span>
            </>
          )}
        </span>

        <span className="text-sm text-dim">{hint}</span>

        {/* Short on purpose. The full explanation belongs in the refusal, which
            is where somebody is actually asking why; a paragraph here would
            wrap to three lines on a phone to pre-empt a question nobody has
            yet. "The host's" is the part that has to survive the trim, because
            it is what stops the number reading as an app limit.

            One template literal rather than text around an expression, because
            JSX dropped the space before the dash and rendered "4.5 MB— the". */}
        {limit !== null && (
          <span className="text-xs text-dim">
            {`Up to ${megabytes(limit)} — the host’s limit, not the app’s`}
          </span>
        )}
      </label>
    </div>
  );
}
