"""Run-bound image IDs become inline bytes only at the trusted model boundary."""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import BytesIO
import math
import re
from typing import Literal
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.errors import AppError

class VisionProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    profile_id: Literal['openai_image_url_v1']
    protocol: Literal['openai_chat_completions'] = 'openai_chat_completions'
    model_id: str = Field(min_length=1, max_length=200)
    model_revision: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}$')
    input_source: Literal['current_run_message_attachment'] = 'current_run_message_attachment'
    token_reservation: Literal['tiles512_2048_plus1024_v1'] = 'tiles512_2048_plus1024_v1'

    @field_validator('model_revision')
    @classmethod
    def explicit_revision(cls,value):
        if value.lower() in {'latest','auto','default','unknown'}:raise ValueError('须明确视觉模型版本，不能使用漂移别名')
        return value

MAX_IMAGES, MAX_SINGLE_BYTES, MAX_TOTAL_BYTES, MAX_PIXELS = 4, 2097152, 4194304, 1048576
_seal = object()
_selected = ContextVar('trusted_vision_selection', default=None)

@dataclass(frozen=True)
class SelectedImages:
    scope: object = field(repr=False)
    ids: tuple[str, ...]
    _trusted: object = field(repr=False)

def _deny():
    raise AppError('VISION_INPUT_NOT_AUTHORIZED', '图片必须来自本次运行已授权消息附件', 403)

def profile(config):
    raw = config.capabilities.get('vision_profile')
    if raw is None:
        raise AppError('MODEL_VISION_NOT_CONFIGURED', '所选模型尚未声明此版本的兼容图片输入能力', 422)
    try: value = VisionProfile.model_validate(raw)
    except ValueError: raise AppError('MODEL_VISION_PROFILE_INVALID', '视觉模型profile或协议无效', 422) from None
    if value.model_id != config.model or value.model_revision.lower() in {'latest','auto','default','unknown'}:
        raise AppError('MODEL_VISION_PROFILE_MISMATCH', '视觉profile必须绑定所选模型和明确版本', 422)
    reasoning = config.capabilities.get('reasoning_profile')
    if reasoning is not None:
        if (not isinstance(reasoning, dict) or reasoning.get('profile_id') == 'deepseek_public_reasoning_v1'
                or reasoning.get('model_id') != value.model_id or reasoning.get('model_revision') != value.model_revision):
            raise AppError('MODEL_VISION_PROFILE_MISMATCH', '此推理profile尚未声明同版本视觉联用能力', 422)
    return value

def _load(selection):
    from .model_access import current_model_access
    from app.execution.effects import get_effect_context
    from app.execution.storage import check_active_run
    from app.db import get_resource
    context, session = current_model_access()
    effect = get_effect_context()
    if (type(selection) is not SelectedImages or selection._trusted is not _seal or effect is None
            or (effect.run_id, effect.principal_id, effect.tenant_id, effect.workspace_id) != (
                selection.scope.run_id, context.operator_id, context.tenant_id, context.workspace_id)):
        _deny()
    check_active_run(session, effect.run_id)
    return _load_in_scope(selection.scope,selection.ids,context,session)

def _load_in_scope(scope,ids,context,session):
    from app.execution.message_inputs import get_message_attachment_input
    from app.db import get_resource
    if scope is None or scope is not get_message_attachment_input():_deny()
    images, total = [], 0
    for identifier in ids:
        # Check declared size before invoking the existing confined/hash-checked loader.
        metadata = get_resource(session, 'attachment', identifier, context)
        if metadata.get('media_kind') != 'image' or type(metadata.get('size_bytes')) is not int or not 1 <= metadata['size_bytes'] <= MAX_SINGLE_BYTES:
            raise AppError('VISION_IMAGE_LIMIT', '图片类型无效或单图超过2MiB', 422)
        metadata, body = scope.load(attachment_id=identifier, context=context, session=session)
        total += len(body)
        if len(body) != metadata['size_bytes'] or len(body) > MAX_SINGLE_BYTES or total > MAX_TOTAL_BYTES:
            raise AppError('VISION_IMAGE_LIMIT', '图片实际大小不一致或总量超过4MiB', 422)
        try:
            with Image.open(BytesIO(body)) as image:
                if (image.format not in {'PNG','JPEG','WEBP'} or Image.MIME[image.format] != metadata['mime']
                        or image.width*image.height > MAX_PIXELS or max(image.size) > 2048
                        or getattr(image, 'n_frames', 1) != 1):
                    raise ValueError()
                width, height = image.size
                image.verify()
        except Exception:
            raise AppError('VISION_IMAGE_INVALID', '图片须为有效单帧PNG/JPEG/WebP，至多1048576像素且边长不超2048', 422) from None
        images.append((metadata, body, width, height))
    return images

