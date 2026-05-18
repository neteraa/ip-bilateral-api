# IP Bilateral API

API bilateral entre Instituições de Pagamento (IPs) — Python + FastAPI.

## Fluxo de uma transferência

```
IP-A (remetente)                    IP-B (recebedora)
        │                                   │
        ├─ POST /transfers ────────────────►│  recebe + valida HMAC
        │                                   ├─ status: PROCESSING
        │◄─ 201 { transfer_id, PROCESSING } ─┤
        │                                   ├─ liquida na conta do beneficiário
        │                                   ├─ status: SETTLED
        │◄─ POST /webhooks (SETTLED) ────── ┤  notifica IP-A
        │  atualiza status local            │
```

## Setup rápido

```bash
cd ip-bilateral-api
cp .env.example .env         # edite com seus dados
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Docs interativas: http://localhost:8000/docs

## Rodando dois nós localmente (simulação bilateral)

**Terminal 1 — IP-A (porta 8000):**
```bash
THIS_ISPB=11111111 THIS_NAME="IP-A" API_KEY_ID=key_a API_SECRET=secret_a \
KNOWN_PEERS_JSON='{"key_b":"secret_b"}' \
PEER_WEBHOOK_URL=http://localhost:8001/webhooks \
uvicorn app.main:app --port 8000
```

**Terminal 2 — IP-B (porta 8001):**
```bash
THIS_ISPB=22222222 THIS_NAME="IP-B" API_KEY_ID=key_b API_SECRET=secret_b \
KNOWN_PEERS_JSON='{"key_a":"secret_a"}' \
PEER_WEBHOOK_URL=http://localhost:8000/webhooks \
uvicorn app.main:app --port 8001
```

## Enviando uma transferência (IP-A → IP-B)

```python
import asyncio
from decimal import Decimal
from app.client import send_transfer
from app.models import ParticipantInfo

async def main():
    resp = await send_transfer(
        peer_base_url="http://localhost:8001",
        amount=Decimal("150.00"),
        sender=ParticipantInfo(
            ispb="11111111", name="João Silva",
            document="12345678901", account="0001234-5"
        ),
        receiver=ParticipantInfo(
            ispb="22222222", name="Maria Souza",
            document="98765432100", account="0009876-5"
        ),
        description="Pagamento serviço X",
    )
    print(resp)

asyncio.run(main())
```

## Autenticação

Toda requisição entre IPs usa HMAC-SHA256:

```
X-IP-Key-Id:    <api_key_id>
X-IP-Timestamp: <unix_ms>
X-IP-Signature: HMAC-SHA256(secret, "key_id:timestamp_ms:sha256(body)")
```

- Janela de tolerância: **30 segundos** (protege contra replay).
- Em produção: adicionar mTLS por cima para dupla camada.

## Endpoints

| Método | Path | Descrição |
|--------|------|-----------|
| `POST` | `/transfers` | Recebe transferência de outra IP |
| `GET` | `/transfers/{id}` | Consulta status |
| `POST` | `/webhooks` | Recebe notificação de evento |
| `POST` | `/reconciliation` | Conciliação bilateral |
| `GET` | `/health` | Health check |

## Produção

- Trocar `DATABASE_URL` para PostgreSQL (`postgresql+asyncpg://...`)
- Adicionar mTLS (certificados por IP, validados pelo Banco Central)
- Substituir `asyncio.create_task` no processor por Celery/RQ
- Rate limiting com `slowapi`
- Logs estruturados (JSON) para SIEM
