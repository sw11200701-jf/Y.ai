"""Small, strict content contracts for trusted general Office renderers."""
import json
import math
import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.errors import AppError

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)

Text = Annotated[str, Field(max_length=1200)]
Short = Annotated[str, Field(max_length=300)]

class WordTable(Strict):
    headers: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(min_length=1, max_length=6)
    rows: list[list[Short]] = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def rectangular(self):
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError('表格行列数量不一致')
        return self

class WordSection(Strict):
    heading: str = Field(default='', max_length=120)
    paragraphs: list[Text] = Field(default_factory=list, max_length=12)
    bullets: list[Short] = Field(default_factory=list, max_length=12)
    table: WordTable | None = None

    @model_validator(mode='after')
    def content(self):
        if not self.paragraphs and not self.bullets and self.table is None:
            raise ValueError('章节须有正文、列表或表格')
        return self

class WordInput(Strict):
    title: str = Field(min_length=1, max_length=160)
    subtitle: str = Field(default='', max_length=300)
    sections: list[WordSection] = Field(min_length=1, max_length=40)

class Column(Strict):
    name: str = Field(min_length=1, max_length=80)
    type: Literal['text', 'integer', 'number', 'boolean', 'date']

Cell = str | int | float | bool | None

class Sheet(Strict):
    name: str = Field(min_length=1, max_length=31)
    columns: list[Column] = Field(min_length=1, max_length=20)
    rows: list[list[Cell]] = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def typed_cells(self):
        if re.search(r'[\\/*?:\[\]]', self.name) or self.name.startswith("'") or self.name.endswith("'"):
            raise ValueError('工作表名称无效')
        if len({col.name for col in self.columns}) != len(self.columns):
            raise ValueError('列名不能重复')
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError('表格行列数量不一致')
            for column, value in zip(self.columns, row):
                if value is None:
                    continue
                kind = column.type
                if kind == 'text' and (type(value) is not str or len(value) > 1200):
                    raise ValueError('文本列只接受最多1200字符文本')
                if kind == 'boolean' and type(value) is not bool:
                    raise ValueError('布尔列须使用true或false')
                if kind == 'integer' and type(value) is not int:
                    raise ValueError('整数列须使用整数，不能使用布尔或文本')
                if kind in {'integer', 'number'}:
                    if type(value) not in {int, float} or (type(value) is float and not math.isfinite(value)):
                        raise ValueError('数值列只接受有限数值')
                    if len(Decimal(str(value)).as_tuple().digits) > 15 or (type(value) is int and len(str(abs(value))) > 15):
                        raise ValueError('Excel精确数值最多15位；更长编号请使用文本列')
                if kind == 'date':
                    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                        raise ValueError('日期列须为YYYY-MM-DD')
                    date.fromisoformat(value)
        return self

class ExcelInput(Strict):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=300)
    sheets: list[Sheet] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def unique_sheets(self):
        if len({sheet.name.casefold() for sheet in self.sheets}) != len(self.sheets):
            raise ValueError('工作表名称不能重复')
        if sum(len(sheet.rows)*len(sheet.columns) for sheet in self.sheets) > 10000:
            raise ValueError('整本工作簿最多10000个数据单元格')
        return self

def width(value):
    return sum(2 if unicodedata.east_asian_width(c) in {'W', 'F'} else 1 for c in value)

def lines(value, capacity):
    return sum(max(1, math.ceil(width(line)/capacity)) for line in value.split('\n'))

class SlideTable(Strict):
    headers: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(min_length=1, max_length=5)
    rows: list[list[Annotated[str, Field(max_length=120)]]] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def fits(self):
        capacity = max(10, int((1136/len(self.headers)-24)/11))
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError('表格行列数量不一致')
        if any(lines(cell, capacity) > 2 for row in [self.headers, *self.rows] for cell in row):
            raise ValueError('PPT表格单元格超过两行，请分为更多页')
        return self

class Slide(Strict):
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default='', max_length=180)
    bullets: list[Annotated[str, Field(min_length=1, max_length=180)]] = Field(default_factory=list, max_length=6)
    table: SlideTable | None = None

    @model_validator(mode='after')
    def fits(self):
        if not self.title.strip() or lines(self.title, 60) > 2 or (self.subtitle and lines(self.subtitle, 110) > 2):
            raise ValueError('PPT标题或副标题超出可读布局，请拆页')
        if bool(self.bullets) == bool(self.table):
            raise ValueError('PPT正文须选择列表或表格之一')
        if any(not value.strip() for value in self.bullets) or sum(lines(value, 84)+1 for value in self.bullets) > 12:
            raise ValueError('PPT正文超出可读布局，请拆为更多页')
        return self

class PowerPointInput(Strict):
    title: str = Field(min_length=1, max_length=160)
    slides: list[Slide] = Field(min_length=1, max_length=20)

TOOLS = {'document.word.v1': WordInput, 'document.excel.v1': ExcelInput, 'document.powerpoint.v1': PowerPointInput}

def validate_content(tool_id, arguments):
    try:
        if type(arguments) is not dict:
            raise ValueError()
        body = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(body) > 262144:
            raise ValueError()
        count = 0
        def inspect(value):
            nonlocal count
            if isinstance(value, str) and any((ord(c) < 32 and c not in '\n\t\r') or
                unicodedata.category(c) == 'Cs' or (ord(c) & 0xFFFF) in {0xFFFE, 0xFFFF} for c in value):
                raise ValueError()
            if isinstance(value, str):
                count += len(value)
                if count > 64000: raise ValueError()
            if isinstance(value, dict):
                for child in value.values(): inspect(child)
            if isinstance(value, list):
                for child in value: inspect(child)
        inspect(arguments)
        if tool_id not in TOOLS:
            raise AppError('DOCUMENT_TOOL_UNSUPPORTED', '文档生成工具或版本不受支持', 422)
        result = TOOLS[tool_id].model_validate(arguments)
        if not result.title.strip(): raise ValueError()
        return result
    except AppError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise AppError('DOCUMENT_INPUT_INVALID', '文档内容、数据类型或布局超过限制，请检查并拆分内容；不接受路径、URL、公式或脚本配置', 422) from None
