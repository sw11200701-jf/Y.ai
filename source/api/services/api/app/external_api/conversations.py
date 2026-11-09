"""Partitioned, bounded chat history with a persistent single writer."""
from sqlalchemy import select
from app.db import new_id,utc_now
from app.errors import AppError
from . import bridge
from .models import Conversation,ConversationMessage
from .storage import write_gate,fresh,dump,load
from .values import reject_credentials,canonical_payload_digest


def _conversation(session,authority,conversation_id):
    row=fresh(session,Conversation,conversation_id)
    if row is None or row.partition!=bridge.partition(authority):raise AppError('NOT_FOUND','会话不存在或无权访问',404)
    return row


def begin_message(session,*,authority,conversation_id,run_id,message):
    write_gate(session);authority=bridge.current(session,authority,'chat.invoke')
    reject_credentials(message);canonical_payload_digest(message,maximum_bytes=32768)
    if conversation_id:
        row=_conversation(session,authority,conversation_id)
    else:
        row=Conversation(id=new_id('appconv'),partition=bridge.partition(authority),revision=0,created_at=utc_now())
        session.add(row);session.flush()
    if row.active_run_id:
        if row.active_run_id==run_id:return {'conversation_id':row.id,'revision':row.revision}
        raise AppError('APP_CONVERSATION_BUSY','同一会话已有运行，请等待完成后继续',409)
    # Must be paired with same-txn reserve+run creation by central admission.
    row.active_run_id=run_id;row.revision+=1
    session.add(ConversationMessage(id=new_id('appmsg'),conversation_id=row.id,run_id=run_id,sequence=row.revision,role='user',content=dump(message),created_at=utc_now()))
    session.flush();return {'conversation_id':row.id,'revision':row.revision}


def complete_message(session,*,authority,conversation_id,run_id,status,public_answer=None):
    """Central supplies already output-schema/ACL checked public answer only."""
    write_gate(session);authority=bridge.current(session,authority)
    row=_conversation(session,authority,conversation_id)
    prior=session.scalar(select(ConversationMessage).where(ConversationMessage.run_id==run_id,ConversationMessage.role=='assistant'))
    if prior:return
    if row.active_run_id!=run_id:raise AppError('APP_REVISION_CONFLICT','会话运行绑定已变化',409)
    if status not in {'succeeded','failed','cancelled'}:return
    if status=='succeeded':
        reject_credentials(public_answer);canonical_payload_digest(public_answer,maximum_bytes=32768)
        row.revision+=1
        session.add(ConversationMessage(id=new_id('appmsg'),conversation_id=row.id,run_id=run_id,sequence=row.revision,role='assistant',content=dump(public_answer),created_at=utc_now()))
    row.active_run_id=None;session.flush()


def history(session,*,authority,conversation_id,authorize_run,required_scope=None):
    """Callback checks current run/source ACLs; no prior-authorization cache."""
    if required_scope is None:required_scope='conversations.read' if 'conversations.read' in authority['scopes'] else 'chat.invoke'
    if required_scope not in {'conversations.read','chat.invoke'}:raise AppError('APP_SCOPE_DENIED','会话读取操作无效',403)
    authority=bridge.current(session,authority,required_scope);row=_conversation(session,authority,conversation_id)
    # Only complete user/assistant pairs; no hidden/failed/partial outputs.
    completed=select(ConversationMessage.run_id).where(ConversationMessage.conversation_id==row.id,ConversationMessage.role=='assistant')
    messages=list(session.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id==row.id,
        ConversationMessage.run_id.in_(completed)).order_by(ConversationMessage.sequence.desc()).limit(10)))
    pairs={}
    for item in messages:pairs.setdefault(item.run_id,[]).append(item)
    safe=[];size=0
    for run_id,pair in pairs.items():
        if len(pair)!=2 or {x.role for x in pair}!={'user','assistant'}:continue
        projected=[{'role':item.role,'content':load(item.content)} for item in sorted(pair,key=lambda x:x.sequence)]
        reject_credentials(projected);length=len(dump(projected).encode('utf-8'))
        if size+length>32768:break
        authorize_run(session,authority=authority,run_id=run_id)
        safe=projected+safe;size+=length
    return {'conversation_id':row.id,'revision':row.revision,'messages':safe,'active_run_id':row.active_run_id}
