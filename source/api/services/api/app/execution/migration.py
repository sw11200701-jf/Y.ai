"""Schema6 is additive; backup existing SQLite before new execution tables."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import inspect, text
from app.errors import AppError


def prepare(engine, data_dir):
    if not inspect(engine).has_table("resources"): return None
    with engine.connect() as connection:
        if inspect(engine).has_table("schema_versions") and connection.execute(text("SELECT 1 FROM schema_versions WHERE version=6")).first(): return None
    if engine.dialect.name != "sqlite": raise AppError("EXECUTION_MIGRATION_REVIEW_REQUIRED", "执行状态迁移需先验证此数据库的备份恢复", 503)
    folder = Path(data_dir)/"backups"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder/("pre-execution6-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")+".sqlite3")
    source_path = Path(engine.url.database).resolve()
    with closing(sqlite3.connect(source_path.as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
    return str(destination)
