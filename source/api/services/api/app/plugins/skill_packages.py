"""Passive, bounded skill packages. No extraction, imports, hooks or execution."""
import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.errors import AppError

MAX_UPLOAD = 1048576
MAX_EXPANDED = 262144
MAX_FILE = 65536
MAX_FILES = 32
ALLOWED_SUFFIXES = {'.md', '.txt', '.csv', '.json'}


def invalid(message='技能包格式不受支持或超过限额'):
    raise AppError('SKILL_PACKAGE_INVALID', message, 422)


def safe_name(value, *, directory=False):
    if (not isinstance(value, str) or not value or len(value) > 180 or value != unicodedata.normalize('NFC', value)
            or '\\' in value or ':' in value or '%' in value or value.startswith('/')
            or any(ord(c) < 32 or unicodedata.category(c) in {'Cf', 'Cs'} for c in value)):
        invalid('技能文件名或路径不安全')
    path = PurePosixPath(value)
    if any(part in {'', '.', '..'} for part in value.split('/')) or len(path.parts) > 5:
        invalid('技能文件不能使用跳转路径或过深目录')
    if not directory and path.suffix.lower() not in ALLOWED_SUFFIXES:
        invalid('仅接受Markdown、文本、CSV和JSON指导附件，不接受脚本或嵌套归档')
    return value


def text_bytes(value):
    try:
        if len(value) > MAX_FILE:
            invalid('技能单文件超过64KiB')
        text = value.decode('utf-8-sig', errors='strict')
    except UnicodeError:
        invalid('技能文件必须为UTF-8文本')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text) or any(unicodedata.category(c) == 'Cs' for c in text):
        invalid('技能文件包含二进制或无效控制字符')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def frontmatter(text):
    """Deliberately restricted scalar YAML subset: name + description only.

    Quoted scalars and indented |/> descriptions work; tags, collections,
    anchors, includes, scripts and arbitrary metadata have no interpretation.
    """
    if not text.startswith('---\n'):
        invalid('SKILL.md须含name和description文本元数据')
    end = text.find('\n---\n', 4)
    if end < 0 or end > 8192:
        invalid('技能元数据未正确结束或超过8KiB')
    lines = text[4:end].split('\n')
    result = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        match = re.fullmatch(r'(name|description):[ \t]*(.*)', line)
        if not match or match[1] in result:
            invalid('技能元数据只允许不重复的name和description')
        key, value = match.groups()
        if value in {'|', '>'}:
            block = []
            while index < len(lines) and (lines[index].startswith('  ') or not lines[index].strip()):
                block.append(lines[index][2:] if lines[index].startswith('  ') else '')
                index += 1
            value = ('\n' if value == '|' else ' ').join(block).strip()
        elif value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                invalid('双引号元数据须为有效JSON文本标量')
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        elif not value or value[0] in '!&*[{>' or value in {'null', 'true', 'false'}:
            invalid('技能元数据仅支持文本标量，不支持YAML对象或执行标记')
        if not isinstance(value, str):
            invalid('技能元数据必须为文本')
        result[key] = value
    if set(result) != {'name', 'description'}:
        invalid('技能缺少name或description')
    body = text[end+5:].strip()
    return {**result, 'instructions': body}


