"""
Portal de onboarding de parceiros IP.

Rotas públicas (parceiro):
  POST /partner/submit          — envia dados da instituição
  POST /partner/{ref}/documents — faz upload de documentos

Rotas admin (requer X-Admin-Key):
  GET  /partner/submissions          — lista todas as submissões
  GET  /partner/submissions/{ref}    — detalhe com documentos
  PATCH /partner/submissions/{ref}/review — aprova ou rejeita
  GET  /partner/documents/{doc_id}/download — baixa arquivo
"""
from __future__ import annotations

import hashlib
import random
import string
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import AuditEvent, log_event
from app.database import get_session
from app.db_models import PartnerDocument, PartnerSubmission
from app.routes.admin import require_admin_key

router = APIRouter(prefix="/partner", tags=["Partner Onboarding"])

UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

DOC_TYPES = {"LICENSE", "LEI", "INCORPORATION", "ID_LEGAL_REP", "BANK_PROOF", "NDA", "OTHER"}
MAX_FILE_MB = 20


def _gen_ref() -> str:
    chars = string.ascii_uppercase + string.digits
    return "IP-" + "".join(random.choices(chars, k=8))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Rotas públicas ────────────────────────────────────────────────────────────

@router.post(
    "/submit",
    status_code=status.HTTP_201_CREATED,
    summary="Submete dados da instituição parceira",
)
async def submit_partner(
    institution_name: str = Form(...),
    country:          str = Form(...),
    regulator:        str = Form(...),
    license_number:   str = Form(...),
    contact_name:     str = Form(...),
    contact_email:    str = Form(...),
    lei_code:         Optional[str] = Form(None),
    swift_bic:        Optional[str] = Form(None),
    contact_phone:    Optional[str] = Form(None),
    website:          Optional[str] = Form(None),
    api_endpoint:     Optional[str] = Form(None),
    notes:            Optional[str] = Form(None),
    session:          AsyncSession = Depends(get_session),
):
    ref = _gen_ref()
    sub = PartnerSubmission(
        reference_code=ref,
        status="PENDING",
        institution_name=institution_name,
        country=country,
        regulator=regulator,
        license_number=license_number,
        lei_code=lei_code,
        swift_bic=swift_bic,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        website=website,
        api_endpoint=api_endpoint,
        notes=notes,
        created_at=datetime.utcnow(),
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)

    await log_event(session, AuditEvent.PEER_ONBOARDED,
        {"ref": ref, "institution": institution_name, "country": country},
        actor="partner_portal")

    return {
        "reference_code": ref,
        "status": "PENDING",
        "message": "Submissão recebida. Guarde o código de referência para acompanhamento.",
    }


