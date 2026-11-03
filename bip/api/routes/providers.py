from __future__ import annotations

from fastapi import APIRouter

from bip.api.deps import AppContext, Context
from bip.api.schemas import ProviderOut, ProviderSummary
from bip.providers.catalog import provider_status

router = APIRouter(tags=["providers"])


def provider_rows(ctx: AppContext) -> list[ProviderOut]:
    rows: list[ProviderOut] = []
    for st in provider_status(ctx.repo.provider_checks()):
        rows.append(
            ProviderOut(
                id=st.info.id,
                name=st.info.name,
                kind=st.info.kind,
                chains=st.info.chains,
                implemented_capabilities=(),
                adapter_implemented=st.adapter_implemented,
                configuration_status=st.configuration_status,
                connection_status=st.connection_status,
                status_reason=st.status_reason,
                research_grade=st.info.research_grade,
                docs_url=st.info.docs_url,
                last_check_at=st.last_check_at,
                last_success_at=st.last_success_at,
            )
        )
    return rows


def summarize(rows: list[ProviderOut]) -> ProviderSummary:
    return ProviderSummary(
        total=len(rows),
        configured=sum(r.configuration_status == "configured" for r in rows),
        adapters_implemented=sum(r.adapter_implemented for r in rows),
        connected=0,
    )


@router.get("/providers/status", response_model=list[ProviderOut])
def list_providers(ctx: Context) -> list[ProviderOut]:
    return provider_rows(ctx)
