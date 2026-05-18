"""
Motor de limites operacionais por IP peer e por conta individual.

Hierarquia de limites (mais restritivo prevalece):
  1. Limite global da plataforma
  2. Limite por IP peer (ISPB da remetente)
  3. Limite por conta individual

Dimensões checadas:
  - Valor máximo por transação única
  - Volume acumulado diário
  - Número de transações diárias
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import TransferLimitConfig, TransferRecord

logger = logging.getLogger(__name__)

# ── defaults globais ─────────────────────────────────────────────────────────
GLOBAL_MAX_SINGLE_TX   = Decimal("500000.00")
GLOBAL_MAX_DAILY_VOL   = Decimal("2000000.00")
GLOBAL_MAX_DAILY_COUNT = 500


@dataclass
class LimitResult:
    passed: bool
    reason: str = ""
    limit_type: str = ""


async def _get_limit_config(
    session: AsyncSession, scope: str, scope_id: str
) -> TransferLimitConfig | None:
    result = await session.execute(
        select(TransferLimitConfig).where(
            TransferLimitConfig.scope == scope,
            TransferLimitConfig.scope_id == scope_id,
            TransferLimitConfig.active == True,
        )
    )
    return result.scalar_one_or_none()


async def _daily_volume_for(session: AsyncSession, column_name: str, value: str) -> Decimal:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    col   = getattr(TransferRecord, column_name)
    result = await session.execute(
        select(func.coalesce(func.sum(TransferRecord.amount), 0)).where(
            col == value,
            TransferRecord.created_at >= today,
            TransferRecord.status.in_(["PROCESSING", "SETTLED"]),
        )
    )
    return Decimal(str(result.scalar_one()))


async def _daily_count_for(session: AsyncSession, column_name: str, value: str) -> int:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    col   = getattr(TransferRecord, column_name)
    result = await session.execute(
        select(func.count()).select_from(TransferRecord).where(
            col == value,
            TransferRecord.created_at >= today,
        )
    )
    return result.scalar_one()


async def check_limits(
    session: AsyncSession,
    amount: Decimal,
    sender_ispb: str,
    sender_account: str,
) -> LimitResult:

    checks: list[tuple[str, str, Decimal, Decimal, int]] = []
    # (scope_label, scope_id, max_single, max_daily_vol, max_daily_count)

    # 1. Global
    checks.append(("global", "global", GLOBAL_MAX_SINGLE_TX, GLOBAL_MAX_DAILY_VOL, GLOBAL_MAX_DAILY_COUNT))

    # 2. Por IP peer
    peer_cfg = await _get_limit_config(session, "peer_ispb", sender_ispb)
    if peer_cfg:
        checks.append((
            "peer_ispb", sender_ispb,
            Decimal(str(peer_cfg.max_single_tx)),
            Decimal(str(peer_cfg.max_daily_volume)),
            peer_cfg.max_daily_count,
        ))

    # 3. Por conta
    acct_cfg = await _get_limit_config(session, "account", sender_account)
    if acct_cfg:
        checks.append((
            "account", sender_account,
            Decimal(str(acct_cfg.max_single_tx)),
            Decimal(str(acct_cfg.max_daily_volume)),
            acct_cfg.max_daily_count,
        ))

    for scope_label, scope_id, max_single, max_daily_vol, max_daily_count in checks:
        # valor único
        if amount > max_single:
            return LimitResult(
                passed=False,
                reason=f"Valor R$ {amount:.2f} excede limite por transação R$ {max_single:.2f} [{scope_label}:{scope_id}]",
                limit_type="single_tx",
            )

        # volume diário
        col = "sender_ispb" if scope_label == "peer_ispb" else (
              "sender_account" if scope_label == "account" else None)

        if col:
            vol = await _daily_volume_for(session, col, scope_id)
            if vol + amount > max_daily_vol:
                return LimitResult(
                    passed=False,
                    reason=f"Volume diário R$ {vol + amount:.2f} excederia limite R$ {max_daily_vol:.2f} [{scope_label}:{scope_id}]",
                    limit_type="daily_volume",
                )

            count = await _daily_count_for(session, col, scope_id)
            if count + 1 > max_daily_count:
                return LimitResult(
                    passed=False,
                    reason=f"Contagem diária {count + 1} excederia limite {max_daily_count} [{scope_label}:{scope_id}]",
                    limit_type="daily_count",
                )

    return LimitResult(passed=True)
