"""Portable recipe DTOs and source/evidence checks; no model calls or storage."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from jsonschema import Draft202012Validator
from referencing import Registry
from app.errors import AppError

ASSETS = Path(__file__).with_name('slicing_assets')
DEFAULTS = json.loads((ASSETS / 'prompts.json').read_text(encoding='utf-8'))
SCHEMAS = json.loads((ASSETS / 'schemas.json').read_text(encoding='utf-8'))
RECIPE = DEFAULTS['recipe_version']
STAGES = tuple(DEFAULTS['stages'])
MAX_ITEMS = 1000


def fail(message, code='SLICING_RESULT_INVALID',details=None):
    raise AppError(code, message, 422,details)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def validate(stage, value):
    if stage not in STAGES: fail('未知切片阶段')
    schema = SCHEMAS[DEFAULTS['stages'][stage]['schema_key']]
    try:
        # JSON roundtrip rejects non-finite values and non-JSON application objects.
        encoded(value)
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except Exception as exc:
        fail(f'{stage}回复不符合阶段结构：{getattr(exc, "message", "非有限JSON")}')
    return deepcopy(value)


def stage_settings(overrides=None):
    result = deepcopy(DEFAULTS['stages'])
    if overrides is None: return result
    if type(overrides) is not dict or set(overrides) != set(STAGES): fail('必须完整配置八个阶段')
    for stage, value in overrides.items():
        if type(value) is not dict or set(value) != {'system_prompt', 'user_prompt'}: fail('阶段只接受系统与用户提示词')
        system, user = value['system_prompt'], value['user_prompt']
        if not isinstance(system, str) or not system.strip() or len(system) > 16000: fail('阶段系统提示词须为1至16000字符')
        if not isinstance(user, str) or not user.strip() or len(user) > 16000: fail('阶段用户提示词须为1至16000字符')
        from app.agents.prompts import validate_local_templates
        validate_local_templates({'prompt_messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}, {'input_json'}, allow_system_inputs=False)
        if '{{input_json}}' not in user: fail('阶段用户提示词必须引用{{input_json}}')
        result[stage].update(value)
    return result


def unique(values, label):
    if len(values) != len(set(values)): fail(label + '不能重复')
    return set(values)


def item_occurrences(item):
    """Textual image evidence and original-file attachments are distinct roles."""
    return list(dict.fromkeys(item['occurrence_ids']+item.get('attachment_occurrence_ids',[])))


def bind_reference_incidence(plan,stage,data,value):
    """Derive redundant ID lists from actual references; never infer coverage.

    The model still decides every object's disposition and reason. A represented
    object with no actual supporting item remains invalid; all semantic audits
    and final source-accounting judgments are unchanged.
    """
    if plan.get('reference_incidence_contract')!='derived-reference-incidence-v1' or not isinstance(value,dict):return value
    items=value.get('items')
    if items is None and stage=='finalaudit' and data.get('audit_facet')=='coverage':items=data.get('items')
    if not isinstance(items,list) or not isinstance(value.get('coverage'),list):return value
    result=deepcopy(value)
    for row in result['coverage']:
        if not isinstance(row,dict):continue
        # An image's empty native-text object carries position, not words. Its
        # separate occurrence still must be attached or explicitly excluded.
        # Do not ask a model to manufacture text evidence for an empty object.
        if (plan.get('attachment_contract')=='original-image-attachments-v1' and row.get('object_type')=='source'
                and row.get('object_id') in plan['registry']['sources']
                and not plan['registry']['sources'][row['object_id']]['text'].strip()
                and any(o['source_id']==row['object_id'] for o in plan['registry']['occurrences'].values())
                and not any(row['object_id'] in i.get('source_ids',[]) for i in items)):
            row.update(disposition='excluded_with_reason',knowledge_ids=[],reason='此原生对象没有正文文字，仅登记原图位置；原图在独立occurrence账中核对附件绑定，不从图中推断文字事实。')
        if row.get('disposition')!='represented':continue
        if row.get('object_type') not in ('source','occurrence'):continue
        row['knowledge_ids']=sorted(i['knowledge_id'] for i in items if row.get('object_id') in
            (i.get('source_ids',[]) if row['object_type']=='source' else item_occurrences(i)))
    return result


def check_registry(sources, assets, occurrences):
    """Only trusted parsers/registrars build this registry, never model JSON."""
    if not sources or len(sources) > 10000: fail('来源数量无效')
    for key, source in sources.items():
        if source.get('source_id') != key or not isinstance(source.get('text'), str) or not source.get('version_sha256') or not source.get('locator'):
            fail('来源缺少身份、版本、原文或定位')
    for key, asset in assets.items():
        if asset.get('asset_id') != key or not isinstance(asset.get('sha256'), str) or len(asset['sha256']) != 64: fail('图片资产身份或hash无效')
        if any(type(asset.get(k)) is not int or asset[k] <= 0 for k in ('width', 'height')): fail('图片原始尺寸无效')
    for key, occurrence in occurrences.items():
        if occurrence.get('occurrence_id') != key or occurrence.get('asset_id') not in assets or occurrence.get('source_id') not in sources or not occurrence.get('locator'):
            fail('图片出现未绑定真实来源及图片资产')


def check_evidence(evidence, sources, assets, occurrences):
    for entry in evidence:
        source = sources.get(entry['source_id'])
        quote = entry['quote']
        if source is None or not quote.strip() or quote not in source['text']: fail('证据引文不属于当前来源')
        occurrence_id = entry.get('occurrence_id')
        if not occurrence_id:
            if entry.get('asset_id') is not None or entry.get('region') is not None: fail('图片证据缺少出现标识')
            continue
        occurrence = occurrences.get(occurrence_id)
        if not occurrence or occurrence['source_id'] != entry['source_id'] or entry.get('asset_id') != occurrence['asset_id']:
            fail('证据出现、资产与来源绑定不一致')
        asset = assets[occurrence['asset_id']]
        region = entry.get('region')
        if (not isinstance(region, list) or len(region) != 4
                or any(type(n) not in (float, int) or not math.isfinite(n) for n in region)
                or not 0 <= region[0] < region[2] <= asset['width']
                or not 0 <= region[1] < region[3] <= asset['height']): fail('OCR证据框超出原图或尺寸无效')
        # OCR quote must resolve to a registered line with exact Unicode offsets.
        matches = [line for line in occurrence.get('ocr_lines', [])
                   if line.get('region') == region and line.get('text') == quote]
        if len(matches) != 1: fail('OCR证据须绑定唯一已登记文字行和图框')
        line = matches[0]
        start, end = line.get('start'), line.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source['text']) or source['text'][start:end] != quote:
            fail('OCR引文与来源offset不一致')


def check_items(items, sources, assets, occurrences):
    if not items or len(items) > MAX_ITEMS: fail('知识单元数量无效')
    ids = unique([item['knowledge_id'] for item in items], '知识ID')
    for item in items:
        if not item['content'].strip() or not item['source_ids']: fail('知识正文或依据来源为空')
        unique(item['source_ids'], '知识来源')
        unique(item['occurrence_ids'], '知识图片出现')
        attachments=item.get('attachment_occurrence_ids',[])
        unique(attachments,'知识附件图片出现')
        if not set(attachments)<=occurrences.keys():fail('附件引用越出原件图片注册表')
        if not set(item['source_ids']) <= sources.keys() or not set(item['occurrence_ids']) <= occurrences.keys(): fail('知识引用越出授权注册表')
        check_evidence(item['evidence'], sources, assets, occurrences)
        evidence_sources = {e['source_id'] for e in item['evidence']}
        evidence_occurrences = {e['occurrence_id'] for e in item['evidence'] if e.get('occurrence_id')}
        if evidence_sources != set(item['source_ids']) or evidence_occurrences != set(item['occurrence_ids']): fail('知识引用集合与实际逐条证据不一致')
        for claim in item['claims']:
            if not claim['text'].strip() or any(index >= len(item['evidence']) for index in claim['evidence_indices']): fail('逐结论证据索引无效')
        if not item['claims']: fail('每条知识必须包含逐结论证据')
        if any(sources[k].get('source_state') in ('draft', 'conflicting', 'revoked') for k in item['source_ids']) and item['status'] == 'approved': fail('草稿、撤销或冲突来源不能自动成为已确认制度')
        if any(not occurrences[k].get('ocr_reviewed', False) for k in item['occurrence_ids']) and item['status'] == 'approved': fail('未复核OCR依据须待审')
    return ids


def check_result(value, sources, assets, occurrences, *, coverage_sources=None, coverage_occurrences=None):
    ids = check_items(value['items'], sources, assets, occurrences)
    coverage = value['coverage']
    keys = [(c['object_type'], c['object_id']) for c in coverage]
    expected = {('source', k) for k in (sources if coverage_sources is None else coverage_sources)} | {('occurrence', k) for k in (occurrences if coverage_occurrences is None else coverage_occurrences)}
    actual=unique(keys, '覆盖对象')
    if actual != expected: fail('每个本批核心来源和图片出现必须恰有一个覆盖决定',details={'missing_coverage_objects':[{'object_type':k,'object_id':v} for k,v in sorted(expected-actual)],'unexpected_coverage_objects':[{'object_type':k,'object_id':v} for k,v in sorted(actual-expected)]})
    unreferenced=[]
    for row in coverage:
        if not set(row['knowledge_ids']) <= ids: fail('覆盖引用不存在的知识ID')
        linked = {i['knowledge_id'] for i in value['items'] if row['object_id'] in (i['source_ids'] if row['object_type']=='source' else item_occurrences(i))}
        if row['disposition'] == 'represented' and (set(row['knowledge_ids']) != linked or not linked):
            unreferenced.append({'object_type':row['object_type'],'object_id':row['object_id'],'knowledge_ids':sorted(linked),'reason':'此对象被声明为represented，但当前没有与之相符的完整正文来源引用或原图附件引用。请补足实际知识/附件绑定；如确为非业务对象，须由模型明确排除并解释。仅改knowledge_ids无效。'})
        if row['disposition'] != 'represented' and not row['reason'].strip(): fail('待审或排除须给出原因')
    if unreferenced:fail('represented对象必须由实际知识或原图附件引用，不能只声明已覆盖',details={'unreferenced_objects':unreferenced})
    return value


def stable_ids(items, source_version):
    """Content-addressed IDs; exact reruns keep identity and all prior IDs map once."""
    mapping = {}
    result = []
    for item in items:
        old = item['knowledge_id']
        if old in mapping: fail('归并前ID重复')
        identity = {k: item[k] for k in ('task_key', 'knowledge_layer', 'parent_task_key', 'kind', 'content', 'source_ids', 'occurrence_ids')}
        if 'attachment_occurrence_ids' in item:identity['attachment_occurrence_ids']=item['attachment_occurrence_ids']
        new = 'knowledge_' + digest({'version': source_version, 'unit': identity})[:32]
        if new in mapping.values(): fail('内容相同单元须先显式归并，不能重复分配稳定ID')
        mapping[old] = new
        result.append({**deepcopy(item), 'knowledge_id': new})
    return result, mapping


def quality_gate(value, *, final_audit_receipt, candidate_sha256, relations=(),terminal_manifest=None):
    """Structural gate plus an actual independent audit receipt, not semantic proof."""
    if (not isinstance(final_audit_receipt, dict) or final_audit_receipt.get('stage') != 'finalaudit'
            or not final_audit_receipt.get('execution_id') or final_audit_receipt.get('candidate_sha256') != candidate_sha256
            or final_audit_receipt.get('result_sha256') != digest(value)):
        fail('缺少绑定最终归并及关系结果的真实最终独审凭据', 'SLICING_FINALAUDIT_REQUIRED')
    if final_audit_receipt.get('contract'):
        from .slicing_terminal_v2 import validate_manifest
        validate_manifest(value,final_audit_receipt,terminal_manifest,relations)
    blockers = [issue for issue in value['issues'] if issue['severity'] == 'blocker' and issue['status'] == 'open']
    pending = (any(r.get('decision') == 'pending_review' for r in relations)
               or not value['complete'] or blockers or any(i['status'] != 'approved' or not all(i['semantic_checks'].values()) for i in value['items'])
               or any(c['disposition'] == 'pending_review' for c in value['coverage']))
    if pending: fail('最终独审存在待审、未覆盖或阻断项，不能生成正式CSV', 'SLICING_PENDING_REVIEW')
    return True
