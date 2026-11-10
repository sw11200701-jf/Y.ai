import os
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[3]

@dataclass
class Settings:
    data_dir: Path = Path(os.getenv("FINANCE_DATA_DIR", str(ROOT / "data"))).resolve()
    database_url: str = os.getenv("DATABASE_URL", "")
    application_api_enabled: bool = os.getenv("FINANCE_APPLICATION_API_ENABLED", "false").lower() == "true"
    application_public_base_url: str = os.getenv('FINANCE_APPLICATION_API_PUBLIC_BASE_URL','')
    runner_concurrency: int = int(os.getenv("RUNNER_CONCURRENCY", "2"))
    runner_queue_limit: int = int(os.getenv("RUNNER_QUEUE_LIMIT", "20"))
    run_timeout_seconds: float = float(os.getenv("RUN_TIMEOUT_SECONDS", "120"))
    run_retention_days: int = int(os.getenv("RUN_RETENTION_DAYS", "90"))
    document_job_timeout_max_seconds: int = int(os.getenv('DOCUMENT_JOB_TIMEOUT_MAX_SECONDS','86400'))
    document_model_timeout_seconds: int = int(os.getenv('DOCUMENT_MODEL_TIMEOUT_SECONDS','600'))
    tool_allowed_origins: tuple[str, ...] = tuple(os.getenv("TOOL_ALLOWED_ORIGINS", "http://127.0.0.1:8011").split(","))
    development_http: bool = os.getenv("FINANCE_DEVELOPMENT_HTTP", "false").lower() == "true"
    allowed_web_origins: tuple[str, ...] = tuple(os.getenv("FINANCE_ALLOWED_WEB_ORIGINS", "https://localhost").split(","))
    session_absolute_seconds: int = int(os.getenv("FINANCE_SESSION_TTL", "28800"))
    session_idle_seconds: int = int(os.getenv("FINANCE_SESSION_IDLE", "1800"))

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"

settings = Settings()
if not 1 <= settings.run_retention_days <= 36500:
    raise ValueError("RUN_RETENTION_DAYS_MUST_BE_BETWEEN_1_AND_36500")
if not settings.database_url:
    settings.database_url = "sqlite:///" + (settings.data_dir / "demo.sqlite3").as_posix()
