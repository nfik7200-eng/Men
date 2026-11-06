import type {
  AnalysisRun,
  Classification,
  MLModel,
  ResearchConfig,
  ResearchPreview,
  ResearchReport,
  ResearchRun,
  TrainingRun,
  ProbePreview,
  ProbeRun,
  ProbeRunPage,
  ProviderProfileDetail,
  ProviderProfiles,
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
  WalletStatus,
  JsonObject,
  LabelInput,
  LabelPage,
  ReviewInput,
  CaseResearchLink,
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

  /** True when the backend itself could not be reached (network failure, or a proxy gateway error). */
  get isConnectionError(): boolean {
    return this.status === 0 || this.code === "backend_unreachable";
  }
}

const UNREACHABLE = "Cannot reach the BIP backend. Is it running?";
const GATEWAY_STATUSES = new Set([502, 503, 504]);

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
    throw new ApiError(0, "connection_failed", UNREACHABLE);
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
    // A gateway status with no BIP error envelope comes from a proxy in front of the backend
    // (for example the Vite dev proxy when the backend is not running), not from BIP itself.
    if (GATEWAY_STATUSES.has(response.status) && typeof env.error?.code !== "string") {
      throw new ApiError(response.status, "backend_unreachable", UNREACHABLE);
    }
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
  walletStatus: (chain: string, address: string) =>
    request<WalletStatus>(`/wallets/${enc(chain)}/${enc(address)}/status`),
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
  // ---- ML classification + AI research (Stage 5)
  classify: (chain: string, address: string) =>
    request<Classification>(`/ml/classify/${enc(chain)}/${enc(address)}`, { method: "POST" }),
  classifications: (chain: string, address: string) =>
    request<{ items: Classification[]; total: number }>(`/ml/classifications/${enc(chain)}/${enc(address)}?limit=10`),
  mlModels: () => request<{ items: MLModel[]; active: Record<string, MLModel | null> }>("/ml/models"),
  mlModel: (name: string, version: string) => request<MLModel>(`/ml/models/${enc(name)}/${enc(version)}`),
  trainingRuns: () => request<{ items: TrainingRun[] }>("/ml/training-runs?limit=10"),
  train: (chain: string, source: "stored_labels" | "synthetic") =>
    request<{ status: string; message: string }>("/ml/training-runs", { method: "POST", ...json({ chain, source }) }),
  researchConfig: () => request<ResearchConfig>("/research/config"),
  researchPreview: (body: { chain: string; address: string; question: string; focus?: string[] }) =>
    request<ResearchPreview>("/research/preview", { method: "POST", ...json(body) }),
  researchStart: (body: { chain: string; address: string; question: string; focus?: string[] }) =>
    request<ResearchRun>("/research/runs", { method: "POST", ...json(body) }),
  researchRun: (id: string) => request<ResearchRun>(`/research/runs/${enc(id)}`),
  researchRuns: (chain: string, address: string) =>
    request<{ items: ResearchRun[]; total: number }>(`/research/runs?limit=10&chain=${enc(chain)}&address=${enc(address)}`),
  researchCancel: (id: string) => request<{ cancel_requested: boolean }>(`/research/runs/${enc(id)}/cancel`, { method: "POST" }),
  researchReport: (id: string) => request<ResearchReport>(`/research/runs/${enc(id)}/report`),
  // ---- provider intelligence (Stage 4). Only probeRun() contacts providers (server-bounded).
  researchHistory: (q: { chain?: string; address?: string; limit?: number; offset?: number } = {}) =>
    request<{ items: ResearchRun[]; total: number; limit: number; offset: number }>(
      `/research/runs?limit=${q.limit ?? 20}&offset=${q.offset ?? 0}` +
        (q.chain ? `&chain=${enc(q.chain)}` : "") +
        (q.address ? `&address=${enc(q.address)}` : ""),
    ),
  researchRerun: (id: string) => request<ResearchRun>(`/research/runs/${enc(id)}/rerun`, { method: "POST" }),
  labels: (limit = 50, offset = 0) => request<LabelPage>(`/ml/labels?limit=${limit}&offset=${offset}`),
  addLabels: (labels: LabelInput[]) => request<JsonObject>("/ml/labels", { method: "POST", ...json({ labels }) }),
  reviewLabel: (id: number, body: ReviewInput) =>
    request<JsonObject>(`/ml/labels/${id}/reviews`, { method: "POST", ...json(body) }),
  taxonomy: () => request<JsonObject>("/ml/taxonomy"),
  walletFeatures: (chain: string, address: string) =>
    request<JsonObject>(`/ml/features/${enc(chain)}/${enc(address)}`),
  capabilityHistory: (id: string) => request<JsonObject>(`/providers/${enc(id)}/capabilities/history`),
  providerReliability: (id: string) => request<JsonObject>(`/providers/${enc(id)}/reliability`),
  providerConsistency: (id: string) => request<JsonObject>(`/providers/${enc(id)}/consistency`),
  caseResearchRuns: (id: number) => request<{ items: CaseResearchLink[] }>(`/investigations/${id}/research-runs`),
  linkResearchRun: (id: number, runId: string, note: string | null) =>
    request<JsonObject>(`/investigations/${id}/research-runs`, { method: "POST", ...json({ run_id: runId, note }) }),
  unlinkResearchRun: (id: number, runId: string) =>
    request<undefined>(`/investigations/${id}/research-runs/${enc(runId)}`, { method: "DELETE" }),
  providerProfiles: () => request<ProviderProfiles>("/providers/profiles"),
  providerProfile: (id: string) => request<ProviderProfileDetail>(`/providers/${enc(id)}/profile`),
  probePreview: (providers: string[]) =>
    request<ProbePreview>(`/providers/probe/preview${providers.length ? "?" + providers.map((p) => `provider=${enc(p)}`).join("&") : ""}`),
  probeRun: (providers: string[]) =>
    request<ProbeRun>("/providers/probe-runs", { method: "POST", ...json(providers.length ? { providers } : {}) }),
  probeRuns: () => request<ProbeRunPage>("/providers/probe-runs?limit=10"),
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
