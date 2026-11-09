"""Bounded completed chat SSE decoding; no incremental browser delivery/retry."""
import json

def decode_chat_sse(body):
    def invalid(): raise ValueError('Invalid bounded chat SSE')
    if not isinstance(body, (bytes, bytearray)) or len(body) > 262144: invalid()
    try: source = bytes(body).decode('utf-8', errors='strict').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeError: invalid()
    content, reasoning, data = [], [], []
    usage, finish, done, count = {}, None, False, 0
    for line in [*source.split('\n'), '']:
        if line.startswith(':'): continue
        if line:
            field, separator, value = line.partition(':')
            if field == 'data' and separator: data.append(value[1:] if value.startswith(' ') else value)
            continue
        if not data: continue
        value, data = '\n'.join(data), []
        count += 1
        if count > 4096 or done: invalid()
        if value == '[DONE]':
            done = True
            continue
        try: event = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, TypeError, RecursionError): invalid()
        if not isinstance(event, dict) or 'error' in event or not isinstance(event.get('choices'), list) or len(event['choices']) > 1: invalid()
        if event.get('usage') is not None:
            if not isinstance(event['usage'], dict): invalid()
            usage = {key: number for key, number in event['usage'].items() if key in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(number) is int and number >= 0}
        for choice in event['choices']:
            if not isinstance(choice, dict) or type(choice.get('index', 0)) is not int or choice.get('index', 0) != 0: invalid()
            delta = choice.get('delta', {})
            if not isinstance(delta, dict) or delta.get('tool_calls'): invalid()  # Tool continuation is explicitly unavailable in these profiles.
            if finish is not None and any(delta.get(key) for key in ('content', 'reasoning_content')): invalid()
            for name, target in [('content', content), ('reasoning_content', reasoning)]:
                fragment = delta.get(name)
                if fragment is not None:
                    if not isinstance(fragment, str): invalid()
                    target.append(fragment)
            # Independent encrypted_content/signature packets are ignored. Never
            # serialize private continuation data into the public model result.
            reason = choice.get('finish_reason')
            if reason is not None:
                if not isinstance(reason, str) or finish is not None: invalid()
                finish = reason
    if not done or finish not in {'stop', 'length', 'content_filter'}: invalid()
    return {'choices': [{'message': {'content': ''.join(content), 'reasoning_content': ''.join(reasoning)}, 'finish_reason': finish}], 'usage': usage}
