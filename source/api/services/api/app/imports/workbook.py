"""Standard XLSX template and bounded cell-level parser."""
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from app.finance.domain import validate_fact, fact_key, FinanceValidationError, METRICS

COLUMNS = ("entity", "period", "metric_code", "amount", "currency", "unit", "scope")
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000

def template_bytes(rows=()) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "FinancialFacts"
    ws.append(COLUMNS)
    for row in rows:
        ws.append([row.get(k) for k in COLUMNS])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:G{max(2, ws.max_row)}"
    for c in ws[1]:
        c.fill = PatternFill("solid", fgColor="17365D")
        c.font = Font(color="FFFFFF", bold=True)
    for column, width in zip("ABCDEFG", (28, 14, 28, 26, 14, 14, 20)):
        ws.column_dimensions[column].width = width
    ws.column_dimensions["D"].number_format = "@"
    # Amount input is text to preserve cents beyond Excel's 15 digit numeric precision.
    for row in ws.iter_rows(min_row=2):
        row[3].number_format = "@"
        row[3].font = Font(color="0000FF")
    for col, formula in (("E", '"CNY,USD,EUR,HKD"'), ("F", '"yuan"'), ("G", '"consolidated,standalone"')):
        dv = DataValidation(type="list", formula1=formula, allow_blank=False)
        dv.errorTitle = "Invalid value"
        dv.error = "Select a supported value"
        dv.showErrorMessage = True
        ws.add_data_validation(dv)
        dv.add(f"{col}2:{col}{MAX_ROWS+1}")
    note = wb.create_sheet("Instructions")
    lines = ["合成数据 SYNTHETIC DATA 财务导入模板", "请在 FinancialFacts 按标准列录入，不修改表头。",
             "entity 为主体；period 为2000至2099年度。", "metric_code: " + ", ".join(METRICS),
             "amount 为十进制金额，最多两位小数；高精度金额请用文本单元格。",
             "currency: CNY USD EUR HKD；unit: yuan；scope: consolidated standalone。",
             "金额空白视为错误；缺失指标请不提供该行。零金额填写0。",
             "不允许公式、重复业务键、混币种、混口径。所有样例为合成，不能作为客户数据。"]
    for text in lines:
        note.append([text])
    note.column_dimensions["A"].width = 110
    for row in note:
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    note["A1"].font = Font(bold=True, size=15, color="17365D")
    for sheet in wb:
        sheet.sheet_view.showGridLines = False
    out = BytesIO()
    wb.save(out)
    return out.getvalue()

def parse_workbook(content: bytes, digest: str) -> tuple[list[dict], list[dict], int]:
    if len(content) > MAX_BYTES:
        return [], [{"code": "FILE_TOO_LARGE", "message": "文件最大5MB", "row": None, "cell": None}], 0
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024 or len(archive.infolist()) > 2000:
                raise ValueError("XLSX 解压大小或成员数超限")
        wb = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except Exception:
        return [], [{"code": "INVALID_XLSX", "message": "无法读取有效XLSX文件", "row": None, "cell": None}], 0
    facts, errors, seen, count = [], [], {}, 0
    try:
        if "FinancialFacts" not in wb.sheetnames:
            return [], [{"code": "INVALID_SHEET", "message": "缺少 FinancialFacts 工作表", "row": 1, "cell": "A1"}], 0
        ws = wb["FinancialFacts"]
        ws.reset_dimensions()  # do not trust attacker-provided sheet dimensions
        iterator = ws.iter_rows()
        header = next(iterator, ())
        if tuple(c.value for c in header) != COLUMNS:
            return [], [{"code": "INVALID_HEADER", "message": "表头必须与标准七列一致", "row": 1, "cell": "A1:G1"}], 0
        for row_number, cells in enumerate(iterator, 2):
            if row_number > MAX_ROWS + 1:
                errors.append({"code": "TOO_MANY_ROWS", "message": "数据不得超过10000行", "row": row_number, "cell": f"A{row_number}"})
                break
            values = [c.value for c in cells]
            if not any(v is not None and v != "" for v in values):
                continue
            count += 1
            start_errors = len(errors)
            def error(column, message, code="INVALID_CELL"):
                errors.append({"code": code, "message": message, "sheet": "FinancialFacts", "row": row_number,
                               "column": COLUMNS[column], "field": COLUMNS[column], "cell": f"{chr(65+column)}{row_number}"})
            if len(values) > 7 and any(v is not None for v in values[7:]):
                error(0, "标准七列以外包含数据")
            values = (values + [None] * 7)[:7]
            for i, val in enumerate(values):
                if i < len(cells) and cells[i].data_type in ("f", "e"):
                    error(i, "不支持公式或Excel错误单元格")
                elif val is None or val == "":
                    error(i, "必填单元格为空", "MISSING_CELL")
            if len(errors) > start_errors:
                continue
            raw = {k: str(v).strip() if k != "amount" else v for k, v in zip(COLUMNS, values)}
            raw.update(source_ref=f"SYNTHETIC:import:{digest}:FinancialFacts!D{row_number}", synthetic=True)
            # Validate each field with known-valid defaults to locate every invalid cell.
            good = dict(entity="SYNTHETIC-DEMO", period="2024", metric_code="revenue", amount="1", currency="CNY",
                        unit="yuan", scope="consolidated", source_ref=raw["source_ref"], synthetic=True)
            for i, k in enumerate(COLUMNS):
                try:
                    validate_fact({**good, k: raw[k]})
                except FinanceValidationError as exc:
                    error(i, str(exc))
            if len(errors) > start_errors:
                continue
            fact = validate_fact(raw)
            key = fact_key(fact)
            if key in seen:
                error(2, f"与第{seen[key]}行业务键重复", "DUPLICATE_FACT")
            else:
                seen[key] = row_number
                facts.append(fact)
        if count == 0:
            errors.append({"code": "EMPTY_BATCH", "message": "工作表没有数据行", "row": 2, "cell": "A2"})
        per_entity = {}
        for fact in facts:
            baseline = per_entity.setdefault(fact["entity"], fact)
            for field, column in (("currency", "E"), ("unit", "F"), ("scope", "G")):
                if baseline[field] != fact[field]:
                    rownum = int(fact["source_ref"].rsplit("D", 1)[1])
                    errors.append({"code": "FINANCIAL_CONTEXT_MISMATCH", "message": "同一主体不得混用币种、单位或单体合并口径",
                                   "row": rownum, "column": field, "field": field, "cell": f"{column}{rownum}", "sheet": "FinancialFacts"})
        return facts, errors, count
    finally:
        wb.close()
