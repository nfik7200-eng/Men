# AI Research Methodology (Stage 5)

Version 1.0 (2026-11-04). Code: `bip/research/`. API: `/api/v1/research`.

## 1. Orchestration

```
request ─► interpret (deterministic) ─► plan (fixed steps) ─► specialist tool calls ─► claims
                                                                                      │
                                    optional LLM synthesis over the claims (data) ◄───┘
                                                     │ validated + screened
                                                     ▼
                                     report (JSON + Markdown) + persisted claims
```

**Lifecycle.**
- States: `queued` → `planning` → `classifying` / `collecting_evidence` → `analysing` → `synthesizing` → `completed` | `completed_with_limitations` | `failed` | `cancelled`.
- A run never leaves a terminal state.
- On startup, unfinished runs are marked `failed` ("interrupted"); they are never resumed silently.
- A rerun creates a new run linked by `parent_run_id`. The original run, its claims and its report are kept.

**Interpretation.**
- The address and chain are validated with BIP's contracts.
- Control characters are removed from the question.
- Focus areas come from the request (a fixed list) or from keywords in the question.
- `classification` and `evidence` are always included.

**Planning** is deterministic. Each focus area maps to fixed (specialist, tool) steps. The plan is stored before any tool runs, and steps beyond the tool-call budget are listed as skipped.

## 2. Specialists and tool permissions

| Specialist | Tools |
|---|---|
| Classification analyst | `wallet_classification`, `wallet_features` |
| Behaviour analyst | `wallet_profile`, `behaviour_rules`, `wallet_features` |
| Flow analyst | `capital_flows` |
| Contract / token analyst | `wallet_profile` |
| Graph analyst | `relationship_graph`, `wallet_profile` |
| Anomaly analyst | `anomaly_detectors` |
| Opportunity analyst | `opportunity_leads` |
| Evidence reviewer | `provider_status`, `transaction_evidence` |

**Tool rules.**
- Tools are read-only functions over BIP's own data, scoped to **the run's subject wallet**.
- `transaction_evidence` only returns the subject's own stored records; any other id is reported as rejected.
- There is no SQL, shell, HTTP or raw RPC tool.
- The permission table is in code and checked on every call.
- The LLM is never offered tools, so injected text cannot expand permissions.

**Provider awareness.** `provider_status` reports, per provider:
- connectivity and reliability from Stage 4 probes;
- whether the data origin is an offline fixture.

When no live probe exists, the report states "provider capabilities are unverified".

## 3. Claims and evidence

| Kind | Status assigned by BIP |
|---|---|
| `blockchain_observation` | From evidence verification: `corroborated` when the evidence is corroborated, otherwise `unverified`; `conflicted` / `invalidated` passed through |
| `deterministic_rule` | Always `inferred` |
| `model_prediction` | Always `inferred`; confidence label from the ML status (high / medium / low) |
| `statistical_signal` | From the verification of its supporting evidence |
| `ai_interpretation` | Always `inferred` |
| `hypothesis` | `insufficient_evidence` (or `inferred` for fully corroborated research leads) |
| `missing_information` | `insufficient_evidence` |

**Rules that always hold:**
- Every claim lists evidence references (transaction, transfer, feature, classification, detector, graph edge, or claim ids for AI output).
- `verified` is reserved for independently attributable evidence. BIP never assigns it automatically.
- Model predictions are not independent evidence. Corroboration follows the infrastructure-group rules (ADR-021).
- Confidence comes from evidence quality and model validation, never from how confident the text sounds.

## 4. LLM synthesis

**Providers** (`bip/research/llm.py`; nothing is called unless configured):
- `none` is the default.
- `ollama` accepts a **loopback URL only**. It must use an already-installed model (`BIP_AI__OLLAMA_MODEL`), and BIP never pulls or changes models.
- `openai_compatible` uses https and the SSRF URL check. Its key is read from the environment variable named by `BIP_AI__API_KEY_ENV`. No paid service is required.

**Input.** The model receives a JSON bundle (question, subject, claims with ids, kinds and statuses). A system prompt states that everything in the bundle is data, and that it cannot call tools, change statuses or add evidence.

**Output acceptance.** The answer must be a JSON object with:
- `executive_summary`;
- `interpretations` and `hypotheses`, each citing ≥ 1 existing claim id;
- `next_steps`.

**Discarded statements** (counted in the limitations):
- unknown or uncited claim references;
- statements that fail the **screen**: instruction-like text ("ignore previous…", "system prompt", "call the tool"), claims of verification ("verified", "confirmed", "definitely"), identity attribution ("belongs to", "owned by", "operated by"), financial advice ("buy", "sell", "invest", "price target"), secrets ("private key", "seed phrase"), and code blocks.

**Failure handling.**
- Without an available provider, or if synthesis fails, the report is deterministic and the run ends `completed_with_limitations` with the reason.
- Model usage records keep only provider, model, sizes, duration and outcome. Prompts and outputs are not logged.

## 5. Reports

Reports are stored once per run (`research_reports`) as JSON and Markdown. They contain:
- executive summary, with its source (AI or deterministic);
- question and subject;
- observation scope and coverage;
- classification (account type, roles, model and rules references, anomaly);
- sections by kind: observations, classification, signals, AI interpretations, hypotheses, missing information;
- contradictions and claim-status counts;
- next steps and limitations;
- a legend, evidence references, and the disclaimer ("no identity attribution and no financial advice; predictions and interpretations are not facts").

Reports are capped at 30 KB: the longest section is trimmed first, and the report is flagged `truncated`.

## 6. Budgets (server-enforced ceilings; requests cannot raise them)

| Limit | Ceiling (`BIP_AI__*`) |
|---|---|
| Time per investigation | 120 s |
| Tool calls | 20 |
| Model calls | 8 (synthesis currently uses 1) |
| Tool output | 100 KB (larger output replaced by a truncation marker) |
| Report | 30 KB |
| Concurrent investigations | 2 (409 `research_busy`) |
| Model response | 16 KB default (≤ 64 KB) |

Cancellation is checked before every step. A cancelled run keeps everything recorded up to that point.

## 7. What is not implemented

- **LLM-driven tool selection.** The planner is deterministic by design. An LLM-planned mode would need its own ADR and threat model.
- **Investigating multiple wallets per run**, or following counterparties automatically.
- **Live provider calls from research.** Research reads stored data only; corroboration stays an explicit action.
- **Persisted templates.** There is no `research_templates` table; focus areas serve that role.
