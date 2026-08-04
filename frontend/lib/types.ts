/**
 * The backend's response bodies, mirrored.
 *
 * Kept by hand and kept honest: these are the shapes `backend/app/schemas.py`
 * actually returns, and each one is added by the step that first renders it
 * rather than the whole contract being copied up front. A type nothing draws is
 * a type nobody notices going stale.
 */

/** `POST /api/imports/netflix` -- what one upload did. */
export interface ImportSummary {
  import_id: number;
  source: string;
  filename: string | null;
  export_format: string;

  /** imported + duplicates + skipped === total_rows, so the count reconciles. */
  total_rows: number;
  imported: number;
  duplicates: number;
  skipped: number;

  /** Reason -> count, e.g. `{ supplemental_video: 12 }`. Empty when nothing was dropped. */
  skipped_by_reason: Record<string, number>;
  /** Readings the importer had to guess at, in plain language. */
  assumptions: string[];
}

/** One streaming service, as JustWatch names it in one country. */
export interface Provider {
  /** What an offer names its provider by, and what a subscription is stored as. */
  short_name: string;
  name: string;
  technical_name: string;
  icon_url: string | null;
  monetization_types: string[];
}

/** `GET /api/providers` -- everything the picker can offer. */
export interface ProviderCatalogue {
  country: string;
  providers: Provider[];
}

/** `POST /api/providers/refresh` -- what a catalogue refresh changed. */
export interface ProviderRefresh {
  country: string;
  /** Zero means JustWatch listed nothing and the stored catalogue was kept, not emptied. */
  fetched: number;
  added: number;
  updated: number;
  removed: number;
}

/** `GET`/`PUT /api/providers/mine` -- the services the user says they have. */
export interface Subscriptions {
  country: string;
  short_names: string[];
}
