"""
Segurança da API bilateral entre IPs.

Mecanismo:
  - Cada IP tem um par (api_key_id, api_secret).
  - Toda requisição de saída inclui os headers:
      X-IP-Key-Id:   <api_key_id>
      X-IP-Timestamp: <unix_ms>
      X-IP-Signature: HMAC-SHA256(secret, "key_id:timestamp:body_sha256")
  - A IP receptora verifica a assinatura antes de processar.
  - Janela de tolerância: 30 segundos (protege contra replay attack).
"""
import hashlib
import hmac
import time
from typing import Optional

from fastapi import Header, HTTPException, Request, status


SIGNATURE_TOLERANCE_MS = 30_000


def _sign(secret: str, key_id: str, timestamp_ms: int, body_sha256: str) -> str:
    message = f"{key_id}:{timestamp_ms}:{body_sha256}"
    return hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def sign_request(secret: str, key_id: str, body: bytes) -> dict[str, str]:
    """Gera os headers de autenticação para enviar à outra IP."""
    ts = int(time.time() * 1000)
    body_hash = hashlib.sha256(body).hexdigest()
    sig = _sign(secret, key_id, ts, body_hash)
    return {
        "X-IP-Key-Id":    key_id,
        "X-IP-Timestamp": str(ts),
        "X-IP-Signature": sig,
    }


async def verify_request(
    request: Request,
    known_peers: dict[str, str],          # {key_id: secret}
    x_ip_key_id:    Optional[str] = Header(None),
    x_ip_timestamp: Optional[str] = Header(None),
    x_ip_signature: Optional[str] = Header(None),
) -> str:
    """
    Dependency FastAPI: verifica assinatura da requisição recebida.
    Retorna o key_id do peer autenticado.
    """
    if not all([x_ip_key_id, x_ip_timestamp, x_ip_signature]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Headers de autenticação ausentes (X-IP-Key-Id, X-IP-Timestamp, X-IP-Signature)",
        )

    # Verifica janela de tempo
    try:
        ts = int(x_ip_timestamp)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Timestamp inválido")

    now_ms = int(time.time() * 1000)
    if abs(now_ms - ts) > SIGNATURE_TOLERANCE_MS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Requisição fora da janela de {SIGNATURE_TOLERANCE_MS // 1000}s",
        )

    # Recupera o secret do peer
    secret = known_peers.get(x_ip_key_id)
    if not secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Key-Id desconhecido")

    # Calcula hash do body
    body = await request.body()
    body_hash = hashlib.sha256(body).hexdigest()

    # Verifica assinatura
    expected = _sign(secret, x_ip_key_id, ts, body_hash)
    if not hmac.compare_digest(expected, x_ip_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Assinatura inválida")

    return x_ip_key_id
