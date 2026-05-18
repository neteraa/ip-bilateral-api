"""
Camada de dados — SQLite async (dev/homologação).
Em produção: trocar DATABASE_URL para PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db_models import Base, TransferRecord, PeerIP, TransferLimitConfig


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


# ── Transfer helpers ────────────────────────────────────────────────────────

async def create_transfer(session: AsyncSession, record: dict) -> TransferRecord:
    tr = TransferRecord(**record)
    session.add(tr)
    await session.commit()
    await session.refresh(tr)
    return tr


async def get_transfer(session: AsyncSession, transfer_id: str) -> Optional[TransferRecord]:
    return await session.get(TransferRecord, transfer_id)


async def get_transfer_by_idempotency(session: AsyncSession, key: str) -> Optional[TransferRecord]:
    result = await session.execute(
        select(TransferRecord).where(TransferRecord.idempotency_key == key)
    )
    return result.scalar_one_or_none()


async def update_transfer_status(
    session: AsyncSession,
    transfer_id: str,
    status: str,
    failure_reason: Optional[str] = None,
    settled_at: Optional[datetime] = None,
) -> Optional[TransferRecord]:
    tr = await get_transfer(session, transfer_id)
    if not tr:
        return None
    tr.status = status
    tr.updated_at = datetime.utcnow()
    if failure_reason:
        tr.failure_reason = failure_reason
    if settled_at:
        tr.settled_at = settled_at
    await session.commit()
    await session.refresh(tr)
    return tr


async def list_transfers(
    session: AsyncSession,
    date_from: datetime,
    date_to: datetime,
    offset: int = 0,
    limit: int = 100,
) -> tuple[int, list[TransferRecord]]:
    q = (
        select(TransferRecord)
        .where(TransferRecord.created_at >= date_from)
        .where(TransferRecord.created_at <= date_to)
    )
    count_result = await session.execute(
        select(sa_func.count()).select_from(q.subquery())
    )
    total = count_result.scalar_one()
    result = await session.execute(q.offset(offset).limit(limit))
    return total, list(result.scalars().all())


# ── Peer IP helpers ─────────────────────────────────────────────────────────

async def get_all_peers(session: AsyncSession) -> list[PeerIP]:
    result = await session.execute(select(PeerIP).where(PeerIP.status == "ACTIVE"))
    return list(result.scalars().all())


async def get_peer_by_ispb(session: AsyncSession, ispb: str) -> Optional[PeerIP]:
    result = await session.execute(select(PeerIP).where(PeerIP.ispb == ispb))
    return result.scalar_one_or_none()


async def get_peer_by_key_id(session: AsyncSession, key_id: str) -> Optional[PeerIP]:
    result = await session.execute(select(PeerIP).where(PeerIP.api_key_id == key_id))
    return result.scalar_one_or_none()


async def get_known_peers_map(session: AsyncSession) -> dict[str, str]:
    """Retorna {api_key_id: api_secret} de todos os peers ativos — usado pelo security.py."""
    peers = await get_all_peers(session)
    return {p.api_key_id: p.api_secret for p in peers}


# ── Limit config helpers ─────────────────────────────────────────────────────

async def list_limits(session: AsyncSession) -> list[TransferLimitConfig]:
    result = await session.execute(
        select(TransferLimitConfig).where(TransferLimitConfig.active == True)
    )
    return list(result.scalars().all())
