"""Pinned passive guidance; it never expands execution/model/tool authority."""
import json
from pydantic import BaseModel,ConfigDict,Field
from app.errors import AppError


class SkillBinding(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    skill_id:str=Field(min_length=1,max_length=100)
    version_id:str=Field(min_length=1,max_length=100)
    content_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')


def resolve_guidance(config,*,context,session):
    if config.get('skill_bindings'):
        raise AppError('FEATURE_UNAVAILABLE','当前产品暂不支持旧Skill绑定应用；原配置和历史记录保留，请明确重新配置后再发布。',422,{'feature':'legacy_skill_bindings','execution_allowed':False})
    return []

CAPABILITIES={}
