"""Deterministic annual finance rules. Never use binary floats for arithmetic."""
import hashlib
import json
import re
from decimal import Decimal, localcontext, ROUND_HALF_UP

METRICS = {
    "revenue": "营业收入", "net_profit": "净利润",
    "operating_cash_flow": "经营现金流", "total_assets": "资产总额",
    "total_liabilities": "负债总额",
}
VERSION = "1.0.0"
MAX_AMOUNT = Decimal("999999999999999999.99")

class FinanceValidationError(ValueError):
    pass

def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result

def parse_amount(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise FinanceValidationError("金额必须是有限十进制数")
    raw = str(value).strip()
    if not re.fullmatch(r"-?\d+(\.\d{1,2})?", raw):
        raise FinanceValidationError("金额必须为普通十进制数，最多两位小数，不支持公式或科学计数法")
    amount = Decimal(raw)
    if abs(amount) > MAX_AMOUNT:
        raise FinanceValidationError("金额超过18位整数及2位小数限制")
    # Excel numeric cells cannot safely retain more than 15 significant digits.
    if isinstance(value, (float, int)) and len(raw.replace("-", "").replace(".", "").lstrip("0")) > 15:
        raise FinanceValidationError("高精度金额请以文本单元格提交，避免Excel数值精度损失")
    return decimal_text(amount)

def validate_fact(fact: dict) -> dict:
    result = dict(fact)
    for field in ("entity", "period", "metric_code", "currency", "unit", "scope", "source_ref"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise FinanceValidationError(f"{field} 必填")
        result[field] = result[field].strip()
    if len(result["entity"]) > 120 or len(result["source_ref"]) > 1000:
        raise FinanceValidationError("主体或来源字段过长")
    if not re.fullmatch(r"20\d{2}", result["period"]):
        raise FinanceValidationError("period 必须为2000至2099年度")
    if result["metric_code"] not in METRICS:
        raise FinanceValidationError("未知 metric_code")
    if result["currency"] not in ("CNY", "USD", "EUR", "HKD"):
        raise FinanceValidationError("不支持的 currency")
    if result["unit"] != "yuan":
        raise FinanceValidationError("unit 必须为 yuan，不自动换算万元或千元")
    if result["scope"] not in ("consolidated", "standalone"):
        raise FinanceValidationError("scope 必须为 consolidated 或 standalone")
    result["amount"] = parse_amount(result.get("amount"))
    return result

def fact_key(fact: dict) -> tuple:
    return tuple(fact[k] for k in ("entity", "period", "metric_code", "currency", "unit", "scope"))

def ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return None, "MISSING_FACT"
    if denominator == 0:
        return None, "ZERO_DENOMINATOR"
    with localcontext() as ctx:
        ctx.prec = 50
        return decimal_text((numerator / denominator).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)), None

def build_result(facts: list[dict], query: dict) -> dict:
    periods = query["periods"]
    if not periods or len(periods) > 10 or len(set(periods)) != len(periods):
        raise FinanceValidationError("期间须为1至10个不重复年度")
    if any(not re.fullmatch(r"20\d{2}", p) for p in periods):
        raise FinanceValidationError("仅支持2000至2099年度")
    if (not query.get("metric_codes") or set(query["metric_codes"]) - METRICS.keys()
            or len(set(query["metric_codes"])) != len(query["metric_codes"])):
        raise FinanceValidationError("metric_codes 必须包含支持的基础指标")
    periods = sorted(periods)
    normalized, indexed = [], {}
    for original in facts:
        f = validate_fact(original)
        if any(f[k] != query[k] for k in ("entity", "currency", "unit", "scope")):
            raise FinanceValidationError("FINANCIAL_CONTEXT_MISMATCH: 主体、币种、单位或合并口径不一致")
        if f["period"] not in periods:
            raise FinanceValidationError("FINANCIAL_CONTEXT_MISMATCH: 事实期间超出查询")
        key = (f["period"], f["metric_code"])
        if key in indexed:
            raise FinanceValidationError("CONFLICT: 重复财务事实，不自动求和或覆盖")
        indexed[key] = f
        normalized.append(f)
    rows, warnings = [], []
    def value(period, code):
        f = indexed.get((period, code))
        return Decimal(f["amount"]) if f else None
    def add(code, label, unit, values, reasons, refs):
        row = {"metric_code": code, "label": label, "unit": unit,
               "values": values, "reasons": reasons, "source_refs": sorted(set(refs)), **values}
        rows.append(row)
        warnings.extend({"code": reason, "period": period, "metric_code": code}
                        for period, reason in reasons.items() if reason)
    for code in query["metric_codes"]:
        vals = {p: indexed.get((p, code), {}).get("amount") for p in periods}
        refs = [f["source_ref"] for f in normalized if f["metric_code"] == code]
        add(code, METRICS[code], query["unit"], vals,
            {p: "MISSING_FACT" for p, v in vals.items() if v is None}, refs)
        growth, reasons = {}, {}
        for p in periods:
            prior = str(int(p) - 1)
            current, base = value(p, code), value(prior, code)
            numerator = current - base if current is not None and base is not None else None
            growth[p], reason = ratio(numerator, abs(base) if base is not None else None)
            if reason:
                reasons[p] = reason
        add(code + "_yoy", METRICS[code] + "同比", "ratio", growth, reasons, refs)
    for code, label, num, den in (("net_profit_margin", "净利率", "net_profit", "revenue"),
                                  ("debt_to_assets", "资产负债率", "total_liabilities", "total_assets")):
        vals, reasons = {}, {}
        for p in periods:
            vals[p], reason = ratio(value(p, num), value(p, den))
            if reason:
                reasons[p] = reason
        add(code, label, "ratio", vals, reasons,
            [f["source_ref"] for f in normalized if f["metric_code"] in (num, den)])
    canonical = {"facts": sorted(normalized, key=fact_key), "query": query, "version": VERSION}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"snapshot_id": "snapshot_" + digest, "metric_version": VERSION,
            "metric_versions": {r["metric_code"]: VERSION for r in rows},
            "columns": [{"key": "label", "label": "指标"}] + [{"key": p, "label": p} for p in periods],
            "rows": rows, "facts": normalized, "source_refs": sorted({f["source_ref"] for f in normalized}),
            "warnings": warnings, "synthetic": all(f.get("synthetic", True) for f in normalized),
            "formula_policy": {"yoy": "(current - prior) / abs(prior); prior is previous calendar year",
                               "net_profit_margin": "net_profit / revenue", "debt_to_assets": "total_liabilities / total_assets",
                               "rounding": "ratios 8 decimal places ROUND_HALF_UP; amounts unchanged",
                               "approval": "Demo rule only; not confirmed by a real customer"}}
