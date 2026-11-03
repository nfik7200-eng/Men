import type {
  AnalysisRun,
  AnomalyPage,
  Behaviour,
  ChainStatus,
  Flows,
  Graph,
  IntelEnvelope,
  IntelProfile,
  IntelStatus,
  OpportunityPage,
  OpportunityRefresh,
  VerifyResult,
  WalletVerify,
  DashboardSummary,
  HealthCheck,
  IngestionJob,
  NetworkObservation,
  ObservationPage,
  ProviderDetail,
  FindingSummary,
  Health,
  Investigation,
  InvestigationStatus,
  InvestigationSummary,
  Provider,
  PublicSettings,
  WalletList,
  WalletProfile,
  Watchlist,
  WatchlistEntry,
  WatchlistSummary,
} from "./types";

export const API_BASE = "/api/v1";

/** An error the backend reported with its error envelope, or a connection failure (status 0). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: { field: string; message: string }[];

  constructor(status: number, code: string, message: string, details: ApiError["details"] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  get isConnectionError(): boolean {
    return this.status === 0;
  }
}

type ErrorEnvelope = {
  error?: { code?: unknown; message?: unknown; details?: unknown };
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}) },
    });
  } catch {
    throw new ApiError(0, "connection_failed", "Cannot reach the BIP backend. Is it running?");
  }
  if (response.status === 204) return undefined as T; // only used with T = undefined
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const env = (payload ?? {}) as ErrorEnvelope;
    const code = typeof env.error?.code === "string" ? env.error.code : "http_error";
    const message =
      typeof env.error?.message === "string"
        ? env.error.message
        : `Request failed with status ${response.status}.`;
    const details = Array.isArray(env.error?.details)
      ? (env.error.details as ApiError["details"])
      : [];
    throw new ApiError(response.status, code, message, details);
  }
  return payload as T;
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });
const enc = encodeURIComponent;

export const api = {
  health: () => request<Health>("/health"),
  chains: () => request<ChainStatus[]>("/chains"),
  summary: () => request<DashboardSummary>("/dashboard/summary"),
  providers: () => request<Provider[]>("/providers"),
  provider: (id: string) => request<ProviderDetail>(`/providers/${enc(id)}`),
  healthCheck: (id: string) => request<HealthCheck>(`/providers/${enc(id)}/health-check`, { method: "POST" }),
  setProviderEnabled: (id: string, enabled: boolean) =>
    request<Provider>(`/providers/${enc(id)}`, { method: "PATCH", ...json({ enabled }) }),
  network: (chain: string) => request<NetworkObservation>(`/chains/${enc(chain)}/network`),
  fetchLatestBlock: (chain: string) =>
    request<NetworkObservation>(`/chains/${enc(chain)}/latest-block`, { method: "POST" }),
  ingestWallet: (chain: string, address: string, limit: number) =>
    request<IngestionJob>(`/ingestion/wallet/${enc(chain)}/${enc(address)}?limit=${limit}`, { method: "POST" }),
  job: (id: number) => request<IngestionJob>(`/ingestion/jobs/${id}`),
  jobs: (chain: string, address: string) =>
    request<IngestionJob[]>(`/ingestion/jobs?limit=5&chain=${enc(chain)}&address=${enc(address)}`),
  walletTransactions: (chain: string, address: string) =>
    request<ObservationPage>(`/wallets/${enc(chain)}/${enc(address)}/transactions?limit=50`),
  walletTransfers: (chain: string, address: string) =>
    request<ObservationPage>(`/wallets/${enc(chain)}/${enc(address)}/transfers?limit=50`),
  walletBalanceChanges: (chain: string, address: string) =>
    request<ObservationPage>(`/wallets/${enc(chain)}/${enc(address)}/balance-changes?limit=50`),
  settings: () => request<PublicSettings>("/settings"),
  wallets: (limit = 50, offset = 0) => request<WalletList>(`/wallets?limit=${limit}&offset=${offset}`),
  wallet: (chain: string, address: string) =>
    request<WalletProfile>(`/wallets/${enc(chain)}/${enc(address)}`),
  watchlists: () => request<WatchlistSummary[]>("/watchlists"),
  watchlist: (id: number) => request<Watchlist>(`/watchlists/${id}`),
  createWatchlist: (name: string, description: string | null) =>
    request<Watchlist>("/watchlists", { method: "POST", ...json({ name, description }) }),
  deleteWatchlist: (id: number) => request<undefined>(`/watchlists/${id}`, { method: "DELETE" }),
  addWatchlistEntry: (id: number, chain: string, address: string, note: string | null) =>
    request<WatchlistEntry>(`/watchlists/${id}/entries`, {
      method: "POST",
      ...json({ chain, address, note }),
    }),
  removeWatchlistEntry: (id: number, entryId: number) =>
    request<undefined>(`/watchlists/${id}/entries/${entryId}`, { method: "DELETE" }),
  investigations: () => request<InvestigationSummary[]>("/investigations"),
  investigation: (id: number) => request<Investigation>(`/investigations/${id}`),
  createInvestigation: (body: {
    title: string;
    description: string | null;
    subject_chain: string | null;
    subject_address: string | null;
  }) => request<Investigation>("/investigations", { method: "POST", ...json(body) }),
  updateInvestigation: (id: number, status: InvestigationStatus) =>
    request<Investigation>(`/investigations/${id}`, { method: "PATCH", ...json({ status }) }),
  // ---- intelligence (Stage 3): computed from stored observations only
  intelStatus: () => request<IntelStatus>("/intelligence/status"),
  intelProfile: (chain: string, address: string) =>
    request<IntelProfile>(`/intelligence/wallets/${enc(chain)}/${enc(address)}/profile`),
  intelBehaviour: (chain: string, address: string) =>
    request<Behaviour>(`/intelligence/wallets/${enc(chain)}/${enc(address)}/behaviour`),
  analyze: (chain: string, address: string) =>
    request<AnalysisRun & IntelEnvelope>(`/intelligence/analyze/${enc(chain)}/${enc(address)}`, { method: "POST" }),
  anomalies: (chain: string, address: string) =>
    request<AnomalyPage>(`/intelligence/anomalies?limit=50&chain=${enc(chain)}&address=${enc(address)}`),
  flows: (chain: string, address: string) => request<Flows>(`/intelligence/flows/${enc(chain)}/${enc(address)}`),
  graph: (chain: string, address: string, depth: number) =>
    request<Graph>(`/intelligence/graph/${enc(chain)}/${enc(address)}?depth=${depth}`),
  opportunities: () => request<OpportunityPage>("/intelligence/opportunities?limit=50"),
  refreshOpportunities: (chain: string) =>
    request<OpportunityRefresh>(`/intelligence/opportunities/refresh/${enc(chain)}`, { method: "POST" }),
  verifyTx: (chain: string, txId: string) =>
    request<VerifyResult>(`/intelligence/verification/${enc(chain)}/${enc(txId)}`, { method: "POST" }),
  verifyWallet: (chain: string, address: string) =>
    request<WalletVerify>(`/intelligence/verification/${enc(chain)}/wallet/${enc(address)}?limit=5`, { method: "POST" }),
  attachFinding: (findingId: string, investigationId: number) =>
    request<IntelEnvelope>(`/intelligence/findings/${enc(findingId)}/attach`, {
      method: "POST",
      ...json({ investigation_id: investigationId }),
    }),
};

export type { FindingSummary };
