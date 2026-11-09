"""Pure bounded table parsing and explicit, versionable quality decisions."""
import csv
import json
import re
from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import PurePath
from openpyxl import load_workbook
from app.errors import AppError, register_error_messages, chinese_message
from app.imports.documents import _zip_limit
from app.finance.domain import validate_fact, FinanceValidationError

MAX_ROWS = 5000
register_error_messages({
    "SCHEMA_REQUIRED": "请先声明字段结构及导入映射", "REQUIRED": "必填字段不能为空", "MAX_LENGTH": "字段内容超过允许长度",
    "TYPE_INTEGER": "请输入有效整数", "TYPE_DECIMAL": "请输入普通十进制金额文本，最多38位整数和18位小数",
    "TYPE_BOOLEAN": "请输入true或false", "TYPE_DATE": "请输入YYYY-MM-DD格式日期",
    "FORMAT_EMAIL": "邮箱格式无效", "FORMAT_CODE": "编码只能包含字母、数字、下划线或连字符", "FORMAT_DATE": "日期格式无效",
    "DUPLICATE_FIELD": "此字段要求唯一，发现重复值", "DUPLICATE_ROW": "发现重复数据行", "MAPPING_REQUIRED": "请映射到存在的来源列",
    "FORMULA_NOT_ALLOWED": "不接受公式或错误单元格，请提供明确数值", "FINANCE_RULE": "财务字段未通过币种、单位、口径或金额校验",
})

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate JSON field")
        result[key] = value
    return result

FINANCE_FIELDS = [{"name": k, "type": "decimal" if k == "amount" else "string", "required": True, "unique": False,
                   "format": None, "max_length": 120} for k in ("entity", "period", "metric_code", "amount", "currency", "unit", "scope")]

def parse_table(filename, content, sheet_name=None):
    suffix = PurePath(filename).suffix.lower()
    records, columns, location = [], [], ""
    if not content or len(content) > 5*1024*1024:
        raise AppError("VALIDATION_ERROR", "数据源须非空且不超过5MB")
    try:
        if suffix == ".csv":
            reader = csv.reader(StringIO(content.decode("utf-8-sig")))
            columns = next(reader)
            for number, cells in enumerate(reader, 2):
                if number > MAX_ROWS+1: raise AppError("DATASET_LIMIT", "最多5000数据行，不截断")
                if len(cells) != len(columns): raise AppError("VALIDATION_ERROR", f"第{number}行列数不一致")
                records.append({"row": number, "values": dict(zip(columns, cells)), "locator": f"CSV!{number}", "formula_columns": []})
            location = "CSV"
        elif suffix == ".json":
            rows = json.loads(content.decode("utf-8-sig"), parse_int=str, parse_float=str,
                              object_pairs_hook=unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
            if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS or any(not isinstance(r, dict) for r in rows):
                raise AppError("VALIDATION_ERROR", "JSON必须是1..5000个扁平行对象的数组")
            columns = list(dict.fromkeys(k for row in rows for k in row))
            for number, row in enumerate(rows, 2):
                if any(isinstance(v, (dict, list)) for v in row.values()):
                    raise AppError("VALIDATION_ERROR", "首版数据集不展开嵌套JSON，请提供扁平字段")
                records.append({"row": number, "values": {c: row.get(c) for c in columns}, "locator": f"JSON!{number-2}", "formula_columns": []})
            location = "JSON"
        elif suffix == ".xlsx":
            _zip_limit(content)
            wb = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
            try:
                if sheet_name is None and len(wb.sheetnames) != 1:
                    raise AppError("SHEET_REQUIRED", "多工作表文件须显式选择sheet_name", details={"sheets": wb.sheetnames})
                if sheet_name is not None and sheet_name not in wb.sheetnames:
                    raise AppError("VALIDATION_ERROR", "指定工作表不存在")
                ws = wb[sheet_name] if sheet_name else wb.worksheets[0]
                ws.reset_dimensions()
                iterator = ws.iter_rows()
                header = next(iterator, ())
                columns = [c.value for c in header]
                location = ws.title
                for number, cells in enumerate(iterator, 2):
                    if number > MAX_ROWS+1: raise AppError("DATASET_LIMIT", "最多5000数据行，不截断")
                    if not any(c.value is not None for c in cells): continue
                    if len(cells) > len(columns) and any(c.value is not None for c in cells[len(columns):]):
                        raise AppError("VALIDATION_ERROR", f"第{number}行超出已声明表头")
                    values = {col: cells[i].value if i < len(cells) else None for i, col in enumerate(columns)}
                    # Numeric cells already have Excel's precision; exact high precision must be text.
                    values = {k: str(v) if v is not None and not isinstance(v, bool) else v for k, v in values.items()}
                    records.append({"row": number, "values": values, "locator": f"{ws.title}!{number}",
                                    "formula_columns": [col for i, col in enumerate(columns) if i < len(cells) and cells[i].data_type in ("f", "e")]})
            finally:
                wb.close()
        else:
            raise AppError("VALIDATION_ERROR", "通用数据源仅支持UTF-8 CSV、XLSX、扁平JSON")
    except AppError:
        raise
    except Exception as exc:
        raise AppError("DATASET_INVALID", "无法读取有效的表格数据源") from exc
    if not columns or len(columns) > 100 or any(not isinstance(c, str) or not c or len(c) > 120 for c in columns) or len(set(columns)) != len(columns):
        raise AppError("VALIDATION_ERROR", "表头须为1..100个不重复的非空字段名")
    if not records: raise AppError("VALIDATION_ERROR", "数据源没有数据行")
    if sum(len(str(v)) for r in records for v in r["values"].values()) > 500000:
        raise AppError("DATASET_LIMIT", "表格内容超过500000字符，不截断")
    return {"columns": columns, "source_rows": records, "sheet_name": location}

def validate_definition(fields, mapping, columns, cleaning):
    names = [f["name"] for f in fields]
    if len(set(names)) != len(names): raise AppError("VALIDATION_ERROR", "Schema字段名重复")
    if set(mapping) - set(names) or set(mapping.values()) - set(columns):
        raise AppError("VALIDATION_ERROR", "手动映射引用未声明字段或不存在的源列")
    if any(rule["field"] not in names for rule in cleaning):
        raise AppError("VALIDATION_ERROR", "清洗规则须引用声明字段")

def typed(value, field):
    if field.get("required") and isinstance(value, str) and not value.strip(): raise ValueError("REQUIRED")
    if value is None or value == "":
        if field.get("required"): raise ValueError("REQUIRED")
        return None
    raw = str(value)
    if len(raw) > field.get("max_length", 2000): raise ValueError("MAX_LENGTH")
    kind = field["type"]
    if kind == "string": result = raw
    elif kind == "integer":
        if not re.fullmatch(r"-?\d{1,18}", raw): raise ValueError("TYPE_INTEGER")
        result = int(raw)
    elif kind == "decimal":
        if not re.fullmatch(r"-?\d{1,38}(\.\d{1,18})?", raw): raise ValueError("TYPE_DECIMAL")
        result = format(Decimal(raw), "f")
    elif kind == "boolean":
        if isinstance(value, bool): result = value
        elif raw in ("true", "false"): result = raw == "true"
        else: raise ValueError("TYPE_BOOLEAN")
    else:
        try: result = date.fromisoformat(raw).isoformat()
        except ValueError: raise ValueError("TYPE_DATE")
        if result != raw: raise ValueError("TYPE_DATE")
    fmt = field.get("format")
    if fmt == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw): raise ValueError("FORMAT_EMAIL")
    if fmt == "code" and not re.fullmatch(r"[A-Za-z0-9_-]+", raw): raise ValueError("FORMAT_CODE")
    if fmt == "iso_date":
        try:
            if date.fromisoformat(raw).isoformat() != raw: raise ValueError()
        except ValueError: raise ValueError("FORMAT_DATE")
    return result

