"""
Cliente HTTP para chamar a outra IP.
Assina automaticamente todas as requisições.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from uuid import UUID, uuid4

import httpx

from app.config import settings
from app.models import (
    ParticipantInfo,
    ReconciliationRequest,
    ReconciliationResponse,
    TransferRequest,
    TransferResponse,
    WebhookPayload,
)
from app.security import sign_request

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _get_client(peer_base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=peer_base_url, timeout=TIMEOUT)


def _auth_headers(body: bytes) -> dict[str, str]:
    return sign_request(settings.api_secret, settings.api_key_id, body)


async def send_transfer(
    peer_base_url: str,
    amount: Decimal,
    sender: ParticipantInfo,
    receiver: ParticipantInfo,
    description: str | None = None,
    idempotency_key: UUID | None = None,
) -> TransferResponse:
    """Envia uma transferência para a outra IP."""
    req = TransferRequest(
        idempotency_key=idempotency_key or uuid4(),
        amount=amount,
        sender=sender,
        receiver=receiver,
        description=description,
    )
    body = req.model_dump_json().encode()
    headers = {**_auth_headers(body), "Content-Type": "application/json"}

    async with _get_client(peer_base_url) as c:
        resp = await c.post("/transfers", content=body, headers=headers)
        resp.raise_for_status()
        return TransferResponse.model_validate(resp.json())


async def get_transfer_status(peer_base_url: str, transfer_id: str | UUID) -> TransferResponse:
    """Consulta o status de uma transferência na outra IP."""
    body = b""
    headers = _auth_headers(body)

    async with _get_client(peer_base_url) as c:
        resp = await c.get(f"/transfers/{transfer_id}", headers=headers)
        resp.raise_for_status()
        return TransferResponse.model_validate(resp.json())


async def send_webhook(webhook_url: str, payload: WebhookPayload) -> bool:
    """Notifica a outra IP de um evento."""
    body = payload.model_dump_json().encode()
    headers = {**_auth_headers(body), "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(webhook_url, content=body, headers=headers)
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Falha ao enviar webhook para %s: %s", webhook_url, exc)
        return False


async def request_reconciliation(
    peer_base_url: str,
    recon_request: ReconciliationRequest,
) -> ReconciliationResponse:
    """Solicita conciliação do período à outra IP."""
    body = recon_request.model_dump_json().encode()
    headers = {**_auth_headers(body), "Content-Type": "application/json"}

    async with _get_client(peer_base_url) as c:
        resp = await c.post("/reconciliation", content=body, headers=headers)
        resp.raise_for_status()
        return ReconciliationResponse.model_validate(resp.json())
