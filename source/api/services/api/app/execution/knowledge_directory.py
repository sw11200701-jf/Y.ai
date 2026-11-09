"""Explicit workflow directory contract for the released Schema6 repository.

This describes routes, not access grants. Every collection/version read still
uses its existing library, resource, file and current-user authorization.
"""
from typing import Literal
from pydantic import BaseModel,ConfigDict


class KnowledgeDirectoryCapabilities(BaseModel):
    model_config=ConfigDict(extra="forbid")
    schema_version: Literal["workflow-knowledge-directory-v1"]="workflow-knowledge-directory-v1"
    catalog: Literal["published_knowledge_versions"]="published_knowledge_versions"
    resource_collections: list[Literal["documents","datasets"]]
    typed_sources: Literal[False]=False
    published_status: Literal["published"]="published"


def capabilities(context,session):
    from app.knowledge.service import require_knowledge
    require_knowledge(context,session)
    return KnowledgeDirectoryCapabilities(resource_collections=["documents","datasets"])
