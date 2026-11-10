"""Local encrypted credential adapter; protect data/secrets directory for deployment."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import threading
from cryptography.fernet import Fernet
from app.settings import settings

_lock = threading.RLock()

def master_key_bytes():
    configured = os.environ.get("FINANCE_SECRET_KEY_FILE")
    if configured:
        key_path = Path(configured)
        if not key_path.is_absolute():
            raise ValueError("FINANCE_SECRET_KEY_FILE_REQUIRES_ABSOLUTE_PATH")
        return key_path.read_bytes().strip()
    folder = settings.data_dir / "secrets"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = folder / "local.key"
    if not key_path.exists():
        # Publish the whole key atomically. Concurrent workers share the winner.
        with NamedTemporaryFile(dir=folder, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(Fernet.generate_key())
        try:
            os.link(temporary, key_path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    return key_path.read_bytes().strip()


def _paths():
    return Fernet(master_key_bytes()), settings.data_dir / "secrets" / "credentials.enc"

def validate_master_key_configuration():
    if not os.environ.get("FINANCE_SECRET_KEY_FILE"):
        return
    try:
        cipher, path = _paths()
        if path.exists():
            cipher.decrypt(path.read_bytes())
    except Exception:
        raise ValueError("FINANCE_SECRET_KEY_FILE_INVALID_OR_MISMATCHED") from None


def store_secret(ref: str, value: str):
    with _lock:
        cipher, path = _paths()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        values = json.loads(cipher.decrypt(path.read_bytes())) if path.exists() else {}
        values[ref] = value
        temp = path.with_suffix(".tmp")
        temp.write_bytes(cipher.encrypt(json.dumps(values).encode()))
        temp.replace(path)

def read_secret(ref: str) -> str | None:
    with _lock:
        cipher, path = _paths()
        return json.loads(cipher.decrypt(path.read_bytes())).get(ref) if path.exists() else None


def delete_secret(ref: str):
    """Remove a revoked catalogue credential using the existing encrypted vault."""
    with _lock:
        cipher, path = _paths()
        if not path.exists():
            return
        values = json.loads(cipher.decrypt(path.read_bytes()))
        if ref not in values:
            return
        del values[ref]
        temp = path.with_suffix('.tmp')
        temp.write_bytes(cipher.encrypt(json.dumps(values).encode()))
        temp.replace(path)

set_secret = store_secret
get_secret = read_secret
