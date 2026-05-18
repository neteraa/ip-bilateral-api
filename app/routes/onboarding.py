"""
Onboarding de nova IP peer.

Fluxo:
  1. Admin chama POST /onboarding/peers com os dados da outra IP.
  2. Sistema salva na tabela peer_ips e registra no audit trail.
  3. A partir daí, a IP é reconhecida nas verificações HMAC.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditEvent, log_event
from app.database import get_session
from app.db_models import PeerIP
from app.routes.admin import require_admin_key

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


class PeerOnboardRequest(BaseModel):
    ispb:          str = Field(..., min_length=8, max_length=8, pattern=r"^\d{8}$")
    name:          str = Field(..., min_length=3, max_length=200)
    api_key_id:    str = Field(..., min_length=4, max_length=100)
    api_secret:    str = Field(..., min_length=16, max_length=255)
    webhook_url:   str = Field(..., max_length=500)
    base_url:      str = Field(..., max_length=500)
    contact_email: str | None = None
    notes:         str | None = None


class PeerOnboardResponse(BaseModel):
    id:            int
    ispb:          str
    name:          str
    api_key_id:    str
    webhook_url:   str
    base_url:      str
    status:        str
    onboarded_at:  datetime


class PeerSummary(BaseModel):
    id:           int
    ispb:         str
    name:         str
    api_key_id:   str
    status:       str
    onboarded_at: datetime


@router.post(
    "/peers",
    response_model=PeerOnboardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastra uma nova IP peer (requer chave de admin)",
)
async def onboard_peer(
    body:    PeerOnboardRequest,
    session: AsyncSession = Depends(get_session),
    _:       str          = Depends(require_admin_key),
):
    # Verifica duplicidade
    existing = await session.execute(
        select(PeerIP).where(
            (PeerIP.ispb == body.ispb) | (PeerIP.api_key_id == body.api_key_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"IP com ISPB {body.ispb} ou key_id {body.api_key_id} já cadastrada",
        )

    peer = PeerIP(
        ispb=body.ispb,
        name=body.name,
        api_key_id=body.api_key_id,
        api_secret=body.api_secret,
        webhook_url=body.webhook_url,
        base_url=body.base_url,
        contact_email=body.contact_email,
        notes=body.notes,
        status="ACTIVE",
        onboarded_at=datetime.utcnow(),
    )
    session.add(peer)
    await session.commit()
    await session.refresh(peer)

    await log_event(
        session,
        AuditEvent.PEER_ONBOARDED,
        {"ispb": peer.ispb, "name": peer.name, "api_key_id": peer.api_key_id},
        actor="admin",
    )

    return PeerOnboardResponse(
        id=peer.id,
        ispb=peer.ispb,
        name=peer.name,
        api_key_id=peer.api_key_id,
        webhook_url=peer.webhook_url,
        base_url=peer.base_url,
        status=peer.status,
        onboarded_at=peer.onboarded_at,
    )


@router.get(
    "/peers",
    response_model=list[PeerSummary],
    summary="Lista todas as IPs cadastradas",
)
async def list_peers(
    session: AsyncSession = Depends(get_session),
    _:       str          = Depends(require_admin_key),
):
    result = await session.execute(select(PeerIP))
    peers  = result.scalars().all()
    return [
        PeerSummary(
            id=p.id, ispb=p.ispb, name=p.name,
            api_key_id=p.api_key_id, status=p.status,
            onboarded_at=p.onboarded_at,
        )
        for p in peers
    ]


@router.patch(
    "/peers/{ispb}/suspend",
    summary="Suspende uma IP peer",
)
async def suspend_peer(
    ispb:    str,
    session: AsyncSession = Depends(get_session),
    _:       str          = Depends(require_admin_key),
):
    result = await session.execute(select(PeerIP).where(PeerIP.ispb == ispb))
    peer   = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="IP não encontrada")

    peer.status     = "SUSPENDED"
    peer.updated_at = datetime.utcnow()
    await session.commit()

    await log_event(
        session, AuditEvent.PEER_SUSPENDED,
        {"ispb": ispb}, actor="admin",
    )
    return {"ispb": ispb, "status": "SUSPENDED"}
