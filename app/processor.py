"""
Processa a liquidação de uma transferência recebida.

Pipeline:
  1. KYC  — valida documentos e ISPB
  2. Blocklist — verifica sancionados
  3. Limites — checa limites operacionais
  4. AML  — detecta padrões suspeitos
  5. Liquidação — crédito na conta do beneficiário (stub)
  6. Webhook — notifica IP remetente do resultado

Em produção: substituir asyncio.create_task por Celery/RQ worker dedicado.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app.audit import AuditEvent, log_event
from app.client import send_webhook
from app.compliance.aml import run_aml
from app.compliance.blocklist import check_all
from app.compliance.kyc import validate_participant
from app.compliance.limits import check_limits
from app.config import settings
from app.database import AsyncSessionLocal, get_transfer, update_transfer_status
from app.models import (
    ParticipantInfo,
    TransferRequest,
    TransferResponse,
    TransferStatus,
    WebhookEvent,
    WebhookPayload,
)

logger = logging.getLogger(__name__)


def _build_webhook_payload(tr, event: WebhookEvent) -> WebhookPayload:
    return WebhookPayload(
        event=event,
        transfer_id=tr.transfer_id,
        data=TransferResponse(
            transfer_id=tr.transfer_id,
            idempotency_key=tr.idempotency_key,
            status=TransferStatus(tr.status),
            amount=tr.amount,
            currency=tr.currency,
            sender=json.loads(tr.sender_json),
            receiver=json.loads(tr.receiver_json),
            description=tr.description,
            created_at=tr.created_at,
            updated_at=tr.updated_at,
            settled_at=tr.settled_at,
            failure_reason=tr.failure_reason,
        ),
    )


async def settle_transfer(transfer_id: str):
    """Pipeline completo de compliance + liquidação."""
    async with AsyncSessionLocal() as session:
        tr = await get_transfer(session, transfer_id)
        if not tr:
            logger.error("Transfer não encontrado: %s", transfer_id)
            return

        sender   = ParticipantInfo(**json.loads(tr.sender_json))
        receiver = ParticipantInfo(**json.loads(tr.receiver_json))

        # ── 1. KYC ─────────────────────────────────────────────────────────
        for label, participant in [("sender", sender), ("receiver", receiver)]:
            kyc = validate_participant(participant)
            if not kyc.passed:
                logger.warning("KYC falhou [%s]: %s", label, kyc.reason)
                await update_transfer_status(session, transfer_id, "FAILED", failure_reason=f"KYC:{kyc.reason}")
                await log_event(session, AuditEvent.COMPLIANCE_BLOCKED,
                    {"stage": "KYC", "participant": label, "reason": kyc.reason},
                    transfer_id=transfer_id)
                tr = await get_transfer(session, transfer_id)
                await send_webhook(settings.peer_webhook_url, _build_webhook_payload(tr, WebhookEvent.TRANSFER_FAILED))
                return

        # ── 2. Blocklist ────────────────────────────────────────────────────
        block = await check_all(
            session,
            sender.document, sender.ispb, sender.account,
            receiver.document, receiver.ispb, receiver.account,
        )
        if block.blocked:
            logger.warning("Blocklist hit: %s [%s]", block.reason, block.list_source)
            await update_transfer_status(session, transfer_id, "FAILED", failure_reason=f"BLOCKLIST:{block.reason}")
            await log_event(session, AuditEvent.COMPLIANCE_BLOCKED,
                {"stage": "BLOCKLIST", "reason": block.reason, "source": block.list_source},
                transfer_id=transfer_id)
            tr = await get_transfer(session, transfer_id)
            await send_webhook(settings.peer_webhook_url, _build_webhook_payload(tr, WebhookEvent.TRANSFER_FAILED))
            return

        # ── 3. Limites ──────────────────────────────────────────────────────
        lim = await check_limits(session, tr.amount, sender.ispb, sender.account)
        if not lim.passed:
            logger.warning("Limite excedido: %s", lim.reason)
            await update_transfer_status(session, transfer_id, "FAILED", failure_reason=f"LIMIT:{lim.reason}")
            await log_event(session, AuditEvent.COMPLIANCE_BLOCKED,
                {"stage": "LIMITS", "reason": lim.reason, "limit_type": lim.limit_type},
                transfer_id=transfer_id)
            tr = await get_transfer(session, transfer_id)
            await send_webhook(settings.peer_webhook_url, _build_webhook_payload(tr, WebhookEvent.TRANSFER_FAILED))
            return

        # ── 4. AML ──────────────────────────────────────────────────────────
        fake_req = TransferRequest(
            amount=tr.amount, sender=sender, receiver=receiver,
            description=tr.description,
        )
        aml = await run_aml(session, fake_req)

        if aml.rules_triggered:
            await log_event(session, AuditEvent.COMPLIANCE_AML_ALERT,
                {"risk": aml.risk_level, "rules": aml.rules_triggered},
                transfer_id=transfer_id)

        if not aml.passed:
            logger.warning("AML bloqueou: %s | risco=%s", aml.reason, aml.risk_level)
            await update_transfer_status(session, transfer_id, "FAILED", failure_reason=f"AML:{aml.reason}")
            await log_event(session, AuditEvent.COMPLIANCE_BLOCKED,
                {"stage": "AML", "risk": aml.risk_level, "rules": aml.rules_triggered},
                transfer_id=transfer_id)
            tr = await get_transfer(session, transfer_id)
            await send_webhook(settings.peer_webhook_url, _build_webhook_payload(tr, WebhookEvent.TRANSFER_FAILED))
            return

        # ── 5. Liquidação ───────────────────────────────────────────────────
        try:
            # ► Aqui entra a lógica real de crédito (core bancário, saldo, etc.)
            settled_at = datetime.utcnow()
            await update_transfer_status(session, transfer_id, "SETTLED", settled_at=settled_at)
            logger.info("Transfer %s SETTLED | valor=%.2f | risco=%s", transfer_id, tr.amount, aml.risk_level)
            await log_event(session, AuditEvent.TRANSFER_SETTLED,
                {"amount": str(tr.amount), "risk_level": aml.risk_level},
                transfer_id=transfer_id)
            event = WebhookEvent.TRANSFER_SETTLED

        except Exception as exc:
            logger.exception("Erro ao liquidar %s: %s", transfer_id, exc)
            await update_transfer_status(session, transfer_id, "FAILED", failure_reason=str(exc))
            await log_event(session, AuditEvent.TRANSFER_FAILED,
                {"error": str(exc)}, transfer_id=transfer_id)
            event = WebhookEvent.TRANSFER_FAILED

        # ── 6. Webhook de retorno ────────────────────────────────────────────
        tr = await get_transfer(session, transfer_id)
        payload = _build_webhook_payload(tr, event)
        sent    = await send_webhook(settings.peer_webhook_url, payload)
        await log_event(session, AuditEvent.WEBHOOK_SENT,
            {"event": event, "url": settings.peer_webhook_url, "sent": sent},
            transfer_id=transfer_id)
