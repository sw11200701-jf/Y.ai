"""Local encrypted credential adapter; protect data/secrets directory for deployment."""
import json
import threading
from cryptography.fernet import Fernet
from app.settings import settings

_lock = threading.RLock()

def _paths():
    folder = settings.data_dir / "secrets"
    folder.mkdir(parents=True, exist_ok=True)
    key_path = folder / "local.key"
    if not key_path.exists():
        with key_path.open("xb") as stream:
            stream.write(Fernet.generate_key())
    return Fernet(key_path.read_bytes()), folder / "credentials.enc"

def store_secret(ref: str, value: str):
    with _lock:
        cipher, path = _paths()
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
