"""Standard Ed25519 offline markers/attestations; contains no private identity."""

import base64
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PREFIX = "ZP_ORIGIN_1:"
TOKEN = re.compile(rb"ZP_ORIGIN_1:[A-Za-z0-9_-]{100,8000}")


class VerificationError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def public_bytes(key):
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(key):
    return hashlib.sha256(public_bytes(key)).hexdigest()[:24]


def sign_record(payload, key):
    return {"payload": payload, "signature": encode(key.sign(canonical(payload))), "public_key": encode(public_bytes(key.public_key()))}


def verify_record(record, trusted_key):
    try:
        if record["public_key"] != encode(public_bytes(trusted_key)):
            raise VerificationError("KEY_MISMATCH")
        trusted_key.verify(decode(record["signature"]), canonical(record["payload"]))
        return record["payload"]
    except VerificationError:
        raise
    except (ValueError, KeyError, TypeError, InvalidSignature) as exc:
        raise VerificationError("SIGNATURE_INVALID") from exc


def make_marker(payload, key):
    return PREFIX + encode(canonical(sign_record(payload, key)))


def verify_marker(marker, trusted_key):
    try:
        if not marker.startswith(PREFIX):
            raise ValueError()
        record = json.loads(decode(marker[len(PREFIX):]))
        payload = verify_record(record, trusted_key)
        if set(payload) != {"format", "issuance_id", "component", "opaque_claim", "nonce", "key_id"} or payload["format"] != "origin-marker-v1":
            raise ValueError()
        if payload["key_id"] != key_id(trusted_key):
            raise VerificationError("KEY_MISMATCH")
        return payload
    except VerificationError:
        raise
    except Exception as exc:
        raise VerificationError("MARKER_INVALID") from exc


def artifact_files(artifact):
    artifact = Path(artifact).resolve()
    if artifact.is_file():
        return [(artifact.name, artifact)]
    if not artifact.is_dir():
        raise VerificationError("ARTIFACT_NOT_FOUND")
    files = []
    for path in artifact.rglob("*"):
        relative = path.relative_to(artifact)
        if not path.is_file() or any(p in {"cache", ".git", "node_modules"} for p in relative.parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(artifact):
            raise VerificationError("ARTIFACT_SYMLINK")
        files.append((relative.as_posix(), path))
    return sorted(files)


def file_manifest(artifact):
    result = []
    for relative, path in artifact_files(artifact):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1048576), b""):
                digest.update(chunk)
        result.append({"path": relative, "size": path.stat().st_size, "sha256": digest.hexdigest()})
    if not result:
        raise VerificationError("ARTIFACT_EMPTY")
    return result


def extract_markers(artifact, trusted_key, component):
    # Locations are returned only to the private issuing ledger, never attestation.
    found = []
    for relative, path in artifact_files(artifact):
        if path.suffix == ".zip":
            with ZipFile(path) as archive:
                for entry in archive.infolist():
                    if entry.file_size > 16 * 1024 * 1024 or not entry.filename.endswith((".py", ".js", ".html", ".json")):
                        continue
                    for match in TOKEN.finditer(archive.read(entry)):
                        marker = match.group().decode()
                        payload = verify_marker(marker, trusted_key)
                        if payload["component"] == component:
                            found.append({"file": relative, "entry": entry.filename, "offset": match.start(), "marker": marker, "payload": payload})
        elif path.suffix in {".py", ".js", ".mjs", ".cjs", ".html", ".json", ".ts"} and path.stat().st_size <= 16 * 1024 * 1024:
            for match in TOKEN.finditer(path.read_bytes()):
                marker = match.group().decode()
                payload = verify_marker(marker, trusted_key)
                if payload["component"] == component:
                    found.append({"file": relative, "offset": match.start(), "marker": marker, "payload": payload})
    if not found:
        raise VerificationError("MARKER_MISSING")
    return found


def verify_artifact(*, artifact, attestation, trusted_key):
    payload = verify_record(attestation, trusted_key)
    if (not isinstance(payload, dict) or payload.get("format") != "origin-attestation-v1"
            or payload.get("component") not in {"frontend", "backend"}
            or payload.get("key_id") != key_id(trusted_key)
            or not isinstance(payload.get("files"), list)
            or not all(isinstance(payload.get(key), str) and payload[key] for key in ("issuance_id", "opaque_claim"))):
        raise VerificationError("ATTESTATION_INVALID")
    actual = file_manifest(artifact)
    if actual != payload["files"]:
        raise VerificationError("DIGEST_MISMATCH")
    markers = extract_markers(artifact, trusted_key, payload["component"])
    if any(m["payload"]["issuance_id"] != payload["issuance_id"] or m["payload"]["opaque_claim"] != payload["opaque_claim"] for m in markers):
        raise VerificationError("ISSUANCE_MISMATCH")
    return {"verified": True, "component": payload["component"], "key_id": key_id(trusted_key), "file_count": len(actual), "marker_count": len(markers)}
