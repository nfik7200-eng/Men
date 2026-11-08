// Types mirror the JSON the FastAPI backend ACTUALLY returns (shopping/web/app.py, watchtower/*).
// Money is integer minor units; null means "not known" and must never be displayed as zero.

export type SourceKind = 'live' | 'demo' | 'sandbox';
export type Availability = 'in_stock' | 'out_of_stock' | 'backorder' | 'preorder' | 'unknown';
export type MatchStatus = 'self' | 'auto' | 'review' | 'confirmed';
export type PriceStatus = 'verified' | 'stale' | 'unavailable' | string;

export interface DealComponent {
  name: string;
  points: number;
  max_points: number;
  evidence: string;
  detail: string;
}

export interface Deal {
  score: number | null;
  label: string;
  confidence: 'none' | 'low' | 'medium' | 'high';
  warnings: string[];
  components: DealComponent[];
}

export interface Listing {
  id: number;
  title: string;
  brand: string | null;
  category: string | null;
  url: string;
  source: string;
  source_kind: SourceKind;
  currency: string;
  price_minor: number | null;
  original_price_minor: number | null;
  availability: Availability;
  specs: Record<string, unknown>;
  active: boolean;
  product_id: number | null;
  match_status: MatchStatus;
  match_confidence: number | null;
  last_observed_at: string | null;
  price_status: PriceStatus;
  freshness: string | null;
  deal?: Deal;
}

export interface ListingPage {
  total: number;
  page: number;
  items: Listing[];
  /** Currencies in the matched set. More than one means prices cannot be ordered against each other. */
  currencies?: string[];
  /** Set when a price sort fell back to name because of mixed currencies. */
  sort_fallback?: string | null;
}

export interface Observation {
  observed_at: string;
  price_minor: number | null;
  original_price_minor: number | null;
  currency: string;
  availability: Availability;
  shipping_minor: number | null;
}

export interface HistoryResponse {
  listing_id: number;
  observations: Observation[];
}

export interface DealsResponse {
  items: Listing[];
}

export interface PlatformEvent {
  id: number;
  listing_id: number;
  kind: string;
  old: string | null;
  new: string | null;
  pct: number | null;
  detected_at: string;
}

export interface EventsResponse {
  items: PlatformEvent[];
}

export interface Source {
  key: string;
  name: string;
  kind: SourceKind;
  currency: string;
  last_success_at: string | null;
  last_status: string | null;
  terms_note: string;
}

export interface SourcesResponse {
  items: Source[];
}

export interface ResearchPick {
  listing: Listing;
  meets: string[];
  misses: string[];
  unknown: string[];
  import_estimate: Record<string, unknown> | null;
}

export interface ResearchResponse {
  text: string;
  interpretation: Record<string, unknown>;
  caveats: string[];
  gaps: string[];
  retrieved_listing_ids: number[];
  llm_summary: string | null;
  picks: ResearchPick[];
}

export interface CoverageSource {
  key: string;
  name: string;
  kind: SourceKind;
  discovered: number;
  rejected: Record<string, number>;
  duplicates: number;
  non_product: number;
  eligible: number;
  acquired: number;
  fetched_ok: number;
  failed: Record<string, number>;
  pending_retry: number;
  unknown_outcome: number;
  never_attempted: number;
  deferred_budget: number;
  unfetched_with_counterpart: number | null;
  eligible_coverage: number | null;
  extraction_success_rate: number | null;
  last_fetch_at: string | null;
  last_attempt_at: string | null;
  avg_seconds_per_page: number | null;
  freshness: { listings: number; median_age_hours: number | null; max_age_hours: number | null; stale: number };
  health: string;
  next_up?: Array<{ url: string; score: number | null; state: string | null; reason: string | null; why: string[] }>;
}

export interface CoverageResponse {
  data_class: 'live';
  live_merchants: number;
  live_listings: number;
  cross_merchant_products: number;
  sources: CoverageSource[];
}

export interface ApiErrorBody {
  error?: string;
  detail?: string;
}
