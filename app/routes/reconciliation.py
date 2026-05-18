"""
Conciliação bilateral.

Fluxo:
  1. IP-A chama POST /reconciliation/request com período.
  2. IP-B responde com sua lista de transferências do período.
  3. IP-A compara localmente (GET /reconciliation/compare).
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, list_transfers
from app.models import (
    ReconciliationRequest,
    ReconciliationResponse,
    ReconciliationEntry,
    TransferStatus,
)
from app.security import verify_request

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation"])


@router.post(
    "",
    response_model=ReconciliationResponse,
    summary="Retorna lista de transferências do período para conciliação",
)
async def get_reconciliation(
    body:     ReconciliationRequest,
    session:  AsyncSession = Depends(get_session),
    peer_key: str          = Depends(
        lambda req: verify_request(req, settings.known_peers)
    ),
):
    offset = (body.page - 1) * body.page_size
    total, records = await list_transfers(
        session,
        date_from=body.date_from,
        date_to=body.date_to,
        offset=offset,
        limit=body.page_size,
    )

    entries: list[ReconciliationEntry] = []
    total_debit  = Decimal("0")
    total_credit = Decimal("0")

    for r in records:
        entries.append(ReconciliationEntry(
            transfer_id=r.transfer_id,
            amount=r.amount,
            status=TransferStatus(r.status),
            created_at=r.created_at,
            settled_at=r.settled_at,
        ))
        if r.direction == "SENT":
            total_debit += Decimal(str(r.amount))
        else:
            total_credit += Decimal(str(r.amount))

    return ReconciliationResponse(
        total=total,
        page=body.page,
        page_size=body.page_size,
        entries=entries,
        total_debit=total_debit,
        total_credit=total_credit,
    )
