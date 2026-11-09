"""Opt-in local Windows OCR helper; no HTTP, rescaling or automatic approval."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import tempfile
from .slicing_contracts import fail

HELPER = Path(__file__).with_name('slicing_assets') / 'windows_ocr_v1.ps1'


def executable():
    if platform.system() != 'Windows': fail('Windows本地OCR适配仅在已验证Windows主机可用', 'SLICING_OCR_UNAVAILABLE')
    root = Path(os.environ.get('WINDIR', 'C:/Windows')).resolve()
    path = (root / 'System32/WindowsPowerShell/v1.0/powershell.exe').resolve()
    if not path.is_relative_to(root) or not path.is_file(): fail('Windows PowerShell OCR宿主不可用', 'SLICING_OCR_UNAVAILABLE')
    return path


def binary_pins():
    path = executable()
    return {'helper_sha256': hashlib.sha256(HELPER.read_bytes()).hexdigest(),
            'host_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'os_build': platform.version()}


def recognize(*, content, width, height, work_root, language_tag='zh-Hans-CN', expected_pins=None):
    if expected_pins is not None and binary_pins() != expected_pins: fail('本地OCR宿主、脚本或系统已变化，须重新验证', 'SLICING_OCR_QUALIFICATION_CHANGED')
    if not isinstance(content, bytes) or not 0 < len(content) <= 40*1024*1024 or type(width) is not int or type(height) is not int or not 0 < width*height <= 40000000:
        fail('OCR输入大小或像素尺寸无效', 'SLICING_OCR_INVALID')
    if language_tag not in ('zh-Hans-CN', 'en-US'): fail('OCR语言尚未适配', 'SLICING_OCR_UNAVAILABLE')
    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='ocr-', dir=root)).resolve()
    if not directory.is_relative_to(root): fail('OCR暂存路径越界')
    source = directory / 'input.image'
    try:
        with source.open('xb') as handle: handle.write(content)
        result = subprocess.run([str(executable()), '-NoProfile', '-NonInteractive', '-File', str(HELPER),
            '-ImagePath', str(source), '-LanguageTag', language_tag], stdin=subprocess.DEVNULL,
            capture_output=True, timeout=30, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0: fail('本地OCR未成功结束；未重试或改用外部识别服务', 'SLICING_OCR_FAILED')
        if len(result.stdout) > 4*1024*1024: fail('OCR输出超过限额', 'SLICING_OCR_INVALID')
        value = json.loads(result.stdout.decode('utf-8-sig'))
        if (value.get('adapter') != 'windows-media-ocr-v1' or value.get('language_tag') != language_tag
                or value.get('width') != width or value.get('height') != height or abs(value.get('text_angle', 1)) > .1): fail('OCR原图、语言或坐标空间不符', 'SLICING_OCR_INVALID')
        lines = value.get('lines')
        if not isinstance(lines, list) or len(lines) > 10000: fail('OCR行结构或数量无效', 'SLICING_OCR_INVALID')
        for line in lines:
            region = line.get('region')
            if (type(line) is not dict or set(line) != {'text', 'region'} or not isinstance(line['text'], str)
                    or not isinstance(region, list) or len(region) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in region)
                    or not 0 <= region[0] < region[2] <= width or not 0 <= region[1] < region[3] <= height): fail('OCR行框超出原图或不是有限像素坐标', 'SLICING_OCR_INVALID')
        return value
    except subprocess.TimeoutExpired:
        fail('本地OCR超过30秒并终止独立宿主；未自动重试', 'SLICING_OCR_TIMEOUT')
    except (ValueError, UnicodeError, KeyError, TypeError):
        fail('本地OCR返回无效结果', 'SLICING_OCR_INVALID')
    finally:
        # Only the two resolved, server-created paths may be removed.
        if source.is_relative_to(root): source.unlink(missing_ok=True)
        if directory.is_relative_to(root):
            try: directory.rmdir()
            except OSError: pass


def install_qualified(receipt_path, *, data_root):
    """Trusted startup, not an HTTP body or a model tool. Default remains off."""
    from .slicing_parser import register_local_adapters
    path = Path(receipt_path).resolve()
    root = Path(data_root).resolve()
    if not path.is_relative_to(root): fail('OCR资格收据须在本服务私有数据目录', 'SLICING_OCR_UNAVAILABLE')
    try: value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError): fail('OCR资格收据不可用', 'SLICING_OCR_UNAVAILABLE')
    pins = binary_pins()
    if value.get('adapter') != 'windows-media-ocr-v1' or value.get('binary_pins') != pins or value.get('status') != 'qualified_synthetic_geometry_only' or not value.get('fixture_sha256'):
        fail('OCR资格收据与当前宿主不匹配', 'SLICING_OCR_UNAVAILABLE')
    register_local_adapters(ocr=lambda **inputs: recognize(**inputs, work_root=root/'slicing-temp',
        language_tag=value['language_tag'], expected_pins=pins)['lines'])
