"""Read-only customer presentation; original persisted text/evidence untouched."""
import hashlib,re


def exported_id(version_id, sequence):
    prefix = hashlib.sha256(version_id.encode()).hexdigest()[:8].upper()
    return f'Y-{prefix}-{sequence:04d}'


def knowledge_id(chunk, version_id, sequence):
    return chunk.get('knowledge_key') or chunk.get('knowledge_id') or exported_id(version_id, sequence)


def historical_id(chunk):
    # Early manual revisions kept the original internal ID instead of its XLSX
    # alias. Both remain valid lookup keys for the same persisted knowledge.
    match = re.fullmatch(r'(knowledge_version_[0-9a-f]{32})_chunk_([1-9][0-9]*)', chunk.get('knowledge_key') or '')
    return exported_id(match[1], int(match[2])) if match else None

def text(chunk):
    value=chunk.get('text','')
    if (not isinstance(value,str) or chunk.get('revised') or not str(chunk.get('unit_id','')).startswith('record_')
            or chunk.get('transport_version')!='xlsx-packed-cells-v1'):
        return value
    spans=chunk.get('source_spans')
    if not isinstance(spans,list) or not spans:return value
    covered=[]
    for span in spans:
        a,b=span.get('chunk_start'),span.get('chunk_end')
        if type(a) is not int or type(b) is not int or not 0<=a<b<=len(value):return value
        if hashlib.sha256(value[a:b].encode()).hexdigest()!=span.get('text_sha256'):return value
        covered.append((a,b))
    def original(a,b):return any(a<y and b>x for x,y in covered)
    result=[];offset=0
    for line in value.splitlines(keepends=True):
        end=offset+len(line)
        # Only parser-generated, unmapped framing is hidden. Identical words
        # inside an original cell or a human revision are kept verbatim.
        if re.fullmatch(r'(?:工作表：[^\r\n]*|记录行：[0-9]+)\r?\n?',line) and not original(offset,end):
            offset=end;continue
        prefix=re.match(r'[A-Z]{1,3}[1-9][0-9]*：',line)
        if prefix and not original(offset,offset+prefix.end()):line=line[prefix.end():]
        result.append(line);offset=end
    return ''.join(result).strip('\r\n')

def knowledge_content(chunk):
    body=text(chunk);topic=chunk.get('topic','')
    return ('【'+topic+'】\n' if isinstance(topic,str) and topic.strip() else '')+body


def original_paragraph(chunk):
    original = chunk.get('original_text')
    if isinstance(original, str) and original:
        return original
    body = chunk.get('text', '')
    expected = chunk.get('original_text_sha256')
    if not isinstance(body, str) or chunk.get('strategy') == 'semantic_derived':
        return ''
    if (expected is None and chunk.get('revised')) or (expected is not None and hashlib.sha256(body.encode()).hexdigest() != expected):
        return ''
    return body


def source_paragraphs(chunks, load_file):
    originals = [original_paragraph(chunk) for chunk in chunks]
    missing = [i for i, chunk in enumerate(chunks) if not originals[i] and chunk.get('original_text_sha256')
               and chunk.get('strategy') != 'semantic_derived']
    if not missing:
        return originals
    import base64
    from app.errors import AppError
    from app.imports.documents import _segments
    from .chunk_export import _original
    try:
        file = load_file()
        raw = base64.b64decode(file['content_base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != file['sha256']:
            return originals
        segments = list(_segments(raw, file['format']))
    except (AppError, ValueError, KeyError, TypeError):
        return originals
    for index in missing:
        chunk = chunks[index]
        try:
            original = ''.join(_original(span, segments, file['id'])[0] for span in chunk.get('source_spans', [chunk]))
        except (AppError, ValueError, KeyError, TypeError):
            continue
        if hashlib.sha256(original.encode()).hexdigest() == chunk['original_text_sha256']:
            originals[index] = original
    return originals


def semantic(chunk, *, fallback=True):
    topic = (chunk.get('topic') or '').strip()
    summary = (chunk.get('summary') or '').strip()
    if not summary and (chunk.get('strategy') == 'semantic_derived' or chunk.get('content_origin') == 'human_revision'):
        summary = (chunk.get('text') or '').strip()
    if summary:
        return f'{topic}\n{summary}' if topic and topic not in summary else summary
    if topic or not fallback:
        return topic
    paragraph = (chunk.get('original_text') or '').strip()
    return re.split(r'(?<=[。！？!?])|\n', paragraph, maxsplit=1)[0][:160]


def presentation(chunk):
    return {'semantic': semantic(chunk), 'original_paragraph': chunk.get('original_text') or ''}
