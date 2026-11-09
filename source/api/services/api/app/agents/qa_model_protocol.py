"""Passive localhost fixture response builder.01 owns ports/server/CSRF/candidate.

Only synthetic QA servers should call it. No server, sleep, model/tool execution,
credential access or customer input logging occurs in this module.
"""
from dataclasses import dataclass
import hashlib
import json
from jsonschema import Draft202012Validator

@dataclass(frozen=True)
class FixtureReply:
    status:int
    body:bytes
    delay_ms:int=0

def _json(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()

def reply(payload,*,scenario='long',tool_arguments=None,final_json=None):
    messages=payload.get('messages',[])
    text='\n'.join(item.get('content','') for item in messages if item.get('role')=='user' and isinstance(item.get('content'),str))
    summary='SYNTHETIC input_sha256='+hashlib.sha256(text.encode()).hexdigest()[:16]+' chars='+str(len(text))
    finish='stop';message={'role':'assistant','content':summary+'\n'+'这是受控协议桩的完整试答文字。'*100}
    delay=0
    if scenario=='empty':message['content']=''
    elif scenario=='length':finish='length'
    elif scenario=='tool_request':
        finish='tool_calls';message={'role':'assistant','content':None,'tool_calls':[{'id':'call_trial_marker','type':'function','function':{'name':'unrequested_fixture_tool','arguments':'{}'}}]}
    elif scenario=='invalid_protocol':return FixtureReply(200,b'{"choices":"invalid"}')
    elif scenario=='http_error':return FixtureReply(429,_json({'error':{'code':'rate_limit_exceeded','message':'RAW_SYNTHETIC_MARKER_DO_NOT_REFLECT'}}))
    elif scenario in {'timeout','late'}:delay=1200
    elif scenario=='agent_tools':
        tools=payload.get('tools',[])
        if not tools or any(item.get('type')!='function' for item in tools):raise ValueError('Agent fixture requires actual function tools')
        first=tools[0]['function'];arguments={} if tool_arguments is None else tool_arguments
        if not isinstance(arguments,dict) or not Draft202012Validator(first['parameters']).is_valid(arguments):raise ValueError('Fixture arguments do not match the selected actual tool schema')
        returns=[item for item in messages if item.get('role')=='tool' and item.get('tool_call_id')=='call_agent_fixture_1']
        if not returns:
            finish='tool_calls';message={'role':'assistant','content':None,'tool_calls':[{'id':'call_agent_fixture_1','type':'function',
                'function':{'name':first['name'],'arguments':_json(arguments).decode()}}]}
        else:
            output={'answer':'SYNTHETIC_AGENT_OK','items':[]} if final_json is None else final_json
            fmt=payload.get('response_format',{})
            if fmt.get('type')=='json_schema' and not Draft202012Validator(fmt['json_schema']['schema']).is_valid(output):raise ValueError('Fixture final JSON does not match actual response schema')
            message['content']=_json(output).decode()
    elif scenario!='long':raise ValueError('Unknown fixed synthetic scenario')
    return FixtureReply(200,_json({'choices':[{'index':0,'message':message,'finish_reason':finish}],
        'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}),delay)
