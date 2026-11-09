"""Local PDF text and actual image placements, including reused nested forms."""
from io import BytesIO
import math
from pypdf import PdfReader
from pypdf.generic import ContentStream
from .slicing_contracts import fail

IDENTITY = (1., 0., 0., 1., 0., 0.)


def multiply(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f)


def image_bounds(matrix):
    a, b, c, d, e, f = matrix
    points = [(e, f), (a+e, b+f), (c+e, d+f), (a+c+e, b+d+f)]
    return [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]


def objects(content):
    reader = PdfReader(BytesIO(content), strict=True)
    if reader.is_encrypted: fail('不支持加密PDF', 'SLICING_PARSE_INVALID')
    if len(reader.pages) > 200: fail('PDF超过200页', 'SLICING_PARSE_LIMIT')
    for page_number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            yield 'text', line, {'kind': 'page_line', 'page': page_number, 'line': number}, {}
        counter, operations_seen = [0], [0]

        def walk(stream, resources, matrix=IDENTITY, path=(), active=()):
            if len(path) > 8: fail('PDF嵌套图片资源超过8层', 'SLICING_PARSE_LIMIT')
            if stream is None: return
            decoded = stream.get_data()
            if len(decoded) > 80*1024*1024: fail('PDF内容流展开超过限额', 'SLICING_PARSE_LIMIT')
            parsed = ContentStream(stream, reader)
            stack = []
            for arguments, operator in parsed.operations:
                operations_seen[0] += 1
                if operations_seen[0] > 100000: fail('PDF页面操作数量超过限额', 'SLICING_PARSE_LIMIT')
                if operator == b'q': stack.append(matrix)
                elif operator == b'Q':
                    if not stack: fail('PDF图形状态不平衡', 'SLICING_PARSE_INVALID')
                    matrix = stack.pop()
                elif operator == b'cm':
                    values = tuple(float(n) for n in arguments)
                    if len(values) != 6 or any(not math.isfinite(n) for n in values): fail('PDF图形矩阵无效', 'SLICING_PARSE_INVALID')
                    matrix = multiply(matrix, values)
                elif operator == b'INLINE IMAGE':
                    counter[0] += 1
                    yield 'unresolved_image', b'', {'kind': 'pdf_inline_image', 'page': page_number, 'placement': counter[0]}, {'reason': 'INLINE_IMAGE_ADAPTER_UNAVAILABLE'}
                elif operator == b'Do':
                    name = str(arguments[0])
                    table = resources.get('/XObject', {})
                    table = table.get_object() if hasattr(table, 'get_object') else table
                    if name not in table: fail('PDF图片或表单资源缺失', 'SLICING_PARSE_INVALID')
                    reference = table.raw_get(name) if hasattr(table,'raw_get') else table.get(name)
                    obj = reference.get_object()
                    native = getattr(reference, 'idnum', None)
                    subtype = obj.get('/Subtype')
                    if subtype == '/Form':
                        marker = (native, str(name))
                        if marker in active: fail('PDF表单资源递归引用', 'SLICING_PARSE_INVALID')
                        transform = tuple(float(n) for n in obj.get('/Matrix', IDENTITY))
                        if len(transform) != 6 or any(not math.isfinite(n) for n in transform): fail('PDF表单矩阵无效')
                        nested = obj.get('/Resources', resources)
                        nested = nested.get_object() if hasattr(nested, 'get_object') else nested
                        yield from walk(obj, nested, multiply(matrix, transform), (*path, name), (*active, marker))
                    elif subtype == '/Image':
                        width, height = int(obj.get('/Width', 0)), int(obj.get('/Height', 0))
                        if width <= 0 or height <= 0 or width*height*4 > 80*1024*1024: fail('PDF图片展开尺寸超过限额', 'SLICING_PARSE_LIMIT')
                        counter[0] += 1
                        if counter[0] > 2000: fail('PDF页面图片出现超过限额', 'SLICING_PARSE_LIMIT')
                        key = (*path, name)
                        extracted = page.images[key if len(key) > 1 else name]
                        if len(extracted.data) > 40*1024*1024: fail('PDF图片文件超过限额', 'SLICING_PARSE_LIMIT')
                        locator = {'kind': 'pdf_image', 'page': page_number, 'resource_path': list(key), 'native_object_id': native,
                                   'placement': counter[0], 'bbox': image_bounds(matrix), 'coordinate_system': 'PDF user-space points; bottom-left origin'}
                        yield 'image', extracted.data, locator, {'width': width, 'height': height}
            if stack: fail('PDF图形状态未闭合', 'SLICING_PARSE_INVALID')

        resources = page.get('/Resources', {})
        resources = resources.get_object() if hasattr(resources, 'get_object') else resources
        yield from walk(page.get_contents(), resources)
