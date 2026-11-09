"""Only explicitly enabled provider public explanations; never hidden reasoning."""
NOT_PROVIDED='该模型未提供可展示的思考过程'

def unavailable():return {'available':False,'unavailable_reason':NOT_PROVIDED}

def provider_reasoning(message,capabilities,*,private_messages=()):
    for field,kind in (('reasoning_summary','provider_summary'),('reasoning_explanation','provider_explanation')):
        if capabilities.get(field) is not True:continue
        value=message.get(field)
        if not isinstance(value,str) or not value.strip():continue
        if len(value)>4000 or len(value.encode('utf-8'))>8192:return unavailable()
        # A purported summary that repeats an entire supplied message is not a
        # safe explanation. Compatibility reasoning_content requires a separate
        # explicit model/version profile, never this legacy boolean path.
        if any(isinstance(m,dict) and isinstance(m.get('content'),str) and m['content'] and
               (m.get('role')=='system' or len(m['content'])>=32) and m['content'] in value for m in private_messages):
            return unavailable()
        return {'available':True,'text':value,'kind':kind,'source':'provider.message.'+field}
    if 'reasoning_content' in message or 'encrypted_content' in message:
        return {'available':False,'status':'not_adapted','unavailable_reason':'此回复字段尚未选择明确的供应商公开思考profile'}
    return unavailable()
