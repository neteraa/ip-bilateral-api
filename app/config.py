import json
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Identidade desta IP
    this_ispb:    str = "00000000"
    this_name:    str = "Minha IP"
    api_key_id:   str = "key_minha_ip"
    api_secret:   str = "TROCA_ESSE_SECRET_AGORA"

    # Peers conhecidos: JSON string => {"key_id": "secret", ...}
    known_peers_json: str = "{}"

    # URL base da outra IP (para envio de webhooks)
    peer_webhook_url: str = "http://localhost:8001/webhooks"

    # Banco de dados
    database_url: str = "sqlite+aiosqlite:///./bilateral.db"

    # Chave de admin (protege rotas /admin e /onboarding)
    admin_key: str = "TROCA_ESSA_ADMIN_KEY_AGORA"

    # Servidor
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    @property
    def known_peers(self) -> dict[str, str]:
        return json.loads(self.known_peers_json)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
