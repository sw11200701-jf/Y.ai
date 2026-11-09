"""Trusted message metadata resolution; bytes remain behind owner-scoped attachment IDs."""
from app.contracts import MessageEnvelope
from app.db import get_resource
from app.errors import AppError

def public_attachment(payload):
    return {k:payload[k] for k in ("id","filename","mime","size_bytes","media_kind","sha256","created_at") if k in payload}

async def resolve_message(*,message,prompt,inputs,context,session):
    envelope=MessageEnvelope.model_validate(message) if message is not None else MessageEnvelope(type="text",text=prompt or "")
    if len(set(envelope.attachment_ids))!=len(envelope.attachment_ids):raise AppError("MESSAGE_INVALID","Duplicate attachment ID",422)
    attachments=[public_attachment(get_resource(session,"attachment",aid,context)) for aid in envelope.attachment_ids]
    kinds=[a["media_kind"] for a in attachments];text=envelope.text.strip()
    valid={"text":bool(text) and not kinds,"image":bool(kinds) and set(kinds)=={"image"},"audio":len(kinds)==1 and kinds[0]=="audio","file":bool(kinds) and set(kinds)=={"document"},"mixed":bool(text) and bool(kinds)}
    if not valid[envelope.type]:raise AppError("MESSAGE_INVALID","Message content does not match selected trigger type",422)
    merged=dict(inputs)
    for key,value in envelope.inputs.items():
        if key in merged and merged[key]!=value:raise AppError("MESSAGE_INPUT_CONFLICT","Message and run inputs conflict",422)
        merged[key]=value
    return {"type":envelope.type,"text":text,"attachment_ids":envelope.attachment_ids,"attachments":attachments,"inputs":merged,"source_refs":["attachment:"+a["id"]+":"+a["sha256"] for a in attachments],"capabilities":{"requires_vision":"image" in kinds,"requires_audio":"audio" in kinds,"requires_document_extraction":"document" in kinds}}
