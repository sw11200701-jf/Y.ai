"""Normalize original Office bytes in the pinned local conversion container."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
from struct import error as StructError, pack, unpack
import subprocess
import tempfile

from app.errors import AppError
from app.imports.document_container import OFFICE_CONVERSIONS, validate_container

CONTRACT = "office-original-conversion-v1"
MAX_BYTES = 40 * 1024 * 1024


def sha(content):
    return hashlib.sha256(content).hexdigest()


def failure(code, message):
    return AppError(code, message, 409)


def run_converter(docker, image, kind, content):
    token = secrets.token_hex(16)
    name = "yai-office-converter-" + token
    label = "yai.office-converter.owner"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = [str(docker), "run", "-i", "--rm", "--name", name, "--label", label + "=" + token,
               "--pull=never", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "128", "--memory", "1024m",
               "--cpus", "2", "--tmpfs", "/tmp:size=256m,mode=1777", image, kind]
    try:
        result = subprocess.run(command, input=content, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=150, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        # Only the container created for this conversion can be stopped.
        try:
            probe = subprocess.run([str(docker), "inspect", name], capture_output=True,
                                   timeout=8, creationflags=flags)
            if probe.returncode == 0 and json.loads(probe.stdout)[0]["Config"]["Labels"].get(label) == token:
                subprocess.run([str(docker), "rm", "-f", name], capture_output=True,
                               timeout=8, creationflags=flags)
        except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
            pass
        raise failure("SLICING_OFFICE_CONVERSION_TIMEOUT", "原件转换超时，请稍后重试。") from exc
    except OSError as exc:
        raise failure("SLICING_OFFICE_ADAPTER_UNAVAILABLE", "本机 Office 转换组件当前无法启动。") from exc
    if result.returncode != 0 or not 0 < len(result.stdout) <= MAX_BYTES:
        raise failure("SLICING_OFFICE_CONVERSION_FAILED", "本机转换组件无法读取此原件，请检查文件是否加密、损坏或内容与后缀不符。")
    return result.stdout


def configure(data_dir):
    if os.name != 'nt' and os.environ.get('FINANCE_OFFICE_SOCKET'):
        return configure_server(data_dir)
    root = Path(data_dir) / "knowledge-office-converter"
    config_path = root / "config.json"
    if not config_path.exists():
        return None
    cache = root / "cache"
    invalid = failure("SLICING_OFFICE_CONVERTER_CONFIG_INVALID", "本机 Office 转换配置无效。")
    if any(path.is_symlink() for path in (root, config_path, cache)) or not cache.is_dir():
        raise invalid
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("contract") != CONTRACT or os.name != "nt":
        raise invalid
    image = config.get("image_id", "")
    docker = Path(config.get("docker_command", ""))
    command_sha = config.get("docker_command_sha256", "")
    formats = config.get("formats")
    if (not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            or not docker.is_absolute() or not isinstance(command_sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", command_sha)
            or not isinstance(formats, list) or not formats
            or any(not isinstance(kind, str) or kind not in OFFICE_CONVERSIONS for kind in formats)):
        raise invalid
    def convert_office(kind, content):
        try:
            actual_command_sha = sha(docker.read_bytes())
        except OSError as exc:
            raise failure("SLICING_OFFICE_ADAPTER_UNAVAILABLE", "本机 Office 转换命令当前不可用。") from exc
        if actual_command_sha != command_sha:
            raise invalid
        return run_converter(docker, image, kind, content)

    targets = {kind: OFFICE_CONVERSIONS[kind] for kind in formats}
    return cache_converter(cache, image, targets, convert_office), frozenset(formats)


def configure_server(data_dir):
    address = os.environ['FINANCE_OFFICE_SOCKET']
    identity = os.environ.get('FINANCE_OFFICE_CONVERTER_ID', '')
    if address != '/run/office/convert.sock' or not re.fullmatch(r'sha256:[0-9a-f]{64}', identity):
        raise ValueError('SERVER_OFFICE_SOCKET_CONFIG_INVALID')

    def connection(timeout):
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(timeout)
        try:
            client.connect(address)
        except BaseException:
            client.close()
            raise
        return client

    with connection(5) as client, client.makefile('rb') as stream:
        client.sendall(b'ping\n')
        if stream.readline(20) != b'office-v1\n':
            raise ValueError('SERVER_OFFICE_PROTOCOL_INVALID')

    cache = Path(data_dir) / 'knowledge-office-server-v1'
    cache.mkdir(mode=0o700, exist_ok=True)
    if cache.is_symlink() or not cache.is_dir() or cache.stat().st_mode & 0o077:
        raise ValueError('SERVER_OFFICE_CACHE_MUST_BE_PRIVATE')

    def convert_office(kind, content):
        try:
            with connection(150) as client, client.makefile('rb') as stream:
                client.sendall(kind.encode('ascii') + b'\n' + pack('!I', len(content)))
                client.sendall(content)
                size, = unpack('!I', stream.read(4))
                if not 0 < size <= MAX_BYTES:
                    raise failure('SLICING_OFFICE_CONVERSION_FAILED', '转换组件无法读取此原件，请检查原件是否加密或损坏。')
                output = stream.read(size)
                if len(output) != size:
                    raise OSError('INCOMPLETE_OFFICE_RESPONSE')
        except (OSError, ValueError, StructError) as exc:
            raise failure('SLICING_OFFICE_ADAPTER_UNAVAILABLE', '服务器 Office 转换组件当前无法完成读取。') from exc
        if kind == 'doc':
            from .slicing_doc_fields import normalize
            validate_container(output, 'docx')
            output, _ = normalize(output)
        return output

    targets = {**OFFICE_CONVERSIONS, 'doc': 'docx'}
    return cache_converter(cache, identity, targets, convert_office), frozenset(OFFICE_CONVERSIONS)


def cache_converter(cache, image, targets, runner):
    invalid = failure("SLICING_OFFICE_CONVERTER_CONFIG_INVALID", "Office 转换配置无效。")

    def convert(*, content, kind):
        if kind not in targets or not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
            raise failure("SLICING_OFFICE_FORMAT_INVALID", "原件大小或格式超出本机转换范围。")
        validate_container(content, kind)
        target = targets[kind]
        source_sha = sha(content)
        identity = {"contract": CONTRACT, "source_sha256": source_sha, "source_format": kind,
                    "target_format": target, "image_id": image}
        folder = cache / sha(json.dumps(identity, sort_keys=True).encode("utf-8"))

        def cached():
            path = folder / f"normalized.{target}"
            metadata_path = folder / "metadata.json"
            if (any(item.is_symlink() for item in (folder, path, metadata_path))
                    or not 0 < path.stat().st_size <= MAX_BYTES):
                raise failure("SLICING_OFFICE_CACHE_CHANGED", "原件转换缓存无法核对，请联系维护人员。")
            normalized = path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata != {**identity, "converted_sha256": sha(normalized)}:
                raise failure("SLICING_OFFICE_CACHE_CHANGED", "原件转换缓存无法核对，请联系维护人员。")
            validate_container(normalized, target)
            return normalized

        if folder.exists():
            return cached()
        normalized = runner(kind, content)
        validate_container(normalized, target)
        temporary = Path(tempfile.mkdtemp(prefix=".new-", dir=cache))
        if temporary.resolve().parent != cache.resolve() or folder.resolve().parent != cache.resolve():
            raise invalid
        try:
            (temporary / f"normalized.{target}").write_bytes(normalized)
            (temporary / "metadata.json").write_text(
                json.dumps({**identity, "converted_sha256": sha(normalized)}, sort_keys=True), encoding="utf-8")
            try:
                temporary.rename(folder)
            except OSError:
                if not folder.exists():
                    raise
            return cached()
        finally:
            if temporary.exists() and temporary.resolve().parent == cache.resolve():
                shutil.rmtree(temporary)

    return convert
