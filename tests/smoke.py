"""Check the development UI/API and optionally exercise a new synthetic workspace."""
import argparse
from http.cookiejar import CookieJar
import json
from secrets import token_hex
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler, Request


def check(origin, fresh):
    url = urlsplit(origin)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.port is None
            or url.path not in ('', '/') or url.username or url.password or url.query or url.fragment):
        raise ValueError('Use the explicit local Docker development address.')
    origin = origin.rstrip('/')
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(path, data=None, content_type='application/json', csrf=None, status=200):
        headers = {'Origin': origin}
        if data is not None:
            headers['Content-Type'] = content_type
        if csrf:
            headers['X-CSRF-Token'] = csrf
        with opener.open(Request(origin + path, data=data, headers=headers), timeout=20) as response:
            if response.status != status:
                raise RuntimeError(f'{path} returned {response.status}, expected {status}.')
            return response.read()

    def encoded(value):
        return json.dumps(value, ensure_ascii=False).encode('utf-8')

    assert b'/_next/static/' in request('/')
    status = json.loads(request('/api/v1/auth/status'))
    assert isinstance(status['initialized'], bool)
    print('PASS local UI and authentication proxy')
    if not fresh:
        return
    if status['initialized']:
        raise RuntimeError('The synthetic fixture requires a fresh development instance.')
    csrf = json.loads(request('/api/v1/auth/csrf'))['csrf_token']
    user = {'username': 'smoke_' + token_hex(4), 'password': 'Smoke!' + token_hex(18)}
    request('/api/v1/auth/setup', encoded(user), csrf=csrf, status=201)
    csrf = json.loads(request('/api/v1/auth/csrf'))['csrf_token']
    formats = json.loads(request('/api/v1/knowledge/capabilities'))['format_routes']
    assert len(formats) == 20 and formats['.rtf'] == formats['.docx']
    library = json.loads(request('/api/v1/knowledge-bases', encoded({'name': 'Synthetic smoke fixture'}), csrf=csrf, status=201))
    raw = br'{\rtf1\ansi Synthetic approval clause.}'
    boundary = token_hex(16)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.rtf"\r\n'
            'Content-Type: application/rtf\r\n\r\n').encode('ascii') + raw + f'\r\n--{boundary}--\r\n'.encode('ascii')
    upload = json.loads(request(f"/api/v1/knowledge-bases/{library['id']}/documents?profile=portable-slicing-1.0.0",
                               body, f'multipart/form-data; boundary={boundary}', csrf=csrf, status=201))
    assert request(f"/api/v1/knowledge/files/{upload['file_id']}/download") == raw
    options = json.loads(request('/api/v1/builtin-agents/builtin_knowledge_chunker/document-job-options?'
                                 f"document_id={upload['id']}"))
    assert options['id'] == 'builtin_document_chunker'
    print('PASS fresh setup, 20 formats, RTF route and exact original download')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('origin', nargs='?', default='http://127.0.0.1:3271')
    parser.add_argument('--fresh-fixture', action='store_true')
    args = parser.parse_args()
    check(args.origin, args.fresh_fixture)
