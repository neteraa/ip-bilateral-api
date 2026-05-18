"""
Modelos SQLAlchemy — todas as tabelas do sistema.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TransferRecord(Base):
    __tablename__ = "transfers"

    transfer_id      = Column(String(36), primary_key=True)
    idempotency_key  = Column(String(36), unique=True, nullable=False, index=True)
    status           = Column(String(20), nullable=False, default="PENDING")
    amount           = Column(Numeric(18, 2), nullable=False)
    currency         = Column(String(3), nullable=False, default="BRL")
    sender_json      = Column(Text, nullable=False)
    receiver_json    = Column(Text, nullable=False)
    sender_document  = Column(String(14), nullable=True, index=True)
    sender_ispb      = Column(String(8),  nullable=True, index=True)
    sender_account   = Column(String(50), nullable=True, index=True)
    description      = Column(String(140))
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    settled_at       = Column(DateTime, nullable=True)
    failure_reason   = Column(Text, nullable=True)
    direction        = Column(String(10), nullable=False)
    risk_level       = Column(String(10), nullable=True)
    aml_rules        = Column(Text, nullable=True)


class BlocklistEntry(Base):
    __tablename__ = "blocklist"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    value       = Column(String(100), nullable=False, index=True)
    entry_type  = Column(String(20), nullable=False)   # CPF | CNPJ | ISPB | ACCOUNT
    reason      = Column(String(255))
    added_by    = Column(String(100))
    list_source = Column(String(50), default="manual")
    active      = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_type  = Column(String(60), nullable=False, index=True)
    transfer_id = Column(String(36), nullable=True,  index=True)
    actor       = Column(String(100), default="system")
    payload     = Column(Text, nullable=False)
    prev_hash   = Column(String(64), nullable=False)
    this_hash   = Column(String(64), nullable=False, unique=True)
    created_at  = Column(DateTime, nullable=False)


class PeerIP(Base):
    __tablename__ = "peer_ips"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    ispb           = Column(String(8),   unique=True, nullable=False)
    name           = Column(String(200), nullable=False)
    api_key_id     = Column(String(100), unique=True, nullable=False)
    api_secret     = Column(String(255), nullable=False)
    webhook_url    = Column(String(500), nullable=False)
    base_url       = Column(String(500), nullable=False)
    status         = Column(String(20),  default="ACTIVE")   # ACTIVE | SUSPENDED | PENDING
    contact_email  = Column(String(200), nullable=True)
    notes          = Column(Text, nullable=True)
    onboarded_at   = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, nullable=True)


class PartnerSubmission(Base):
    __tablename__ = "partner_submissions"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    reference_code   = Column(String(20), unique=True, nullable=False, index=True)
    status           = Column(String(20), default="PENDING")   # PENDING | UNDER_REVIEW | APPROVED | REJECTED
    institution_name = Column(String(200), nullable=False)
    country          = Column(String(100), nullable=False)
    regulator        = Column(String(100), nullable=False)
    license_number   = Column(String(100), nullable=False)
    lei_code         = Column(String(20),  nullable=True)
    swift_bic        = Column(String(20),  nullable=True)
    contact_name     = Column(String(200), nullable=False)
    contact_email    = Column(String(200), nullable=False)
    contact_phone    = Column(String(50),  nullable=True)
    website          = Column(String(200), nullable=True)
    api_endpoint     = Column(String(500), nullable=True)
    notes            = Column(Text, nullable=True)
    reviewer_comment = Column(Text, nullable=True)
    reviewed_by      = Column(String(100), nullable=True)
    reviewed_at      = Column(DateTime, nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=True)


class PartnerDocument(Base):
    __tablename__ = "partner_documents"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    submission_id  = Column(Integer, nullable=False, index=True)
    doc_type       = Column(String(50),  nullable=False)   # LICENSE | LEI | INCORPORATION | ID_LEGAL_REP | BANK_PROOF | NDA | OTHER
    filename       = Column(String(255), nullable=False)
    stored_path    = Column(String(500), nullable=False)
    sha256         = Column(String(64),  nullable=False)
    size_bytes     = Column(Integer,     nullable=False)
    verified       = Column(Boolean, default=False)
    uploaded_at    = Column(DateTime, default=datetime.utcnow)


class PrefundedAccount(Base):
    __tablename__ = "prefunded_accounts"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    ispb            = Column(String(8),   unique=True, nullable=False, index=True)
    currency        = Column(String(3),   nullable=False, default="USD")
    balance         = Column(Numeric(18, 2), nullable=False, default=0)
    min_balance     = Column(Numeric(18, 2), nullable=False, default=1000)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, nullable=True)


class PrefundedTransaction(Base):
    __tablename__ = "prefunded_transactions"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    ispb         = Column(String(8),   nullable=False, index=True)
    type         = Column(String(20),  nullable=False)   # TOPUP | DEBIT | CREDIT | REVERSAL
    amount       = Column(Numeric(18, 2), nullable=False)
    balance_after= Column(Numeric(18, 2), nullable=False)
    currency     = Column(String(3),   nullable=False, default="USD")
    transfer_id  = Column(String(36),  nullable=True)
    description  = Column(String(255), nullable=True)
    created_by   = Column(String(100), default="system")
    created_at   = Column(DateTime, default=datetime.utcnow)


class TransferLimitConfig(Base):
    __tablename__ = "transfer_limits"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    scope            = Column(String(20), nullable=False)    # global | peer_ispb | account
    scope_id         = Column(String(100), nullable=False)
    max_single_tx    = Column(Numeric(18, 2), nullable=False)
    max_daily_volume = Column(Numeric(18, 2), nullable=False)
    max_daily_count  = Column(Integer, nullable=False)
    active           = Column(Boolean, default=True)
    created_by       = Column(String(100))
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=True)
