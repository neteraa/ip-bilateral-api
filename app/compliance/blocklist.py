"""
Blocklist — documentos/contas/ISPBs sancionados ou bloqueados manualmente.

Fontes suportadas:
  - Banco de dados local (tabela `blocklist`).
  - Arquivo CSV externo (ex: lista OFAC, lista COAF).
  - API futura (stub pronto para integrar).
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_models import BlocklistEntry

logger = logging.getLogger(__name__)

LISTS_DIR = Path(__file__).parent.parent.parent / "lists"


@dataclass
class BlocklistResult:
    blocked: bool
    reason: str = ""
    list_source: str = ""


def _digits_only(v: str) -> str:
    return re.sub(r"\D", "", v)


# ── banco de dados ──────────────────────────────────────────────────────────

async def is_blocked_db(session: AsyncSession, value: str) -> BlocklistResult:
    normalized = _digits_only(value) or value.strip().lower()
    result = await session.execute(
        select(BlocklistEntry).where(
            BlocklistEntry.value == normalized,
            BlocklistEntry.active == True,
        )
    )
    entry = result.scalar_one_or_none()
    if entry:
        return BlocklistResult(
            blocked=True,
            reason=entry.reason or "Entidade bloqueada",
            list_source=entry.list_source or "db",
        )
    return BlocklistResult(blocked=False)


async def add_to_blocklist(
    session: AsyncSession,
    value: str,
    entry_type: str,
    reason: str,
    added_by: str,
    list_source: str = "manual",
) -> BlocklistEntry:
    normalized = _digits_only(value) or value.strip().lower()
    entry = BlocklistEntry(
        value=normalized,
        entry_type=entry_type,
        reason=reason,
        added_by=added_by,
        list_source=list_source,
        active=True,
        created_at=datetime.utcnow(),
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    logger.warning("BLOCKLIST ADD: type=%s value=%s reason=%s by=%s", entry_type, normalized, reason, added_by)
    return entry


async def remove_from_blocklist(session: AsyncSession, entry_id: int, removed_by: str) -> bool:
    entry = await session.get(BlocklistEntry, entry_id)
    if not entry:
        return False
    entry.active = False
    entry.updated_at = datetime.utcnow()
    await session.commit()
    logger.warning("BLOCKLIST REMOVE: id=%s by=%s", entry_id, removed_by)
    return True


# ── CSV externo ─────────────────────────────────────────────────────────────

def _load_csv_set(filename: str) -> set[str]:
    path = LISTS_DIR / filename
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {_digits_only(row.get("document", "")) or row.get("document", "").strip().lower()
                for row in reader}


_OFAC_SET: set[str] = set()
_COAF_SET: set[str] = set()


def reload_external_lists():
    global _OFAC_SET, _COAF_SET
    _OFAC_SET = _load_csv_set("ofac.csv")
    _COAF_SET = _load_csv_set("coaf.csv")
    logger.info("Listas externas recarregadas: OFAC=%d COAF=%d", len(_OFAC_SET), len(_COAF_SET))


reload_external_lists()


def is_blocked_external(value: str) -> BlocklistResult:
    normalized = _digits_only(value) or value.strip().lower()
    if normalized in _OFAC_SET:
        return BlocklistResult(blocked=True, reason="Entidade na lista OFAC", list_source="ofac")
    if normalized in _COAF_SET:
        return BlocklistResult(blocked=True, reason="Entidade na lista COAF", list_source="coaf")
    return BlocklistResult(blocked=False)


# ── verificação combinada ────────────────────────────────────────────────────

async def check_all(session: AsyncSession, *values: str) -> BlocklistResult:
    """Verifica múltiplos valores (documento, ISPB, conta) contra todas as fontes."""
    for v in values:
        # externo primeiro (mais rápido, sem I/O)
        ext = is_blocked_external(v)
        if ext.blocked:
            return ext
        # banco de dados
        db = await is_blocked_db(session, v)
        if db.blocked:
            return db
    return BlocklistResult(blocked=False)
