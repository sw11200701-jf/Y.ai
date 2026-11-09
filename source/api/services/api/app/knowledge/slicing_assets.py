"""Server-owned image URL registration; never use URLs emitted by a model."""
from .slicing_contracts import fail

_url_resolver = None


def register_occurrence_url_resolver(callback):
    global _url_resolver
    if not callable(callback) or _url_resolver is not None: raise RuntimeError('Occurrence resolver registration invalid')
    _url_resolver = callback


def occurrence_url(plan, occurrence, *, context, session):
    from .slicing_document import source_guard
    source_guard(plan, context=context, session=session)
    if _url_resolver is None: fail('图片受权下载注册器尚未接入，未用模型URL生成CSV', 'SLICING_ASSET_URL_UNAVAILABLE')
    return _url_resolver(plan=plan, occurrence=occurrence, context=context, session=session)
