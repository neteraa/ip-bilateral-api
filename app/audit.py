"""
Audit Trail imutável com hash chain.

Cada evento tem:
  - ID sequencial
  - Timestamp UTC
  - Tipo de evento
  - Payload JSON
  - Hash SHA-256 do registro anterior (chain)
  - Hash SHA-256 deste registro

Isso garante que qualquer adulteração retroativa quebre a chain.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import AuditLog

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64


async def _last_hash(session: AsyncSession) -> str:
    result = await session.execute(
        select(AuditLog.this_hash).order_by(AuditLog.id.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    return row or GENESIS_HASH


def _compute_hash(prev_hash: str, event_type: str, timestamp: str, payload: str) -> str:
    raw = f"{prev_hash}|{event_type}|{timestamp}|{payload}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def log_event(
    session: AsyncSession,
    event_type: str,
    payload: dict[str, Any],
    actor: str = "system",
    transfer_id: str | None = None,
) -> AuditLog:
    prev_hash  = await _last_hash(session)
    ts         = datetime.utcnow()
    ts_str     = ts.isoformat()
    payload_str = json.dumps(payload, default=str, sort_keys=True)
    this_hash  = _compute_hash(prev_hash, event_type, ts_str, payload_str)

    entry = AuditLog(
        event_type=event_type,
        transfer_id=transfer_id,
        actor=actor,
        payload=payload_str,
        prev_hash=prev_hash,
        this_hash=this_hash,
        created_at=ts,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)

    logger.info("AUDIT [%s] event=%s actor=%s transfer=%s", entry.id, event_type, actor, transfer_id)
    return entry


async def verify_chain(session: AsyncSession) -> tuple[bool, str]:
    """
    Verifica integridade completa do audit trail.
    Retorna (True, "") se íntegro, ou (False, mensagem) se corrompido.
    """
    result = await session.execute(select(AuditLog).order_by(AuditLog.id.asc()))
    entries = list(result.scalars().all())

    prev_hash = GENESIS_HASH
    for entry in entries:
        if entry.prev_hash != prev_hash:
            return False, f"Chain quebrada no id={entry.id}: prev_hash esperado={prev_hash} recebido={entry.prev_hash}"

        expected = _compute_hash(prev_hash, entry.event_type, entry.created_at.isoformat(), entry.payload)
        if entry.this_hash != expected:
            return False, f"Hash inválido no id={entry.id}: esperado={expected} armazenado={entry.this_hash}"

        prev_hash = entry.this_hash

    return True, f"Chain íntegra — {len(entries)} eventos verificados"


# ── event type constants ─────────────────────────────────────────────────────

class AuditEvent:
    TRANSFER_RECEIVED     = "TRANSFER_RECEIVED"
    TRANSFER_SETTLED      = "TRANSFER_SETTLED"
    TRANSFER_FAILED       = "TRANSFER_FAILED"
    TRANSFER_REVERSED     = "TRANSFER_REVERSED"
    COMPLIANCE_BLOCKED    = "COMPLIANCE_BLOCKED"
    COMPLIANCE_AML_ALERT  = "COMPLIANCE_AML_ALERT"
    BLOCKLIST_ADD         = "BLOCKLIST_ADD"
    BLOCKLIST_REMOVE      = "BLOCKLIST_REMOVE"
    PEER_ONBOARDED        = "PEER_ONBOARDED"
    PEER_SUSPENDED        = "PEER_SUSPENDED"
    LIMIT_CHANGED         = "LIMIT_CHANGED"
    CHAIN_VERIFIED        = "CHAIN_VERIFIED"
    WEBHOOK_SENT          = "WEBHOOK_SENT"
    WEBHOOK_RECEIVED      = "WEBHOOK_RECEIVED"
    RECONCILIATION_RUN    = "RECONCILIATION_RUN"
