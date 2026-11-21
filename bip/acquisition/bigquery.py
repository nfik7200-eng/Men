"""BigQuery sandbox integration (free tier). Dry-run first; hard byte cap; client is injected.

No google SDK is imported here (no new dependency, no credentials in code). A concrete client
implementing :class:`BigQueryClient` is supplied by the caller, so tests use a fake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

SANDBOX_MONTHLY_FREE_BYTES = 1 * 1024**4  # 1 TiB/month free query tier
_FORBIDDEN = re.compile(r"\b(insert|update|delete|merge|drop|create|alter|truncate|grant)\b", re.I)


class BigQueryClient(Protocol):
    def dry_run_bytes(self, sql: str) -> int: ...
    def run(self, sql: str) -> list[dict[str, Any]]: ...


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class BigQuerySandbox:
    client: BigQueryClient
    per_query_cap_bytes: int = 10 * 1024**3
    monthly_cap_bytes: int = SANDBOX_MONTHLY_FREE_BYTES // 2
    used_bytes: int = 0
    log: list[dict[str, Any]] = field(default_factory=lambda: [])

    def query(self, sql: str) -> list[dict[str, Any]]:
        if not sql.lstrip().lower().startswith(("select", "with")) or _FORBIDDEN.search(sql):
            raise ValueError("only read-only SELECT queries are allowed")
        est = self.client.dry_run_bytes(sql)
        if est > self.per_query_cap_bytes:
            raise BudgetExceededError(f"estimate {est} exceeds per-query cap")
        if self.used_bytes + est > self.monthly_cap_bytes:
            raise BudgetExceededError("monthly sandbox budget would be exceeded")
        rows = self.client.run(sql)
        self.used_bytes += est
        self.log.append(
            {"provider_id": "bigquery_sandbox", "bytes_estimate": est, "rows": len(rows)}
        )
        return rows
