"""
Rotas administrativas — blocklist, limites, audit, health da chain.
Protegidas por chave de admin (X-Admin-Key header).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditEvent, log_event, verify_chain
from app.compliance.blocklist import add_to_blocklist, remove_from_blocklist
from app.config import settings
from app.database import get_session, list_limits
from app.db_models import AuditLog, BlocklistEntry, TransferLimitConfig

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── autenticação admin ───────────────────────────────────────────────────────

async def require_admin_key(x_admin_key: str = Header(...)):
    if x_admin_key != settings.admin_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chave de admin inválida",
        )
    return x_admin_key


# ── Blocklist ────────────────────────────────────────────────────────────────

class BlocklistAddRequest(BaseModel):
    value:       str = Field(..., min_length=1, max_length=100)
    entry_type:  str = Field(..., pattern=r"^(CPF|CNPJ|ISPB|ACCOUNT)$")
    reason:      str = Field(..., min_length=3, max_length=255)
    list_source: str = "manual"


@router.post("/blocklist", status_code=201, summary="Adiciona à blocklist")
async def add_blocklist(
    body:    BlocklistAddRequest,
    session: AsyncSession = Depends(get_session),
    admin:   str          = Depends(require_admin_key),
):
    entry = await add_to_blocklist(
        session,
        value=body.value,
        entry_type=body.entry_type,
        reason=body.reason,
        added_by="admin",
        list_source=body.list_source,
    )
    await log_event(
        session, AuditEvent.BLOCKLIST_ADD,
        {"value": body.value, "type": body.entry_type, "reason": body.reason},
        actor="admin",
    )
    return {"id": entry.id, "value": entry.value, "active": entry.active}


@router.delete("/blocklist/{entry_id}", summary="Remove da blocklist")
async def remove_blocklist(
    entry_id: int,
    session:  AsyncSession = Depends(get_session),
    admin:    str          = Depends(require_admin_key),
):
    ok = await remove_from_blocklist(session, entry_id, removed_by="admin")
    if not ok:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    await log_event(
        session, AuditEvent.BLOCKLIST_REMOVE,
        {"entry_id": entry_id}, actor="admin",
    )
    return {"removed": True}


@router.get("/blocklist", summary="Lista blocklist ativa")
async def list_blocklist(
    session: AsyncSession = Depends(get_session),
    admin:   str          = Depends(require_admin_key),
):
    result = await session.execute(
        select(BlocklistEntry).where(BlocklistEntry.active == True).order_by(BlocklistEntry.created_at.desc())
    )
    entries = result.scalars().all()
    return [
        {"id": e.id, "value": e.value, "type": e.entry_type,
         "reason": e.reason, "source": e.list_source, "created_at": e.created_at}
        for e in entries
    ]


# ── Limites ──────────────────────────────────────────────────────────────────

class LimitSetRequest(BaseModel):
    scope:            str     = Field(..., pattern=r"^(global|peer_ispb|account)$")
    scope_id:         str     = Field(..., min_length=1, max_length=100)
    max_single_tx:    Decimal = Field(..., gt=0)
    max_daily_volume: Decimal = Field(..., gt=0)
    max_daily_count:  int     = Field(..., gt=0)


@router.post("/limits", status_code=201, summary="Define ou atualiza limite operacional")
async def set_limit(
    body:    LimitSetRequest,
    session: AsyncSession = Depends(get_session),
    admin:   str          = Depends(require_admin_key),
):
    # Desativa limite anterior do mesmo scope/scope_id
    result = await session.execute(
        select(TransferLimitConfig).where(
            TransferLimitConfig.scope    == body.scope,
            TransferLimitConfig.scope_id == body.scope_id,
            TransferLimitConfig.active   == True,
        )
    )
    old = result.scalar_one_or_none()
    if old:
        old.active     = False
        old.updated_at = datetime.utcnow()

    cfg = TransferLimitConfig(
        scope=body.scope,
        scope_id=body.scope_id,
        max_single_tx=body.max_single_tx,
        max_daily_volume=body.max_daily_volume,
        max_daily_count=body.max_daily_count,
        active=True,
        created_by="admin",
        created_at=datetime.utcnow(),
    )
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    await log_event(
        session, AuditEvent.LIMIT_CHANGED,
        {"scope": body.scope, "scope_id": body.scope_id,
         "max_single_tx": str(body.max_single_tx),
         "max_daily_volume": str(body.max_daily_volume),
         "max_daily_count": body.max_daily_count},
        actor="admin",
    )
    return {"id": cfg.id, "scope": cfg.scope, "scope_id": cfg.scope_id, "active": cfg.active}


@router.get("/limits", summary="Lista limites configurados")
async def get_limits(
    session: AsyncSession = Depends(get_session),
    admin:   str          = Depends(require_admin_key),
):
    limits = await list_limits(session)
    return [
        {"id": l.id, "scope": l.scope, "scope_id": l.scope_id,
         "max_single_tx": str(l.max_single_tx),
         "max_daily_volume": str(l.max_daily_volume),
         "max_daily_count": l.max_daily_count}
        for l in limits
    ]


# ── Audit Trail ──────────────────────────────────────────────────────────────

@router.get("/audit", summary="Consulta audit trail")
async def get_audit(
    session:   AsyncSession = Depends(get_session),
    admin:     str          = Depends(require_admin_key),
    page:      int = 1,
    page_size: int = 50,
    event_type: Optional[str] = None,
    transfer_id: Optional[str] = None,
):
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if event_type:
        q = q.where(AuditLog.event_type == event_type)
    if transfer_id:
        q = q.where(AuditLog.transfer_id == transfer_id)
    q = q.offset((page - 1) * page_size).limit(page_size)

    result = await session.execute(q)
    entries = result.scalars().all()
    return [
        {"id": e.id, "event_type": e.event_type, "transfer_id": e.transfer_id,
         "actor": e.actor, "created_at": e.created_at, "this_hash": e.this_hash}
        for e in entries
    ]


@router.get("/audit/verify", summary="Verifica integridade do audit trail (hash chain)")
async def audit_verify(
    session: AsyncSession = Depends(get_session),
    admin:   str          = Depends(require_admin_key),
):
    ok, message = await verify_chain(session)
    await log_event(session, AuditEvent.CHAIN_VERIFIED, {"result": ok, "message": message}, actor="admin")
    return {"integrity_ok": ok, "message": message}
