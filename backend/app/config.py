"""Application settings.

Everything that differs between the demo, CI and a real deployment lives here so
that no module has to reach for os.environ directly.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- storage -------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://invoice:invoice@localhost:5433/invoice"
    # Documents are written here in the demo. In production this is swapped for a
    # GCS bucket behind the same interface (see app/pipeline/render.py).
    storage_dir: Path = REPO_ROOT / "storage"
    invoice_dir: Path = REPO_ROOT / "invoices"

    # --- the accounting system we must integrate with ------------------------
    accounting_api_base: str = "http://localhost:8080"
    accounting_api_key: str = "demo-key-1234"
    accounting_timeout_seconds: float = 20.0

    # --- extraction ----------------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    extraction_model: str = "google/gemini-3.7-flash"
    extraction_timeout_seconds: float = 180.0
    render_dpi: int = 200
    max_concurrent_extractions: int = 4

    # --- the automation / human-review boundary ------------------------------
    # Every one of these is a policy dial, not a constant. They are the knobs a
    # finance team would actually want to turn, so they are configuration.
    auto_post_enabled: bool = True
    confidence_floor: float = 0.80
    # Invoices above this total always get a human, however clean the extraction.
    # Set to 0 to disable. Chosen as a deliberately conservative default.
    amount_review_threshold_jpy: int = 1_000_000
    near_duplicate_window_days: int = 7
    fuzzy_partner_threshold: float = 85.0

    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously."""
        return self.database_url.replace("+asyncpg", "+psycopg2").replace(
            "postgresql+psycopg2", "postgresql"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
