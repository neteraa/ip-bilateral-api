import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import (
    create_transfer,
    get_session,
    get_transfer,
    get_transfer_by_idempotency,
)
from app.models import (
    TransferRequest,
    TransferResponse,
    TransferStatus,
)
from app.security import verify_request

router = APIRouter(prefix="/transfers", tags=["Transfers"])


def _record_to_response(tr) -> TransferResponse:
    return TransferResponse(
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
    )


@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Recebe uma transferência de outra IP",
)
async def receive_transfer(
    body:       TransferRequest,
    session:    AsyncSession = Depends(get_session),
    peer_key:   str          = Depends(
        lambda req: verify_request(req, settings.known_peers)
    ),
):
    # Idempotência: mesma key? Retorna o existente
    existing = await get_transfer_by_idempotency(session, str(body.idempotency_key))
    if existing:
        return _record_to_response(existing)

    # Valida que o destino é esta IP
    if body.receiver.ispb != settings.this_ispb:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ISPB destino {body.receiver.ispb} não é este servidor ({settings.this_ispb})",
        )

    now = datetime.utcnow()
    transfer_id = str(uuid4())

    record = {
        "transfer_id":     transfer_id,
        "idempotency_key": str(body.idempotency_key),
        "status":          TransferStatus.PROCESSING.value,
        "amount":          body.amount,
        "currency":        body.currency,
        "sender_json":     body.sender.model_dump_json(),
        "receiver_json":   body.receiver.model_dump_json(),
        "description":     body.description,
        "created_at":      now,
        "updated_at":      now,
        "direction":       "RECEIVED",
    }

    tr = await create_transfer(session, record)

    # Enfileira processamento assíncrono (liquidação) em background
    from fastapi import BackgroundTasks
    from app.processor import settle_transfer
    import asyncio
    asyncio.create_task(settle_transfer(transfer_id))

    return _record_to_response(tr)


@router.get(
    "/{transfer_id}",
    response_model=TransferResponse,
    summary="Consulta status de uma transferência",
)
async def get_transfer_status(
    transfer_id: str,
    session:     AsyncSession = Depends(get_session),
    peer_key:    str          = Depends(
        lambda req: verify_request(req, settings.known_peers)
    ),
):
    tr = await get_transfer(session, transfer_id)
    if not tr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transferência não encontrada")
    return _record_to_response(tr)
