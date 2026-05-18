"""
Módulo de contas pré-financiadas (Pre-funded Accounts).

Modelo de liquidação recomendado para parceiros novos:
o parceiro deposita um saldo adiantado; cada transferência
debita desse saldo. Quando cai abaixo do mínimo, um alerta
é emitido para o parceiro recarregar.

Rotas (todas protegidas por X-Admin-Key):
  POST   /prefunded/{ispb}                — cria conta pré-financiada
  GET    /prefunded/{ispb}                — consulta saldo atual
  POST   /prefunded/{ispb}/topup          — adiciona saldo (depósito recebido)
  POST   /prefunded/{ispb}/debit          — debita manualmente (admin)
  GET    /prefunded/{ispb}/transactions   — extrato completo
  GET    /prefunded                       — lista todas as contas
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditEvent, log_event
from app.database import get_session
from app.db_models import PrefundedAccount, PrefundedTransaction
from app.routes.admin import require_admin_key

router = APIRouter(prefix="/prefunded", tags=["Pre-funded Accounts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateAccountIn(BaseModel):
    ispb:        str = Field(..., min_length=8, max_length=8)
    currency:    str = Field("USD", max_length=3)
    min_balance: Decimal = Field(Decimal("1000.00"), ge=0)

class TopupIn(BaseModel):
    amount:      Decimal = Field(..., gt=0)
    description: Optional[str] = None

class DebitIn(BaseModel):
    amount:      Decimal = Field(..., gt=0)
    description: Optional[str] = None
    transfer_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_account(ispb: str, session: AsyncSession) -> PrefundedAccount:
    row = await session.scalar(
        select(PrefundedAccount).where(PrefundedAccount.ispb == ispb)
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"No prefunded account for ISPB {ispb}")
    return row


async def _record_tx(
    session: AsyncSession,
    ispb: str,
    tx_type: str,
    amount: Decimal,
    balance_after: Decimal,
    currency: str,
    transfer_id: Optional[str] = None,
    description: Optional[str] = None,
    created_by: str = "admin",
) -> PrefundedTransaction:
    tx = PrefundedTransaction(
        ispb=ispb,
        type=tx_type,
        amount=amount,
        balance_after=balance_after,
        currency=currency,
        transfer_id=transfer_id,
        description=description,
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    session.add(tx)
    return tx


# ── Rotas ─────────────────────────────────────────────────────────────────────

@router.post("/{ispb}", status_code=status.HTTP_201_CREATED,
             summary="Cria conta pré-financiada para um peer")
async def create_account(
    ispb: str,
    body: CreateAccountIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    existing = await session.scalar(
        select(PrefundedAccount).where(PrefundedAccount.ispb == ispb)
    )
    if existing:
        raise HTTPException(status_code=409, detail="Account already exists")
    acc = PrefundedAccount(
        ispb=ispb,
        currency=body.currency,
        balance=Decimal("0.00"),
        min_balance=body.min_balance,
        created_at=datetime.utcnow(),
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    await log_event(session, AuditEvent.PEER_ONBOARDED,
        {"action": "prefunded_account_created", "ispb": ispb, "currency": body.currency},
        actor="admin")
    return {"ispb": ispb, "currency": acc.currency, "balance": float(acc.balance),
            "min_balance": float(acc.min_balance), "status": "created"}


@router.get("", summary="Lista todas as contas pré-financiadas")
async def list_accounts(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    rows = (await session.scalars(select(PrefundedAccount))).all()
    result = []
    for a in rows:
        bal = float(a.balance)
        mn  = float(a.min_balance)
        result.append({
            "ispb": a.ispb,
            "currency": a.currency,
            "balance": bal,
            "min_balance": mn,
            "alert": bal < mn,
            "created_at": a.created_at,
            "updated_at": a.updated_at,
        })
    return result


@router.get("/{ispb}", summary="Consulta saldo de um peer")
async def get_balance(
    ispb: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    acc = await _get_account(ispb, session)
    bal = float(acc.balance)
    mn  = float(acc.min_balance)
    return {
        "ispb": acc.ispb,
        "currency": acc.currency,
        "balance": bal,
        "min_balance": mn,
        "alert": bal < mn,
        "alert_message": f"⚠️ Balance below minimum! Ask {ispb} to top up." if bal < mn else None,
        "updated_at": acc.updated_at,
    }


@router.post("/{ispb}/topup", summary="Adiciona saldo (depósito recebido do parceiro)")
async def topup(
    ispb: str,
    body: TopupIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    acc = await _get_account(ispb, session)
    acc.balance   = Decimal(str(acc.balance)) + body.amount
    acc.updated_at = datetime.utcnow()
    new_bal = Decimal(str(acc.balance))
    await _record_tx(session, ispb, "TOPUP", body.amount, new_bal,
                     acc.currency, description=body.description or "Manual top-up")
    await session.commit()
    await log_event(session, AuditEvent.PEER_ONBOARDED,
        {"action": "prefunded_topup", "ispb": ispb, "amount": float(body.amount), "balance": float(new_bal)},
        actor="admin")
    return {
        "ispb": ispb,
        "topup_amount": float(body.amount),
        "new_balance": float(new_bal),
        "currency": acc.currency,
        "alert": float(new_bal) < float(acc.min_balance),
    }


@router.post("/{ispb}/debit", summary="Debita saldo manualmente (admin)")
async def debit(
    ispb: str,
    body: DebitIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    acc = await _get_account(ispb, session)
    current = Decimal(str(acc.balance))
    if body.amount > current:
        raise HTTPException(status_code=422,
            detail=f"Insufficient balance: {float(current)} {acc.currency} available")
    acc.balance    = current - body.amount
    acc.updated_at = datetime.utcnow()
    new_bal = Decimal(str(acc.balance))
    await _record_tx(session, ispb, "DEBIT", body.amount, new_bal,
                     acc.currency, transfer_id=body.transfer_id,
                     description=body.description or "Manual debit")
    await session.commit()
    return {
        "ispb": ispb,
        "debited": float(body.amount),
        "new_balance": float(new_bal),
        "currency": acc.currency,
        "alert": float(new_bal) < float(acc.min_balance),
    }


@router.get("/{ispb}/transactions", summary="Extrato da conta pré-financiada")
async def get_transactions(
    ispb: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    await _get_account(ispb, session)
    txs = (await session.scalars(
        select(PrefundedTransaction)
        .where(PrefundedTransaction.ispb == ispb)
        .order_by(PrefundedTransaction.created_at.desc())
    )).all()
    return [
        {
            "id": t.id,
            "type": t.type,
            "amount": float(t.amount),
            "balance_after": float(t.balance_after),
            "currency": t.currency,
            "transfer_id": t.transfer_id,
            "description": t.description,
            "created_by": t.created_by,
            "created_at": t.created_at,
        }
        for t in txs
    ]
