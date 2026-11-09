"""Bounded cache of pure parser fingerprints; never cache authorization/text."""
from collections import OrderedDict
from threading import Lock
import hashlib

_cache = OrderedDict()
_lock = Lock()


def fingerprint(*, content, kind, version_id, transport_version):
    from .semantic_document import windows_from_bytes
    from app.agents.builtin_chunker import digest
    # Current source bytes are hashed on every access. ACL, model/policy,
    # source binding and supplied-plan checks remain at their original gates.
    key = (hashlib.sha256(content).hexdigest(), kind, version_id, transport_version)
    with _lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
            return value
    windows = windows_from_bytes(content=content, kind=kind,
        document_version_id=version_id, transport_version=transport_version)
    value = (digest(windows), tuple(w['input']['window_id'] for w in windows))
    with _lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
    return value