@contextmanager
def bind_images(attachment_ids, *, context, session):
    """03 node handler calls with IDs; ordinary JSON cannot mint the scope/seal."""
    from app.execution.message_inputs import get_message_attachment_input
    from .model_access import bind_model_access
    if not isinstance(attachment_ids, list) or len(attachment_ids) > MAX_IMAGES or any(
            not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in attachment_ids) or len(set(attachment_ids)) != len(attachment_ids):
        raise AppError('VISION_INPUT_INVALID', '请选择本次消息的至多4张不同图片', 422)
    scope = get_message_attachment_input()
    if attachment_ids and scope is None: _deny()
    selection = SelectedImages(scope, tuple(attachment_ids), _seal) if attachment_ids else None
    if selection:
        with bind_model_access(context=context, session=session): _load(selection)
    token = _selected.set(selection)
    try: yield selection
    finally: _selected.reset(token)

def validate_text_messages(messages):
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise AppError('VISION_INPUT_INVALID', '模型消息结构无效', 422)
    for message in messages:
        if (not isinstance(message, dict) or message.get('role') not in {'system','user','assistant','tool'}
                or not isinstance(message.get('content'), str) and not (message.get('role') == 'assistant' and message.get('content') is None)):
            raise AppError('VISION_INPUT_INVALID', '图片仅接受受控附件ID，不接受任意URL、路径或多模态JSON', 422)

def prepare_images(config, messages):
    validate_text_messages(messages)
    selection = _selected.get()
    if selection is None: return messages, 0
    profile(config)
    images = _load(selection)
    users = [index for index, message in enumerate(messages) if message['role'] == 'user']
    if not users: raise AppError('VISION_INPUT_INVALID', '视觉输入需要一条User任务消息', 422)
    wire = [dict(message) for message in messages]
    content = [{'type': 'text', 'text': wire[users[-1]]['content']}]
    reserve = 0
    for metadata, body, width, height in images:
        encoded = base64.b64encode(body).decode('ascii')
        content.append({'type': 'image_url', 'image_url': {'url': 'data:'+metadata['mime']+';base64,'+encoded}})
        reserve += 1024+2048*math.ceil(width/512)*math.ceil(height/512)
    wire[users[-1]]['content'] = content
    return wire, reserve

def validate_payload(payload):
    """Reauthorize exact inline payload immediately before HTTP; no URL fetch."""
    blocks = [block for message in payload.get('messages', []) if isinstance(message.get('content'), list)
              for block in message['content'] if isinstance(block, dict) and block.get('type') == 'image_url']
    if not blocks: return ()
    selection = _selected.get()
    if selection is None: _deny()
    images = _load(selection)
    urls, private = [], []
    for metadata, body, _, _ in images:
        encoded = base64.b64encode(body).decode('ascii')
        url = 'data:'+metadata['mime']+';base64,'+encoded
        urls.append({'type':'image_url','image_url':{'url':url}})
        private.extend((url, encoded))
    if blocks != urls: _deny()
    return tuple(private)

def revalidate_images():
    if _selected.get() is not None: _load(_selected.get())

def selected_source_refs():
    selection = _selected.get()
    if selection is None: return []
    return ['attachment:'+metadata['id']+':'+metadata['sha256'] for metadata, _, _, _ in _load(selection)]
