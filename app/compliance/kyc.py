"""
KYC — validação de participantes (remetente e destinatário).

Regras implementadas:
  1. Formato de CPF (11 dígitos) ou CNPJ (14 dígitos) + dígitos verificadores.
  2. ISPB deve ter 8 dígitos numéricos.
  3. Número de conta não pode ser vazio.
  4. Documentos bloqueados são rejeitados (delegado ao blocklist).
"""
import re
from dataclasses import dataclass

from app.models import ParticipantInfo


@dataclass
class KYCResult:
    passed: bool
    reason: str = ""


# ── validadores de documento ────────────────────────────────────────────────

def _digits_only(v: str) -> str:
    return re.sub(r"\D", "", v)


def _cpf_valid(cpf: str) -> bool:
    d = _digits_only(cpf)
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for i in range(2):
        total = sum(int(d[j]) * (10 + i - j) for j in range(9 + i))
        check = (total * 10 % 11) % 10
        if check != int(d[9 + i]):
            return False
    return True


def _cnpj_valid(cnpj: str) -> bool:
    d = _digits_only(cnpj)
    if len(d) != 14 or len(set(d)) == 1:
        return False
    weights1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    weights2 = [6] + weights1
    for weights, pos in [(weights1, 12), (weights2, 13)]:
        total = sum(int(d[i]) * weights[i] for i in range(len(weights)))
        rem = total % 11
        check = 0 if rem < 2 else 11 - rem
        if check != int(d[pos]):
            return False
    return True


def validate_document(document: str) -> KYCResult:
    d = _digits_only(document)
    if len(d) == 11:
        if not _cpf_valid(d):
            return KYCResult(False, f"CPF inválido: {document}")
        return KYCResult(True)
    if len(d) == 14:
        if not _cnpj_valid(d):
            return KYCResult(False, f"CNPJ inválido: {document}")
        return KYCResult(True)
    return KYCResult(False, f"Documento com comprimento inválido ({len(d)} dígitos): {document}")


def validate_ispb(ispb: str) -> KYCResult:
    d = _digits_only(ispb)
    if len(d) != 8:
        return KYCResult(False, f"ISPB deve ter 8 dígitos: '{ispb}'")
    return KYCResult(True)


# ── validação completa de participante ──────────────────────────────────────

def validate_participant(p: ParticipantInfo) -> KYCResult:
    result = validate_ispb(p.ispb)
    if not result.passed:
        return result

    result = validate_document(p.document)
    if not result.passed:
        return result

    if not p.account or not p.account.strip():
        return KYCResult(False, "Número de conta não pode ser vazio")

    if not p.name or not p.name.strip():
        return KYCResult(False, "Nome do participante não pode ser vazio")

    return KYCResult(True)
