"""Non-secret loopback preview identity, never an authorization credential."""
import hashlib
import os
from pathlib import Path
from app.settings import settings, ROOT


def local_preview_identity():
    if not settings.development_http:
        return None
    def digest(path):
        return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()
    return {"application": "zhangpu", "project_sha256": digest(ROOT), "data_sha256": digest(settings.data_dir),
            "pid": os.getpid(), "instance_id": os.environ.get("ZHANGPU_LAUNCH_INSTANCE", "")}
