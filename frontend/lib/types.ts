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

/** What somebody is in the mood for. Mirrors `app/core/moods.py`. */
export type Mood =
  | "surprise_me"
  | "laugh"
  | "thrill"
  | "think"
  | "comfort"
  | "moved"
  | "escape";

/** Whether they want a film, a series, or do not mind. */
export type KindPreference = "any" | "movie" | "series";

/** `POST /api/recommend` -- what somebody wants tonight. */
export interface RecommendationRequestBody {
  mood: Mood;
  /** Null means no limit, which is not the same as a large number: with no
   *  budget, runtime stops influencing the choice rather than preferring
   *  something enormous. */
  minutes_available: number | null;
  kind: KindPreference;
  /** The titles already turned down in this sitting -- what "not this one" sends. */
  exclude_ids: number[];
}

/** Somewhere the pick can be watched at no additional cost. */
export interface WatchOn {
  short_name: string;
  name: string;
  monetization: string;
  url: string | null;
  /** False for anything free to everybody, so the interface can say "free on
   *  JioHotstar" rather than implying a subscription they do not have. */
  requires_subscription: boolean;
}

/** The one title, and everything needed to justify and act on it. */
export interface RecommendedTitle {
  title_id: number;
  jw_node_id: string;
  title: string;
  object_type: string;
  release_year: number | null;
  runtime_minutes: number | null;
  genres: string[];
  poster_url: string | null;
  imdb_score: number | null;

  score: number;
  /** Why this one, in plain language, strongest first. */
  reasons: string[];
  watch_on: WatchOn[];
  /** Already waiting on the list, said outright rather than left to be read
   *  out of `reasons` -- which is written for a person, not for a parser. */
  on_watchlist: boolean;
}

/** How many candidates survived each stage. Read in order, it says where the
 *  search collapsed -- which is the difference between "import something",
 *  "tick a box in settings" and "ask again with more time". */
export interface Considered {
  pool: number;
  available: number;
  eligible: number;
}

/**
 * One title, or none and the reason why.
 *
 * `title` is a single object rather than a list with one element in it, and
 * that is the product: there is no field here that could hold a second answer.
 */
export interface Recommendation {
  title: RecommendedTitle | null;
  /** Populated only when there is no title, and written to be acted on. */
  reason: string;
  considered: Considered;
}

/**
 * `GET /api/watchlist` -- one thing somebody decided they want to watch.
 *
 * The only thing in this app that was chosen rather than inferred. Everything
 * else about a person here is a guess from their history; this is not.
 */
export interface WatchlistItem {
  title_id: number;
  jw_node_id: string;
  title: string;
  object_type: string;
  release_year: number | null;
  runtime_minutes: number | null;
  genres: string[];
  poster_url: string | null;
  imdb_score: number | null;

  /** Where it can be watched now, best first. Empty means nowhere they can. */
  watch_on: WatchOn[];

  added_at: string;
  /** Null while it is still waiting; set once they say they have seen it. */
  watched_at: string | null;
  note: string | null;
}

/**
 * One labelled number in a ranked or ordered list. Mirrors `CountResponse`.
 *
 * The order is the backend's and means something different per list: genres
 * arrive commonest-first, decades arrive oldest-first. Nothing here re-sorts
 * them.
 */
export interface LabelledCount {
  label: string;
  count: number;
}

/** Activity in one month, dated by its first day as `YYYY-MM-DD`. */
export interface MonthCount {
  /** Every month between the first and the last is present, empty ones
   *  included -- a gap in viewing is information, and the series would draw a
   *  lie about the shape of a year without them. */
  month: string;
  count: number;
}

/** A much-watched title, and how much watching went into it. */
export interface TopTitle {
  title_id: number;
  title: string;
  /** Carried because the count means different things either side of it:
   *  twelve sessions of a series is twelve episodes, twelve of a film is
   *  having watched it twelve times. */
  object_type: string;
  sessions: number;
}

/** The Netflix-shaped half of `GET /api/stats` -- a watch history, counted. */
export interface HistoryStats {
  /** Distinct things, and the number of times somebody sat down. Both are
   *  true and neither substitutes for the other. */
  titles: number;
  sessions: number;
  movies: number;
  series: number;

  /** Null when nothing in the history recorded how long it ran, which is not
   *  the same as nothing having been watched. Present it as the lower bound
   *  `sessions_timed` says it is. */
  minutes_watched: number | null;
  sessions_timed: number;

  first_watched: string | null;
  last_watched: string | null;

  top_genres: LabelledCount[];
  decades: LabelledCount[];
  top_titles: TopTitle[];
  by_month: MonthCount[];
}

/** The YouTube half, reported separately because it is a separate thing. */
export interface YouTubeStats {
  /** Views and distinct videos both, because the gap between them is how
   *  often somebody goes back to the same thing. */
  views: number;
  videos: number;
  channels: number;
  first_watched: string | null;
  last_watched: string | null;
  top_channels: LabelledCount[];
  by_month: MonthCount[];
}

/** `GET /api/stats` -- everything the stats page is drawn from. */
export interface Stats {
  history: HistoryStats;
  youtube: YouTubeStats;
  /** Watch events that never reached a catalogue row, and so are in none of
   *  the numbers above. Worth showing because the fix is one a person can
   *  act on: resolve the library, or decide a few by hand. */
  unresolved_sessions: number;
}
