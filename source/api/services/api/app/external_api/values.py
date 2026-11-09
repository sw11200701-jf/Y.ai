"""Pure bounded values; no routes, resource lookup, identity or implicit history."""
import hashlib
import json
import re
from app.errors import AppError


def canonical_payload_digest(value,*,maximum_bytes=262144,maximum_depth=12):
    """Private idempotency digest. Preserve JSON0/false/empty/null distinctly."""
    def valid(item,depth=0):
        if depth>maximum_depth:return False
        if item is None or type(item) in (str,int,float,bool):return True
        if type(item) is list:return len(item)<=1024 and all(valid(v,depth+1) for v in item)
        if type(item) is dict:return len(item)<=256 and all(type(k) is str and valid(v,depth+1) for k,v in item.items())
        return False
    if not valid(value):raise AppError('APP_INPUT_INVALID','外部请求参数类型、数量或嵌套层数无效',422)
    try:encoded=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    except (ValueError,TypeError,RecursionError,UnicodeError):raise AppError('APP_INPUT_INVALID','外部请求须为有效的有限JSON',422) from None
    if len(encoded)>maximum_bytes:raise AppError('APP_REQUEST_TOO_LARGE','外部请求超过应用允许的长度',413)
    return hashlib.sha256(encoded).hexdigest()


def external_user_label(value):
    if not isinstance(value,str) or not 1<=len(value)<=128 or len(value.encode('utf-8'))>512 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise AppError('APP_INPUT_INVALID','客户用户标识须为1至128个字符且不含控制字符',422)
    return value  # Literal namespace only; never a User, path, role or authority.


def bounded_reported_usage(value):
    """Missing usage is unknown, never zero/fabricated billing proof."""
    if not isinstance(value,dict):return {'status':'unknown','tokens':{}}
    incomplete=False
    if 'tokens' in value:
        if value.get('status') not in {'known','partial'} or not isinstance(value['tokens'],dict):return {'status':'unknown','tokens':{}}
        incomplete=value.get('status')=='partial'
        value=value['tokens']
    tokens={k:v for k,v in value.items() if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v) is int and 0<=v<=2**53-1}
    if 'total_tokens' in tokens and tokens['total_tokens']<sum(tokens.get(k,0) for k in ('prompt_tokens','completion_tokens')):
        tokens.pop('total_tokens')  # Contradictory provider metadata is not actual billing.
    return {'status':'partial' if tokens and incomplete else 'known' if 'total_tokens' in tokens else 'partial' if tokens else 'unknown','tokens':tokens}


KEY_PATTERN=re.compile(r'zpa_[0-9a-f]{16,32}_[A-Za-z0-9_-]{32,}')


def reject_credentials(value,depth=0):
    if depth>32:raise AppError('APP_INPUT_INVALID','请求嵌套层数超限',422)
    if isinstance(value,str) and KEY_PATTERN.search(value):
        raise AppError('APP_INPUT_INVALID','业务字段不能包含应用凭据',422)
    if isinstance(value,dict):
        for key,item in value.items():reject_credentials(key,depth+1);reject_credentials(item,depth+1)
    elif isinstance(value,(list,tuple)):
        for item in value:reject_credentials(item,depth+1)