@router.post(
    "/{reference_code}/documents",
    status_code=status.HTTP_201_CREATED,
    summary="Upload de documento (um por request)",
)
async def upload_document(
    reference_code: str,
    doc_type:       str          = Form(..., description="LICENSE | LEI | INCORPORATION | ID_LEGAL_REP | BANK_PROOF | NDA | OTHER"),
    file:           UploadFile   = File(...),
    session:        AsyncSession = Depends(get_session),
):
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, f"doc_type inválido. Use: {', '.join(DOC_TYPES)}")

    result = await session.execute(
        select(PartnerSubmission).where(PartnerSubmission.reference_code == reference_code)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Código de referência não encontrado")
    if sub.status == "APPROVED":
        raise HTTPException(409, "Submissão já aprovada — não é possível adicionar documentos")

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        raise HTTPException(413, f"Arquivo excede {MAX_FILE_MB}MB")

    sha = _sha256(content)
    safe_name = f"{reference_code}_{doc_type}_{sha[:8]}_{file.filename}"
    dest = UPLOADS_DIR / safe_name
    dest.write_bytes(content)

    doc = PartnerDocument(
        submission_id=sub.id,
        doc_type=doc_type,
        filename=file.filename,
        stored_path=str(dest),
        sha256=sha,
        size_bytes=len(content),
        verified=False,
        uploaded_at=datetime.utcnow(),
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    return {
        "doc_id": doc.id,
        "doc_type": doc_type,
        "filename": file.filename,
        "sha256": sha,
        "size_kb": round(len(content) / 1024, 1),
    }


# ── Rotas admin ───────────────────────────────────────────────────────────────

@router.get(
    "/submissions",
    summary="[ADMIN] Lista submissões de parceiros",
)
async def list_submissions(
    status_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    q = select(PartnerSubmission).order_by(PartnerSubmission.created_at.desc())
    if status_filter:
        q = q.where(PartnerSubmission.status == status_filter.upper())
    result = await session.execute(q)
    subs = result.scalars().all()
    return [
        {
            "id": s.id,
            "reference_code": s.reference_code,
            "institution_name": s.institution_name,
            "country": s.country,
            "regulator": s.regulator,
            "contact_email": s.contact_email,
            "status": s.status,
            "created_at": s.created_at,
        }
        for s in subs
    ]


@router.get(
    "/submissions/{reference_code}",
    summary="[ADMIN] Detalhe da submissão com documentos",
)
async def get_submission(
    reference_code: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_admin_key),
):
    result = await session.execute(
        select(PartnerSubmission).where(PartnerSubmission.reference_code == reference_code)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Submissão não encontrada")

    docs_result = await session.execute(
        select(PartnerDocument).where(PartnerDocument.submission_id == sub.id)
    )
    docs = docs_result.scalars().all()

    return {
        "reference_code": sub.reference_code,
        "status": sub.status,
        "institution_name": sub.institution_name,
        "country": sub.country,
        "regulator": sub.regulator,
        "license_number": sub.license_number,
        "lei_code": sub.lei_code,
        "swift_bic": sub.swift_bic,
        "contact_name": sub.contact_name,
        "contact_email": sub.contact_email,
        "contact_phone": sub.contact_phone,
        "website": sub.website,
        "api_endpoint": sub.api_endpoint,
        "notes": sub.notes,
        "reviewer_comment": sub.reviewer_comment,
        "reviewed_by": sub.reviewed_by,
        "reviewed_at": sub.reviewed_at,
        "created_at": sub.created_at,
        "documents": [
            {
                "doc_id": d.id,
                "doc_type": d.doc_type,
                "filename": d.filename,
                "sha256": d.sha256,
                "size_kb": round(d.size_bytes / 1024, 1),
                "verified": d.verified,
                "uploaded_at": d.uploaded_at,
                "download_url": f"/partner/documents/{d.id}/download",
            }
            for d in docs
        ],
    }


@router.patch(
    "/submissions/{reference_code}/review",
    summary="[ADMIN] Aprova ou rejeita submissão",
)
async def review_submission(
    reference_code: str,
    decision:       str = Form(..., description="APPROVED | REJECTED | UNDER_REVIEW"),
    comment:        Optional[str] = Form(None),
    session:        AsyncSession = Depends(get_session),
    admin:          str = Depends(require_admin_key),
):
    valid = {"APPROVED", "REJECTED", "UNDER_REVIEW"}
    if decision not in valid:
        raise HTTPException(422, f"decision deve ser: {', '.join(valid)}")

    result = await session.execute(
        select(PartnerSubmission).where(PartnerSubmission.reference_code == reference_code)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Submissão não encontrada")

    sub.status           = decision
    sub.reviewer_comment = comment
    sub.reviewed_by      = "admin"
    sub.reviewed_at      = datetime.utcnow()
    sub.updated_at       = datetime.utcnow()
    await session.commit()

    await log_event(session, AuditEvent.PEER_ONBOARDED,
        {"ref": reference_code, "decision": decision, "comment": comment},
        actor="admin")

    return {"reference_code": reference_code, "status": decision, "comment": comment}


@router.patch(
    "/documents/{doc_id}/verify",
    summary="[ADMIN] Marca documento como verificado",
)
async def verify_document(
    doc_id:  int,
    session: AsyncSession = Depends(get_session),
    _:       str = Depends(require_admin_key),
):
    doc = await session.get(PartnerDocument, doc_id)
    if not doc:
        raise HTTPException(404, "Documento não encontrado")
    doc.verified = True
    await session.commit()
    return {"doc_id": doc_id, "verified": True, "sha256": doc.sha256}


@router.get(
    "/documents/{doc_id}/download",
    summary="[ADMIN] Download do documento",
)
async def download_document(
    doc_id:  int,
    session: AsyncSession = Depends(get_session),
    _:       str = Depends(require_admin_key),
):
    doc = await session.get(PartnerDocument, doc_id)
    if not doc:
        raise HTTPException(404, "Documento não encontrado")
    path = Path(doc.stored_path)
    if not path.exists():
        raise HTTPException(404, "Arquivo não encontrado no servidor")
    return FileResponse(path=path, filename=doc.filename)
