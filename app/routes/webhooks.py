"""
Recebe notificações de eventos da outra IP.
A outra IP chama POST /webhooks quando uma transferência muda de status.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session, update_transfer_status
from app.models import WebhookEvent, WebhookPayload
from app.security import verify_request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    summary="Recebe notificação de evento de outra IP",
)
async def receive_webhook(
    payload:  WebhookPayload,
    session:  AsyncSession = Depends(get_session),
    peer_key: str          = Depends(
        lambda req: verify_request(req, settings.known_peers)
    ),
):
    logger.info("Webhook recebido: event=%s transfer=%s", payload.event, payload.transfer_id)

    transfer_id = str(payload.transfer_id)

    if payload.event == WebhookEvent.TRANSFER_SETTLED:
        await update_transfer_status(
            session,
            transfer_id,
            status="SETTLED",
            settled_at=payload.data.settled_at or datetime.utcnow(),
        )

    elif payload.event == WebhookEvent.TRANSFER_FAILED:
        await update_transfer_status(
            session,
            transfer_id,
            status="FAILED",
            failure_reason=payload.data.failure_reason,
        )

    elif payload.event == WebhookEvent.TRANSFER_REVERSED:
        await update_transfer_status(
            session,
            transfer_id,
            status="REVERSED",
        )

    elif payload.event == WebhookEvent.TRANSFER_RECEIVED:
        # Outra IP confirmou que recebeu — atualiza para PROCESSING se ainda PENDING
        await update_transfer_status(session, transfer_id, status="PROCESSING")

    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Evento desconhecido: {payload.event}",
        )

    return {"acknowledged": True, "event_id": str(payload.event_id)}