class SkillFile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str = Field(min_length=1, max_length=180)
    text: str = Field(max_length=65536)


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(pattern=r'^[a-z][a-z0-9-]{0,63}$')
    description: str = Field(min_length=1, max_length=2000)
    instructions: str = Field(min_length=1, max_length=32768)
    references: list[SkillFile] = Field(default_factory=list, max_length=31)

    @model_validator(mode='after')
    def bounded(self):
        if not self.description.strip() or not self.instructions.strip():
            raise ValueError('技能说明和指导正文不能为空白')
        names = set()
        total = len(self.instructions.encode('utf-8')) + len(self.description.encode('utf-8'))
        for file in self.references:
            safe_name(file.path)
            if file.path.casefold() == 'skill.md' or file.path.casefold() in names:
                raise ValueError('技能附件名称重复或冒充主文件')
            names.add(file.path.casefold())
            text_bytes(file.text.encode('utf-8'))
            total += len(file.text.encode('utf-8'))
        if total > MAX_EXPANDED:
            raise ValueError('技能正文和附件总量超过256KiB')
        text_bytes(self.instructions.encode('utf-8'))
        text_bytes(self.description.encode('utf-8'))
        return self


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def package_hash(draft):
    parsed = SkillDraft.model_validate(draft).model_dump(mode='json')
    parsed['references'].sort(key=lambda item: item['path'])
    return hashlib.sha256(canonical(parsed).encode('utf-8')).hexdigest()


def parse_upload(filename, body):
    if type(body) is not bytes or not body or len(body) > MAX_UPLOAD:
        invalid('技能上传须为1字节至1MiB')
    if not isinstance(filename, str) or '/' in filename or '\\' in filename or len(filename) > 150:
        invalid('上传文件名无效')
    files = {}
    if filename.lower().endswith('.md'):
        files['SKILL.md'] = text_bytes(body)
    elif filename.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                infos = archive.infolist()
                if not 1 <= len(infos) <= 64:
                    invalid('技能归档最多64个条目和32个文件')
                total = 0
                names = set()
                for info in infos:
                    if info.orig_filename != info.filename:
                        invalid('技能归档原始名称含路径分隔或空字节规范化')
                    mode = info.external_attr >> 16
                    if info.flag_bits & 1 or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                        invalid('技能归档不能包含加密文件、符号链接或特殊文件')
                    if info.is_dir():
                        safe_name(info.orig_filename[:-1], directory=True)
                        continue
                    if stat.S_IFMT(mode) == stat.S_IFDIR:
                        invalid('技能归档目录元数据不一致')
                    name = safe_name(info.filename)
                    if name.casefold() in names:
                        invalid('技能归档文件名重复')
                    names.add(name.casefold())
                    if len(names) > MAX_FILES:
                        invalid('技能归档最多32个文件')
                    total += info.file_size
                    if (info.file_size > MAX_FILE or total > MAX_EXPANDED
                            or info.file_size > max(1024, info.compress_size*100)):
                        invalid('技能归档展开大小或压缩比例超限')
                    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        invalid('技能归档只接受普通ZIP压缩')
                    with archive.open(info) as handle:
                        content = handle.read(MAX_FILE+1)
                    if len(content) != info.file_size:
                        invalid('技能归档文件长度不一致')
                    files[name] = text_bytes(content)
        except AppError:
            raise
        except (zipfile.BadZipFile, RuntimeError, ValueError, OSError, NotImplementedError, zlib.error, EOFError):
            invalid('技能ZIP无效或无法安全读取')
    else:
        invalid('请上传SKILL.md或仅含文本资源的ZIP包')
    roots = [path for path in files if PurePosixPath(path).name == 'SKILL.md']
    if len(roots) != 1:
        invalid('技能包须有且仅有一个SKILL.md')
    main = roots[0]
    prefix = main[:-len('SKILL.md')]
    if prefix and any(not path.startswith(prefix) for path in files):
        invalid('技能包不能含主技能目录以外的文件')
    try:
        draft = SkillDraft.model_validate({**frontmatter(files[main]), 'references': [
            {'path': path[len(prefix):], 'text': content} for path, content in sorted(files.items()) if path != main]})
    except ValueError:
        invalid('技能名称、说明、正文或附件不符合限制')
    value = draft.model_dump(mode='json')
    return {'draft': value, 'content_sha256': package_hash(value),
            'upload_sha256': hashlib.sha256(body).hexdigest(), 'executable': False,
            'attachment_policy': 'passive_utf8_text_only', 'tools': [],
            'warnings': ['技能正文与附件仅作不可信指导资料；上传不会运行脚本、安装依赖或授予工具权限']}
