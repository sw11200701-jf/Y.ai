"""Conservative Chinese keyword matching for approved published chunks."""
from decimal import Decimal
import re
import unicodedata
from app.imports.documents import _terms

_ALIASES = {
    "酒店": "住宿", "宾馆": "住宿", "住店": "住宿",
    "坐飞机": "飞行", "乘飞机": "飞行", "搭飞机": "飞行", "乘机": "飞行", "航班": "飞行",
    "一晚": "每晚", "一夜": "每晚", "最多": "上限",
}
NO_KNOWLEDGE_MATCH = "在当前可用的已发布知识中没有找到相关内容"
_ANCHORS = ("住宿", "飞行")
_QUESTION_FILLER = re.compile(r"请问|能报多少钱|可以报销多少|能报销多少|报销多少|多少钱|是多少|怎么样|怎么|如何|是否|可以|能否|的规定|有什么规定|什么规定")
_ALIAS_PATTERN = re.compile("|".join(map(re.escape, _ALIASES)))


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def canonical(text):
    return _ALIAS_PATTERN.sub(lambda match: _ALIASES[match[0]], text)


def prepare_query(text):
    normalized = normalize(text)
    content = canonical(_QUESTION_FILLER.sub(" ", normalized))
    return normalized, content, _terms(content), {term for term in _ANCHORS if term in content}


def match_chunk(query, text):
    normalized, content, terms, anchors = query
    raw = normalize(text)
    canonical_text = canonical(raw)
    tokens = _terms(canonical_text)
    matched = sorted(terms.keys() & tokens.keys())
    exact = len(normalized) >= 2 and normalized in raw
    anchor_matches = anchors.intersection(matched)
    # Specific anchors disambiguate travel topics. Generic overlap alone is
    # insufficient for long questions, while exact short searches stay usable.
    if not matched or (not exact and (anchors and not anchor_matches
            or len(terms) > 1 and len(matched) == 1 and not anchor_matches)):
        return None
    score = Decimal(len(matched)) * 3 / Decimal(len(terms))
    score += Decimal(sum(min(tokens[term], 3) for term in matched)) / 100
    score += Decimal(len(anchor_matches))
    if exact:
        score += 2
    elif len(content) >= 2 and content in canonical_text:
        score += 1
    return matched, format(score.quantize(Decimal("0.000001")), "f")
