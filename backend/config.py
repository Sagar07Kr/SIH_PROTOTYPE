"""Runtime configuration. Everything here has a working default so that the
demo path runs with no .env file, no API key and no network access (I7)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- storage
    data_dir: Path = ROOT_DIR / "var"
    fonts_dir: Path = BACKEND_DIR / "assets" / "fonts"
    sample_dir: Path = ROOT_DIR / "sample-data"
    database_url: str = ""            # empty -> sqlite in data_dir

    # --- provider
    ai_provider: str = "mock"        # mock | openai
    ai_api_key: str = ""
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"
    ai_timeout_s: float = 60.0
    ai_max_concurrency: int = 4
    mock_latency_scale: float = 1.0   # 0 disables the mock's simulated latency

    # --- limits (§12)
    max_upload_mb: int = 50
    max_pages: int = 200

    # --- engine knobs (documented in docs/LAYOUT_ENGINE.md)
    render_dpi_validate: int = 150
    render_dpi_ocr: int = 300
    render_dpi_thumb: int = 72
    prose_size_floor: float = 0.82   # fit ladder rung 3 floor
    cell_size_floor: float = 0.75    # table cells are allowed to go tighter
    ocr_min_confidence: float = 75.0
    overlap_tolerance: float = 0.02  # I5
    graphics_ssim_target: float = 0.98

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'layoutloom.db'}"

    @property
    def upload_dir(self) -> Path:
        p = self.data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def render_dir(self) -> Path:
        p = self.data_dir / "renders"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_dir(self) -> Path:
        p = self.data_dir / "versions"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
