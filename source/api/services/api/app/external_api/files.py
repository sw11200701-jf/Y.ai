"""No URL fetching or storage engine. Central owns content inspection/ACLs."""
import re
from app.db import new_id,utc_now
from app.errors import AppError
from . import bridge
from .models import ApplicationFile
from .storage import write_gate,fresh
from .values import reject_credentials


def register_file(session,*,authority,resource_kind,resource_id,filename,media_type,size_bytes,sha256):
    """Use after existing attachment validator+store+source binding succeeded."""
    write_gate(session);authority=bridge.current(session,authority,'files.upload')
    reject_credentials([filename,resource_id])
    if resource_kind not in {'attachment','artifact'} or not isinstance(resource_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',resource_id):
        raise AppError('APP_INPUT_INVALID','文件来源引用无效',422)
    if not isinstance(filename,str) or not 1<=len(filename)<=150 or any(c in filename for c in '/\\:') or filename in {'.','..'} or any(ord(c)<32 or ord(c)==127 for c in filename):
        raise AppError('APP_INPUT_INVALID','文件名称无效',422)
    if type(size_bytes) is not int or not 0<size_bytes<=min(10485760,authority['runtime_policy'].get('max_file_bytes',10485760)):
        raise AppError('APP_REQUEST_TOO_LARGE','文件大小超过应用限制',413)
    if not isinstance(sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',sha256) or not isinstance(media_type,str) or not re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+',media_type):
        raise AppError('APP_INPUT_INVALID','文件校验元数据无效',422)
    row=ApplicationFile(id=new_id('appfile'),partition=bridge.partition(authority),resource_kind=resource_kind,resource_id=resource_id,
        filename=filename,media_type=media_type,size_bytes=size_bytes,sha256=sha256,created_at=utc_now())
    session.add(row);session.flush();return metadata(row)


def resolve_file(session,*,authority,file_id,scope=None):
    """Internal reference only. Central must then authorize actual source action."""
    if scope is None:scope='files.read' if 'files.read' in authority['scopes'] else 'chat.invoke'
    if scope not in {'files.read','chat.invoke'}:raise AppError('APP_SCOPE_DENIED','文件读取操作无效',403)
    authority=bridge.current(session,authority,scope);row=fresh(session,ApplicationFile,file_id)
    if row is None or row.partition!=bridge.partition(authority):raise AppError('NOT_FOUND','文件不存在或无权访问',404)
    return row


def metadata(row):
    return {'file_id':row.id,'filename':row.filename,'media_type':row.media_type,'size_bytes':row.size_bytes,'sha256':row.sha256,'created_at':row.created_at}
