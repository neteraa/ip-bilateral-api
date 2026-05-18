"""
AML (Anti-Money Laundering) — detecção de padrões suspeitos.

Regras implementadas (configuráveis via env/DB):
  R01 — Valor único acima do threshold de reporte (padrão R$ 2.000,00 — Res. COAF 36/2021)
  R02 — Velocity: muitas transações do mesmo documento em janela de 1h
  R03 — Velocity: volume acumulado do documento no dia acima do limite
  R04 — Valores "redondos" repetidos em sequência (smurfing)
  R05 — Valor exato logo abaixo do threshold (structuring)
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import TransferRecord
from app.models import TransferRequest

logger = logging.getLogger(__name__)

# ── limites padrão (sobrescritos via config/DB) ──────────────────────────────
COAF_THRESHOLD      = Decimal("2000.00")    # acima: registra + alerta
STRUCTURING_MARGIN  = Decimal("100.00")     # valores dentro desse range abaixo do threshold: alerta
VELOCITY_WINDOW_MIN = 60                    # minutos
VELOCITY_MAX_TX     = 10                    # máx transações na janela
DAILY_LIMIT_DEFAULT = Decimal("50000.00")   # limite diário por documento
SMURFING_REPEAT     = 3                     # nº de valores iguais na janela = suspeito


@dataclass
class AMLResult:
    passed: bool
    risk_level: str = "LOW"    # LOW | MEDIUM | HIGH | CRITICAL
    rules_triggered: list[str] = None
    reason: str = ""

    def __post_init__(self):
        if self.rules_triggered is None:
            self.rules_triggered = []


async def _count_recent_tx(session: AsyncSession, document: str, window_minutes: int) -> int:
    since = datetime.utcnow() - timedelta(minutes=window_minutes)
    result = await session.execute(
        select(func.count()).select_from(TransferRecord).where(
            TransferRecord.sender_document == document,
            TransferRecord.created_at >= since,
        )
    )
    return result.scalar_one()


async def _daily_volume(session: AsyncSession, document: str) -> Decimal:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.coalesce(func.sum(TransferRecord.amount), 0)).where(
            TransferRecord.sender_document == document,
            TransferRecord.created_at >= today,
            TransferRecord.status.in_(["PROCESSING", "SETTLED"]),
        )
    )
    return Decimal(str(result.scalar_one()))


async def _repeated_amount_count(
    session: AsyncSession, document: str, amount: Decimal, window_minutes: int
) -> int:
    since = datetime.utcnow() - timedelta(minutes=window_minutes)
    result = await session.execute(
        select(func.count()).select_from(TransferRecord).where(
            TransferRecord.sender_document == document,
            TransferRecord.amount == amount,
            TransferRecord.created_at >= since,
        )
    )
    return result.scalar_one()


async def run_aml(
    session: AsyncSession,
    req: TransferRequest,
    daily_limit: Decimal = DAILY_LIMIT_DEFAULT,
) -> AMLResult:
    triggered: list[str] = []
    amount = req.amount
    doc = req.sender.document.replace(".", "").replace("-", "").replace("/", "")

    # R01 — COAF threshold
    if amount >= COAF_THRESHOLD:
        triggered.append("R01:COAF_THRESHOLD")
        logger.warning("AML R01: valor %.2f >= threshold %.2f | doc=%s", amount, COAF_THRESHOLD, doc)

    # R05 — structuring (valor logo abaixo do threshold)
    if COAF_THRESHOLD - STRUCTURING_MARGIN <= amount < COAF_THRESHOLD:
        triggered.append("R05:STRUCTURING")
        logger.warning("AML R05: possível fracionamento | valor=%.2f | doc=%s", amount, doc)

    # R02 — velocity de transações
    recent_count = await _count_recent_tx(session, doc, VELOCITY_WINDOW_MIN)
    if recent_count >= VELOCITY_MAX_TX:
        triggered.append(f"R02:VELOCITY_TX:{recent_count}tx/{VELOCITY_WINDOW_MIN}min")
        logger.warning("AML R02: %d transações em %dmin | doc=%s", recent_count, VELOCITY_WINDOW_MIN, doc)

    # R03 — volume diário
    vol = await _daily_volume(session, doc)
    if vol + amount > daily_limit:
        triggered.append(f"R03:DAILY_LIMIT:{float(vol + amount):.2f}")
        logger.warning("AML R03: volume diário %.2f excede limite %.2f | doc=%s", vol + amount, daily_limit, doc)

    # R04 — smurfing (mesmos valores repetidos)
    rep = await _repeated_amount_count(session, doc, amount, VELOCITY_WINDOW_MIN)
    if rep >= SMURFING_REPEAT:
        triggered.append(f"R04:SMURFING:{rep}x_same_amount")
        logger.warning("AML R04: valor %.2f repetido %dx | doc=%s", amount, rep, doc)

    if not triggered:
        return AMLResult(passed=True)

    # Calcula nível de risco
    critical_rules = {"R02", "R03", "R04"}
    high_rules     = {"R05"}

    codes = {r.split(":")[0] for r in triggered}
    if codes & critical_rules:
        risk = "CRITICAL"
    elif codes & high_rules:
        risk = "HIGH"
    elif "R01" in codes:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    # CRITICAL e HIGH bloqueiam; MEDIUM e LOW passam mas registram alerta
    passed = risk in ("LOW", "MEDIUM")

    return AMLResult(
        passed=passed,
        risk_level=risk,
        rules_triggered=triggered,
        reason="; ".join(triggered) if not passed else "",
    )
