from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class TransferStatus(str, Enum):
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    SETTLED    = "SETTLED"
    FAILED     = "FAILED"
    REVERSED   = "REVERSED"


class TransferType(str, Enum):
    CREDIT = "CREDIT"
    DEBIT  = "DEBIT"


class ParticipantInfo(BaseModel):
    ispb:         str = Field(..., description="ISPB da instituição (8 dígitos)")
    name:         str
    document:     str = Field(..., description="CPF (11 dígitos) ou CNPJ (14 dígitos)")
    account:      str
    branch:       Optional[str] = None
    account_type: str = Field(default="CONTA_CORRENTE")


class TransferRequest(BaseModel):
    idempotency_key: UUID        = Field(default_factory=uuid4)
    amount:          Decimal     = Field(..., gt=0, decimal_places=2)
    currency:        str         = Field(default="BRL")
    sender:          ParticipantInfo
    receiver:        ParticipantInfo
    description:     Optional[str] = Field(None, max_length=140)
    scheduled_at:    Optional[datetime] = None

    @field_validator("amount")
    @classmethod
    def amount_max(cls, v: Decimal) -> Decimal:
        if v > Decimal("10000000.00"):
            raise ValueError("Valor excede limite de R$ 10.000.000,00 por transferência")
        return v


class TransferResponse(BaseModel):
    transfer_id:     UUID
    idempotency_key: UUID
    status:          TransferStatus
    amount:          Decimal
    currency:        str
    sender:          ParticipantInfo
    receiver:        ParticipantInfo
    description:     Optional[str]
    created_at:      datetime
    updated_at:      datetime
    settled_at:      Optional[datetime] = None
    failure_reason:  Optional[str]      = None


class WebhookEvent(str, Enum):
    TRANSFER_RECEIVED  = "TRANSFER_RECEIVED"
    TRANSFER_SETTLED   = "TRANSFER_SETTLED"
    TRANSFER_FAILED    = "TRANSFER_FAILED"
    TRANSFER_REVERSED  = "TRANSFER_REVERSED"


class WebhookPayload(BaseModel):
    event_id:    UUID = Field(default_factory=uuid4)
    event:       WebhookEvent
    transfer_id: UUID
    timestamp:   datetime = Field(default_factory=datetime.utcnow)
    data:        TransferResponse


class ReconciliationEntry(BaseModel):
    transfer_id: UUID
    amount:      Decimal
    status:      TransferStatus
    created_at:  datetime
    settled_at:  Optional[datetime]


class ReconciliationRequest(BaseModel):
    date_from: datetime
    date_to:   datetime
    page:      int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=1000)


class ReconciliationResponse(BaseModel):
    total:       int
    page:        int
    page_size:   int
    entries:     list[ReconciliationEntry]
    total_debit: Decimal
    total_credit: Decimal