def assess(*, source_rows, fields, mapping, quality_rules, cleaning=(), dataset_type="generic"):
    rows, errors, unique, whole = [], [], {}, {}
    if not fields:
        return {"rows": [], "errors": [{"row": 1, "field": None, "cell": None, "code": "SCHEMA_REQUIRED", "message": "先声明字段Schema及手动映射"}],
                "quality": {"row_count": len(source_rows), "valid_row_count": 0, "error_count": 1}, "status": "invalid"}
    columns = list(source_rows[0]["values"])
    validate_definition(fields, mapping, columns, cleaning)
    for source in source_rows:
        row, start_errors = {}, len(errors)
        def err(field, code):
            name = field["name"]
            origin = mapping.get(name, name)
            errors.append({"row": source["row"], "field": name, "source_field": origin, "cell": f"{source['locator']}:{origin}",
                           "code": code, "message": chinese_message(code)})
        for field in fields:
            name = field["name"]
            col = mapping.get(name, name)
            if col not in source["values"]:
                err(field, "MAPPING_REQUIRED")
                continue
            if col in source.get("formula_columns", []):
                err(field, "FORMULA_NOT_ALLOWED")
                continue
            value = source["values"].get(col)
            for rule in cleaning:
                if rule["field"] == name and isinstance(value, str):
                    value = value.strip() if rule["operation"] == "trim" else value.upper() if rule["operation"] == "uppercase" else value.lower()
            try:
                row[name] = typed(value, field)
            except ValueError as exc:
                err(field, str(exc))
                continue
            if field.get("unique") and row[name] is not None:
                key = (name, json.dumps(row[name], sort_keys=True))
                if key in unique: err(field, "DUPLICATE_FIELD")
                else: unique[key] = source["row"]
        if len(errors) == start_errors:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if quality_rules.get("unique_rows", True) and key in whole:
                err(fields[0], "DUPLICATE_ROW")
            whole[key] = source["row"]
            if dataset_type == "finance":
                try: validate_fact({**row, "period": str(row.get("period")), "source_ref": "SYNTHETIC:governance:preview", "synthetic": True})
                except FinanceValidationError: err(fields[0], "FINANCE_RULE")
        rows.append({"row": source["row"], "values": row, "source_locator": source["locator"]})
    bad_rows = {e["row"] for e in errors}
    return {"rows": rows, "errors": errors, "quality": {"row_count": len(source_rows), "valid_row_count": len(source_rows)-len(bad_rows),
                                                       "error_count": len(errors)}, "status": "invalid" if errors else "validated"}

def corrected_rows(rows, corrections):
    revised = deepcopy(rows)
    index = {r["row"]: r for r in revised}
    seen = set()
    for change in corrections:
        key = (change["row"], change["column"])
        if key in seen or change["row"] not in index or change["column"] not in index[change["row"]]["values"]:
            raise AppError("VALIDATION_ERROR", "修订坐标不存在或重复")
        seen.add(key)
        target = index[change["row"]]
        target["values"][change["column"]] = change["value"]
        target["formula_columns"] = [c for c in target.get("formula_columns", []) if c != change["column"]]
    return revised
