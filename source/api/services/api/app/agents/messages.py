"""Five-envelope adapter; all attachment bytes are owner checked by engineering."""

from app.errors import AppError


async def agent_message(*, message, prompt="", inputs=None, allow_images=False, context, session):
    from app.messages import resolve_message
    raw = message.model_dump() if hasattr(message, "model_dump") else message
    # Resolved metadata is re-authorized from IDs, never trusted as caller metadata.
    envelope = {k: raw[k] for k in ("type", "text", "attachment_ids", "inputs") if k in raw}
    resolved = await resolve_message(message=envelope, prompt=prompt, inputs=inputs or {}, context=context, session=session)
    capabilities = resolved["capabilities"]
    if capabilities["requires_audio"] or capabilities["requires_vision"] and not allow_images:
        raise AppError("MODEL_CAPABILITY_UNAVAILABLE", "视觉/语音适配尚未配置，不能读取图片内容或伪造转写", 503)
    documents = []
    for attachment in resolved["attachments"]:
        if attachment['media_kind'] == 'image': continue
        from app.attachments import load_attachment
        from app.imports.documents import import_document, extract_document
        metadata, data = load_attachment(attachment_id=attachment["id"], context=context, session=session)
        document = await import_document(filename=metadata["filename"], content=data, context=context, session=session)
        extracted = await extract_document(file_id=document["id"], context=context, session=session)
        documents.append({"file_id": document["id"], "chunks": extracted["chunks"], "source_refs": extracted["source_refs"],
                          "data_sources": [{"kind": "attachment", "resource_id": attachment["id"], "action": "use"},
                                           {"kind": "document", "resource_id": document["id"], "action": "use"}]})
    resolved["documents"] = documents
    resolved['image_attachment_ids'] = [item['id'] for item in resolved['attachments'] if item['media_kind'] == 'image']
    if documents and not resolved["text"]:
        resolved["text"] = "已读取授权文档附件"
    return resolved
