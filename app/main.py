import logging

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routes import transfers, webhooks, reconciliation
from app.routes import admin, onboarding, partner, prefunded
from app.compliance.blocklist import reload_external_lists

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    reload_external_lists()
    yield


app = FastAPI(
    title="IP Bilateral API",
    description=(
        "API bilateral entre Instituições de Pagamento (IPs).\n\n"
        "Autenticação via HMAC-SHA256 (header `X-IP-Signature`).\n"
        "Cada IP roda esta mesma aplicação — simetria total."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transfers.router)
app.include_router(webhooks.router)
app.include_router(reconciliation.router)
app.include_router(admin.router)
app.include_router(onboarding.router)
app.include_router(partner.router)
app.include_router(prefunded.router)

PUBLIC = Path(__file__).parent.parent / "public"
if PUBLIC.exists():
    app.mount("/public", StaticFiles(directory=str(PUBLIC)), name="public")


@app.get("/", include_in_schema=False)
async def root():
    index = PUBLIC / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "ok",
        "ispb":   settings.this_ispb,
        "name":   settings.this_name,
    }
