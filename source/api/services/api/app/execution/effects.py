"""Transient effect context for existing six-argument business handlers."""
from contextvars import ContextVar
from dataclasses import dataclass
from app.errors import AppError, chinese_message


@dataclass(frozen=True)
class EffectContext:
    execution_id: str
    run_id: str
    node_key: str
    principal_id: str
    tenant_id: str
    workspace_id: str


current_effect: ContextVar[EffectContext | None] = ContextVar("execution_effect", default=None)


def get_effect_context() -> EffectContext | None:
    """Server-only stable identity; do not accept this value from user config."""
    return current_effect.get()


_OBSERVATION_SEAL = object()
_OBSERVED_VALIDATION_CODES = frozenset({'OUTPUT_SCHEMA_MISMATCH', 'MODEL_ERROR', 'CONTEXT_BUDGET_EXCEEDED',
    'CHUNK_AGENT_RESPONSE_INCOMPLETE', 'CHUNK_AGENT_OUTPUT_INVALID', 'CHUNK_AGENT_SOURCE_MISMATCH',
    'CHUNK_AGENT_SPAN_OVERLAP', 'CHUNK_AGENT_COVERAGE_INCOMPLETE', 'CHUNK_OVERVIEW_INVALID',
    'CHUNK_OVERVIEW_COVERAGE_INVALID', 'CHUNK_OVERVIEW_EVIDENCE_INVALID',
    'SLICING_RESPONSE_INCOMPLETE','SLICING_RESULT_INVALID','SLICING_FINALAUDIT_REQUIRED','KNOWLEDGE_CONTENT_AUDIT_FAILED'})


@dataclass(frozen=True, init=False, repr=False)
class ModelResponseReceipt:
    """Transient server capability, never a DTO/checkpoint or response body."""
    _effect: EffectContext | None
    _seal: object
    _nonce: object

    def __init__(self, *, _seal, effect):
        if _seal is not _OBSERVATION_SEAL:
            raise TypeError('Model response receipts are server-created only')
        object.__setattr__(self, '_effect', effect)
        object.__setattr__(self, '_seal', _seal)
        object.__setattr__(self, '_nonce', object())


class ObservedModelFailure(AppError):
    def __init__(self, *, _seal, receipt, error):
        if _seal is not _OBSERVATION_SEAL:
            raise TypeError('Observed model failures are server-created only')
        self._receipt = receipt
        self._seal = _seal
        domain_message = ('独立内容复核未通过，未生成完整草稿；此任务不能恢复或重试'
            if error.code=='KNOWLEDGE_CONTENT_AUDIT_FAILED' else
            '模型回复达到输出上限或包含工具请求，切片未生成；此任务不能恢复或重试'
            if error.code in ('CHUNK_AGENT_RESPONSE_INCOMPLETE','SLICING_RESPONSE_INCOMPLETE') else
            '已收到模型回复，但切片格式或原文覆盖校验未通过；此任务不能恢复或重试'
            if error.code.startswith(('CHUNK_AGENT_', 'CHUNK_OVERVIEW_','SLICING_')) else chinese_message(error.code))
        super().__init__(error.code, domain_message, error.status_code,
            {'phase': 'response_validation', 'response_received': True,
             'effect_issued': True, 'automatic_retry': False})
        if isinstance(error.details,dict):
            issues=error.details.get('validation_issues')
            if isinstance(issues,list):
                clean=[]
                fields={'overview','topics','units','through','topic','summary','chunks','unit_id','start','end','quote','text','evidence_ids','overview_evidence_ids','<source_unit>'}
                rules={'required','additionalProperties','type','minLength','maxLength','minItems','maxItems','enum','schema'}
                for item in issues[:8]:
                    if not isinstance(item,dict) or item.get('rule') not in rules or not isinstance(item.get('path'),list):continue
                    safe={'rule':item['rule'],'path':[p if type(p) is int and 0<=p<=10000 else p if type(p) is str and p in fields else '<source_unit>' for p in item['path'][:8]]}
                    for name in ('limit','observed_length'):
                        value=item.get(name)
                        if type(value) is int and 0<=value<=10000000:safe[name]=value
                    clean.append(safe)
                if clean:self.details['validation_issues']=clean
        # Only allowlisted protocol metadata, never model text or provider data.
        if error.code in ('CHUNK_AGENT_RESPONSE_INCOMPLETE','SLICING_RESPONSE_INCOMPLETE') and isinstance(error.details, dict):
            if error.details.get('finish_reason') in ('stop','length','tool_calls','content_filter','function_call'):
                self.details['finish_reason'] = error.details['finish_reason']
            if type(error.details.get('tool_request_received')) is bool:
                self.details['tool_request_received'] = error.details['tool_request_received']
            for key in ('output_limit','completion_tokens','prompt_tokens','total_tokens','reasoning_tokens'):
                value=error.details.get(key)
                if type(value) is int and 0<=value<=10000000:self.details[key]=value


def observe_complete_model_response() -> ModelResponseReceipt:
    """03 calls only AFTER the bounded HTTP body stream ends successfully."""
    return ModelResponseReceipt(_seal=_OBSERVATION_SEAL, effect=get_effect_context())


def observed_model_validation_failure(receipt, error: AppError) -> ObservedModelFailure:
    """Wrap a local validation failure of that exact completed model response."""
    if (type(receipt) is not ModelResponseReceipt or receipt._seal is not _OBSERVATION_SEAL
            or receipt._effect != get_effect_context() or not isinstance(error, AppError)
            or error.code not in _OBSERVED_VALIDATION_CODES):
        raise TypeError('Invalid completed model response observation')
    if type(error) is ObservedModelFailure and error._receipt is receipt:
        return error
    return ObservedModelFailure(_seal=_OBSERVATION_SEAL, receipt=receipt, error=error)


def is_observed_model_failure(error, effect: EffectContext) -> bool:
    """The bridge checks private authority and exact effect identity, not flags."""
    return (effect is not None and type(error) is ObservedModelFailure
            and error._seal is _OBSERVATION_SEAL
            and type(error._receipt) is ModelResponseReceipt
            and error._receipt._seal is _OBSERVATION_SEAL
            and error._receipt._effect == effect)
