from decimal import Decimal
from pathlib import Path
from io import BytesIO
import json
from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.drawing.image import Image as ExcelImage
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def display(value, ratio=False):
    if value is None:
        return "不可用"
    dec = Decimal(value)
    return f"{dec * 100:.2f}%" if ratio else f"{dec:,.2f}"

def selected_rows(dataset):
    return [r for r in dataset.rows if not r["metric_code"].endswith("_yoy")]

def chart_png(dataset, path: Path):
    image = Image.new("RGB", (1200, 640), "white")
    draw = ImageDraw.Draw(image)
    def font(size):
        for filename in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf"):
            if Path(filename).exists():
                return ImageFont.truetype(filename, size)
        return ImageFont.load_default(size=size)
    large, small = font(30), font(21)
    draw.text((55, 20), "合成数据  营业收入与净利润趋势", fill="#17365D", font=large)
    draw.text((55, 65), "SYNTHETIC DATA  金额 CNY yuan / 元" if dataset.query.currency == "CNY" else f"SYNTHETIC DATA  {dataset.query.currency} yuan", fill="#566879", font=small)
    periods = sorted(dataset.query.periods)
    series = [r for r in dataset.rows if r["metric_code"] in ("revenue", "net_profit")]
    amounts = [Decimal(v) for r in series for v in r["values"].values() if v is not None]
    high = max([Decimal(1)] + amounts)
    low = min([Decimal(0)] + amounts)
    span = high - low
    x0, x1, top, bottom = 150, 1120, 135, 505
    def y(value):
        return bottom - int((value - low) / span * (bottom-top))
    for i in range(5):
        value = low + span * Decimal(i) / 4
        yy = y(value)
        draw.line((x0, yy, x1, yy), fill="#E2E8F0", width=2)
        draw.text((10, yy-12), f"{value:,.0f}", fill="#566879", font=small)
    draw.line((x0, y(Decimal(0)), x1, y(Decimal(0))), fill="#8998A7", width=2)
    colors = ("#1F5B8F", "#008578")
    group = (x1-x0)/max(len(periods), 1)
    width = min(60, int(group/4))
    for index, period in enumerate(periods):
        center = int(x0 + group*(index+.5))
        draw.text((center-25, bottom+20), period, fill="#233446", font=small)
        for s, row in enumerate(series):
            raw = row["values"].get(period)
            x = center + (s*2-1)*(width//2+6)
            if raw is None:
                draw.text((x-width//2, top+40+s*32), "缺失", fill="#B45309", font=small)
                continue
            value = Decimal(raw)
            yy, baseline = y(value), y(Decimal(0))
            draw.rectangle((x-width//2, min(yy, baseline), x+width//2, max(yy, baseline)), fill=colors[s])
            label = f"{value:,.0f}"
            bbox = draw.textbbox((0, 0), label, font=small)
            label_x = x + width//2 + 8 if s == 1 else x-(bbox[2]-bbox[0])/2
            draw.text((label_x, yy-32 if value >= 0 else yy+8), label, fill=colors[s], font=small)
    for index, row in enumerate(series):
        x = 350 + index*300
        draw.rectangle((x, 582, x+24, 606), fill=colors[index])
        draw.text((x+38, 578), row["label"], fill="#233446", font=small)
    provenance = PngInfo()
    for key, value in {"snapshot_id": dataset.snapshot_id, "dataset_id": dataset.id,
                       "metric_versions": getattr(dataset, "metric_versions", {"all": dataset.metric_version}),
                       "source_refs": dataset.source_refs, "synthetic": dataset.synthetic}.items():
        provenance.add_text(key, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    image.save(path, pnginfo=provenance)

def text(cell, value):
    # Explicit string type prevents entity/source fields becoming spreadsheet formulas.
    cell.value = str(value)
    cell.data_type = "s"

def numeric(cell, value, ratio=False):
    if value is None:
        text(cell, "不可用")
    elif len(str(value).replace("-", "").replace(".", "").lstrip("0")) > 15:
        text(cell, value)
    else:
        cell.value = Decimal(value)
        cell.number_format = '0.00%' if ratio else '#,##0.00;(#,##0.00);"-"'

def excel_report(dataset, chart: Path, path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "年度分析"
    text(ws["A1"], "年度财务分析 合成数据 SYNTHETIC DATA")
    text(ws["A2"], f"{dataset.query.entity}  {dataset.query.currency}  {dataset.query.unit}  {dataset.query.scope}")
    text(ws["A3"], "snapshot_id: " + dataset.snapshot_id)
    text(ws["A4"], "指标版本 " + dataset.metric_version + "  比率为Decimal快照结果 8位小数")
    periods = sorted(dataset.query.periods)
    headers = ["指标", *periods, "单位", "不可用原因"]
    for col, header in enumerate(headers, 1):
        text(ws.cell(6, col), header)
    for rownum, row in enumerate(dataset.rows, 7):
        text(ws.cell(rownum, 1), row["label"])
        for col, period in enumerate(periods, 2):
            numeric(ws.cell(rownum, col), row["values"].get(period), row["unit"] == "ratio")
        text(ws.cell(rownum, len(periods)+2), "%" if row["unit"] == "ratio" else dataset.query.currency + " yuan")
        text(ws.cell(rownum, len(periods)+3), "; ".join(f"{p} {reason}" for p, reason in row["reasons"].items()))
    ws.column_dimensions["A"].width = 28
    for col in range(2, len(periods)+2):
        ws.column_dimensions[chr(64+col)].width = 22
    ws.column_dimensions[chr(64+len(periods)+2)].width = 18
    ws.column_dimensions[chr(64+len(periods)+3)].width = 48
    ws.freeze_panes = "B7"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(5, len(headers)))
    ws["A1"].font = Font(size=17, bold=True, color="17365D")
    ws.row_dimensions[1].height = 30
    img = ExcelImage(str(chart))
    img.width, img.height = 900, 480
    ws.add_image(img, f"A{len(dataset.rows)+10}")
    facts = wb.create_sheet("来源底表")
    fields = ["entity", "period", "metric_code", "amount", "currency", "unit", "scope", "source_ref", "synthetic"]
    facts.append(fields)
    for rownum, f in enumerate(dataset.facts, 2):
        data = f.model_dump(mode="json")
        for col, field in enumerate(fields, 1):
            # Keep authoritative amounts as exact decimal text in source sheet.
            text(facts.cell(rownum, col), data[field])
    for col, width in zip("ABCDEFGHI", (28, 12, 26, 26, 12, 12, 20, 100, 12)):
        facts.column_dimensions[col].width = width
    facts.freeze_panes = "A2"
    facts.auto_filter.ref = facts.dimensions
    for row in range(2, facts.max_row + 1):
        source_length = len(str(facts.cell(row, 8).value or ""))
        facts.row_dimensions[row].height = 16 * max(1, (source_length + 99) // 100)
    policy = wb.create_sheet("计算口径")
    for line in ["合成数据 SYNTHETIC DATA 固定年度分析模板", "snapshot_id: " + dataset.snapshot_id,
                 "指标版本 " + dataset.metric_version, "同比 = (本年 - 上年) / abs(上年)，只比较相邻日历年。",
                 "负基数使用绝对值，仅为Demo规则，尚未由真实客户确认；零分母不可用 ZERO_DENOMINATOR；缺失 MISSING_FACT。",
                 "净利率 = 净利润 / 营业收入；资产负债率 = 负债 / 资产。",
                 "金额不变，比率按ROUND_HALF_UP保留8位小数。显示百分比保留2位。",
                 "Excel为已验证快照，不在Excel重新计算财务数值。底表金额文本保留精度。",
                 "固定年度分析报告，不是完整法定年报。"]:
        policy.append([line])
    policy.column_dimensions["A"].width = 120
    for sheet, header_row in ((ws, 6), (facts, 1)):
        for cell in sheet[header_row]:
            cell.fill = PatternFill("solid", fgColor="17365D")
            cell.font = Font(color="FFFFFF", bold=True)
        for row in sheet.iter_rows(min_row=header_row+1):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    for sheet in wb:
        sheet.sheet_view.showGridLines = False
    wb.save(path)

def word_report(dataset, chart: Path, path: Path):
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.65)
    section.left_margin = section.right_margin = Inches(.7)
    for name in ("Normal", "Title", "Heading 1", "Heading 2"):
        style = doc.styles[name]
        style.font.name = "Microsoft YaHei"
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.color.rgb = RGBColor.from_string("000000")
    doc.styles["Normal"].font.size = Pt(9)
    doc.styles["Normal"].paragraph_format.space_after = Pt(5)
    doc.styles["Title"].font.size = Pt(22)
    doc.add_paragraph("年度财务分析报告", "Title")
    doc.add_paragraph("合成数据 SYNTHETIC DATA", "Subtitle")
    doc.add_paragraph(f"主体 {dataset.query.entity}；年度 {' / '.join(sorted(dataset.query.periods))}；币种 {dataset.query.currency}；单位 元；口径 {dataset.query.scope}。")
    lookup = {r["metric_code"]: r for r in dataset.rows}
    last = max(dataset.query.periods)
    def growth(code):
        row = lookup.get(code + "_yoy")
        if not row:
            return "不可用 MISSING_FACT"
        value = row["values"].get(last)
        return display(value, True) if value is not None else "不可用 " + row["reasons"].get(last, "MISSING_FACT")
    doc.add_paragraph(f"{last}年营业收入同比 {growth('revenue')}，净利润同比 {growth('net_profit')}。下表和趋势图使用同一数据快照，缺失值保留为不可用。")
    doc.add_heading("财务指标", level=1)
    periods = sorted(dataset.query.periods)
    table = doc.add_table(rows=1, cols=len(periods)+1)
    table.style = "Light Shading Accent 1"
    for cell, value in zip(table.rows[0].cells, ["指标", *periods]):
        cell.text = value
    for row in selected_rows(dataset):
        cells = table.add_row().cells
        cells[0].text = row["label"]
        for i, period in enumerate(periods, 1):
            cells[i].text = display(row["values"].get(period), row["unit"] == "ratio")
    doc.add_picture(str(chart), width=Inches(6.8))
    doc.add_paragraph("图表金额与表格及来源底表一致；缺失期间显示缺失，不补零。")
    doc.add_page_break()
    doc.add_heading("计算口径与来源", level=1)
    doc.add_paragraph("同比 = (本年 - 上年) / abs(上年)，只比较相邻日历年。负基数同比按上年绝对值计算。净利率 = 净利润 / 营业收入；资产负债率 = 负债 / 资产。金额保留原始精度，比率以 Decimal 计算并按 ROUND_HALF_UP 保留8位小数，展示百分比保留2位。")
    doc.add_paragraph("固定年度分析模板，不是完整法定年报。所有数据显著标记合成。负基数同比仅为Demo公式规则，尚未由真实客户确认。")
    doc.add_paragraph("snapshot_id " + dataset.snapshot_id)
    doc.add_paragraph("指标版本 " + dataset.metric_version)
    doc.add_paragraph("dataset_id " + dataset.id)
    if dataset.warnings:
        doc.add_heading("不可用原因", level=2)
        for warning in dataset.warnings:
            doc.add_paragraph(f"{warning['period']} {warning['metric_code']} {warning['code']}")
    doc.add_heading("来源追溯", level=2)
    for f in dataset.facts:
        p = doc.add_paragraph(f"{f.period} {f.metric_code}  {f.amount} {f.currency} {f.unit}\n{f.source_ref}")
        p.paragraph_format.keep_together = True
        for run in p.runs:
            run.font.size = Pt(8)
    doc.core_properties.title = "年度财务分析报告 合成数据"
    doc.core_properties.author = "Finance Agent Demo"
    doc.save(path)
