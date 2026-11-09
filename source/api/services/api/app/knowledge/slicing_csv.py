"""Three-column semantic output with explicit legacy five-column decoding."""
import csv
from io import StringIO
from .slicing_contracts import fail

HEADERS = ('知识ID', '知识内容', '相关附件(URL)', '关联性', '相关位置')
SEMANTIC_HEADERS = HEADERS[:3]
SEMANTIC_CONTRACT = 'semantic-three-columns-v1'
TEXT_HEADERS = HEADERS[:2]
TEXT_CONTRACT = 'semantic-text-two-columns-v1'


def formula_risk(value):
    return bool(value) and (value[0] in '\t\r\n' or value.lstrip()[:1] in ('=', '+', '-', '@'))


def encode_csv(rows, *, variant='original', headers=HEADERS):
    if headers not in (HEADERS, SEMANTIC_HEADERS, TEXT_HEADERS): fail('CSV列合同不受支持', 'CSV_INVALID')
    if variant not in ('original', 'excel_safe'): fail('未知CSV导出副本策略', 'CSV_INVALID')
    if not rows or len(rows) > 1000: fail('CSV记录数须为1至1000', 'CSV_INVALID')
    output = StringIO(newline='')
    writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
    writer.writerow(headers)
    ids, escaped = set(), []
    for index, row in enumerate(rows, 1):
        if type(row) is not dict or set(row) != set(headers) or any(type(row[k]) is not str or '\0' in row[k] for k in headers): fail('CSV必须符合所选列合同且为无NUL原值文本', 'CSV_INVALID')
        required = (headers[0], headers[1], HEADERS[4]) if headers == HEADERS else headers[:2]
        if any(not row[k].strip() for k in required): fail('知识ID、正文及合同必填项不能为空', 'CSV_INVALID')
        if row[HEADERS[0]] in ids: fail('知识ID不能重复', 'CSV_INVALID')
        ids.add(row[HEADERS[0]])
        values = []
        for key in headers:
            value = row[key]
            if formula_risk(value):
                if variant == 'original': fail('原值存在公式风险；请明确选择Excel安全副本', 'CSV_FORMULA_RISK')
                escaped.append({'row': index, 'column': key, 'original': value})
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    content = output.getvalue().encode('utf-8-sig')
    return {'content': content, 'variant': variant, 'escaped_cells': escaped, 'row_count': len(rows)}


def decode_csv(content):
    if not isinstance(content, bytes) or not content.startswith(b'\xef\xbb\xbf'): fail('CSV须为UTF-8 BOM编码', 'CSV_INVALID')
    try:
        table = list(csv.reader(StringIO(content.decode('utf-8-sig'), newline=''), strict=True))
    except (UnicodeError, csv.Error): fail('CSV字节或引号结构无效', 'CSV_INVALID')
    headers = tuple(table[0]) if table else ()
    if headers not in (HEADERS, SEMANTIC_HEADERS, TEXT_HEADERS) or any(len(row) != len(headers) for row in table[1:]): fail('CSV表头或列数不符', 'CSV_INVALID')
    rows = [dict(zip(headers, row)) for row in table[1:]]
    # Reuse value checks without interpreting the explicit safe-copy apostrophe.
    encode_csv(rows, headers=headers)
    return rows
