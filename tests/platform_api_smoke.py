"""Platform publications and knowledge sync against a fresh synthetic database."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import os
import time

from fastapi.testclient import TestClient

from app.main import app
from model_network_smoke import ORIGIN, body, post

PREFIX = '/api/platform/v1'
WORKFLOW = {
    'name': 'Synthetic flow', 'schemaVersion': '2.0', 'mode': 'demo',
    'nodes': [{'id': 'start', 'type': 'start'}, {'id': 'answer', 'type': 'answer', 'inputs': {'text': 'synthetic answer'}}],
    'edges': [{'source': 'start', 'target': 'answer'}],
}


def key(owner, workspace, **grant):
    return body(post(owner, '/platform/keys', {'name': 'Synthetic fixture', 'spaces': [
        {'workspace_id': workspace, **grant}]}), 201)['api_key']


def token(client, raw):
    return body(client.post(PREFIX + '/token', json={'api_key': raw}), 200)['access_token']


def human_publish(owner):
    workflow = body(post(owner, '/workflows', WORKFLOW), 201)
    flow_publication = body(post(owner, f"/platform/assets/workflow/{workflow['id']}/api/publish", {}), 201)
    agent = body(post(owner, '/agents', {'name': 'Synthetic agent', 'agent_type': 'advanced',
        'main_workflow_id': workflow['id']}), 201)
    version = body(post(owner, f"/agents/{agent['id']}/publish", {}), 201)
    agent_publication = body(post(owner, f"/platform/assets/agent/{agent['id']}/api/publish",
        {'agent_version_id': version['id']}), 201)
    return flow_publication, agent_publication


def check():
    assert os.environ.get('FINANCE_DATA_DIR')
    with TestClient(app, base_url=ORIGIN, client=('127.0.0.1', 53000)) as owner:
        user = body(post(owner, '/auth/setup', {'username': 'platform_owner', 'password': 'FixtureOnly!893'}), 201)['user']
        workspace = user['workspace_id']
        root = f'{PREFIX}/spaces/{workspace}'
        with closing(TestClient(app, base_url=ORIGIN)) as client:
            wide = token(client, key(owner, workspace, access='write', execute=True, all_resources=True, allow_create=True))
            headers = {'Authorization': f'Bearer {wide}'}

            def call(method, path, status=200, **kwargs):
                result = client.request(method, root + path, headers={**headers, **kwargs.pop('headers', {})}, **kwargs)
                assert result.status_code == status, (method, path, result.status_code, result.text[:450])
                return result

            contract = body(client.get(PREFIX + '/openapi.json', headers=headers), 200)
            assert PREFIX + '/token' in contract['paths']
            assert root.replace(workspace, '{workspace_id}') + '/knowledge/search' in contract['paths']
            assert contract['paths'][PREFIX + '/token']['post']['security'] == []
            kb_response = call('POST', '/knowledge-bases', 201, json={'name': 'Integration KB'})
            kb = kb_response.json()['id']
            call('PATCH', f'/knowledge-bases/{kb}', 428, json={'description': 'Configured'})
            call('PATCH', f'/knowledge-bases/{kb}', headers={'If-Match': kb_response.headers['etag']}, json={'description': 'Configured'})
            ext = f'/knowledge-bases/{kb}/documents/by-external-id/finance-policy-42'

            def upload(text, expected=None, status=200):
                extra = {'If-Match': expected} if expected else {}
                return call('PUT', ext, status, files={'file': ('policy.txt', text.encode())}, headers=extra)

            created = upload('Original approval evidence policy.', status=201)
            doc = created.json()['id']
            old_file = created.json()['file_id']
            doc_path = f'/knowledge/documents/{doc}'
            assert not upload('Original approval evidence policy.').json()['changed']
            assert call('GET', ext).json()['id'] == doc
            concurrent_path = f'/knowledge-bases/{kb}/documents/by-external-id/concurrent-create'
            with ThreadPoolExecutor(max_workers=2) as pool:
                attempts = list(pool.map(lambda _: client.put(root + concurrent_path, headers=headers,
                    files={'file': ('same.txt', b'Same synthetic original.')}), range(2)))
            assert sorted(result.status_code for result in attempts) == [200, 201]
            assert len({result.json()['id'] for result in attempts}) == 1
            first = call('POST', doc_path + '/parse', json={}).json()['id']
            call('POST', doc_path + '/publish', json={'version_id': first})
            old_head = call('GET', doc_path).headers['etag']
            upload('Replacement invoice reimbursement rules.', status=428)
            upload('Replacement invoice reimbursement rules.', expected=created.headers['etag'], status=412)
            updated = upload('Replacement invoice reimbursement rules.', expected=old_head)
            assert updated.json()['id'] == doc and updated.json()['file_id'] != old_file
            assert updated.json()['published_version_id'] == first
            assert not upload('Replacement invoice reimbursement rules.', expected=old_head).json()['changed']
            assert call('GET', f'/knowledge/files/{old_file}/download').content == b'Original approval evidence policy.'

            def search(query, **filters):
                return call('POST', '/knowledge/search',
                            json={'query': query, 'knowledge_base_ids': [kb], **filters}).json()['hits']

            assert search('approval evidence', file_ids=[old_file])
            assert not search('invoice reimbursement')
            call('POST', doc_path + '/publish', 409, json={'version_id': first})
            call('POST', doc_path + '/revisions', 409,
                 json={'base_version_id': first, 'corrections': [{'chunk_id': 'old', 'text': 'not applied'}]})
            draft = call('POST', doc_path + '/parse', json={'strategy': 'paragraph'}).json()['id']
            chunks = call('GET', doc_path + '/chunks').json()['items']
            saved = call('POST', doc_path + '/revisions', json={
                'base_version_id': draft, 'corrections': [{'chunk_id': chunks[0]['id'], 'text': 'Updated invoice reimbursement evidence.'}],
            }).json()['id']
            call('POST', doc_path + '/publish', json={'version_id': saved})
            assert search('invoice reimbursement')
            assert len(call('GET', doc_path + '/versions').json()['items']) == 3
            exported = call('GET', doc_path + '/chunks/latest.xlsx', params={'expected_version_id': saved})
            assert exported.content.startswith(b'PK') and exported.headers['x-knowledge-version-id'] == saved

            read_only = token(client, key(owner, workspace, access='read', all_resources=True))
            call('PATCH', doc_path, 404, headers={'Authorization': f'Bearer {read_only}'}, json={'name': 'Denied'})
            assert call('GET', ext, headers={'Authorization': f'Bearer {read_only}'}).json()['id'] == doc
            client_token = token(client, key(owner, workspace, access='read', resources=[
                {'kind': 'knowledge_base', 'resource_id': kb, 'access': 'read'}]))
            call('GET', doc_path, 404, headers={'Authorization': f'Bearer {client_token}'})
            scope = {'workspace_id': workspace, 'access': 'write', 'resources': [
                {'kind': 'knowledge_base', 'resource_id': kb, 'access': 'write'},
                {'kind': 'knowledge_document', 'resource_id': doc, 'access': 'write'},
            ]}
            scoped_key = body(post(owner, '/platform/keys', {'name': 'Scoped fixture', 'spaces': [scope]}), 201)
            scope_headers = {'Authorization': 'Bearer ' + token(client, scoped_key['api_key'])}
            latest = call('GET', doc_path, headers=scope_headers)
            call('PATCH', doc_path, headers={**scope_headers, 'If-Match': latest.headers['etag']},
                 json={'name': 'Renamed fixture'})
            csrf = body(owner.get('/api/v1/auth/csrf'), 200)['csrf_token']
            body(owner.put('/api/v1/platform/keys/' + scoped_key['key']['id'],
                headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}, json={'name': 'Scoped fixture',
                    'expected_revision': scoped_key['key']['revision'],
                    'spaces': [{**scope, 'resources': scope['resources'][:1]}]}), 200)
            call('GET', doc_path, 404, headers=scope_headers)
            assert client.get(f'{PREFIX}/spaces/other-workspace/knowledge-bases', headers=headers).status_code in (403, 404)
            call('DELETE', doc_path)
            assert not search('invoice reimbursement')
            call('POST', doc_path + '/restore')
            assert not search('invoice reimbursement')

            for index, publication in enumerate(human_publish(owner)):
                invoke = '/publications/' + publication['id'] + '/invoke'
                idem = {'Idempotency-Key': f'fixture-call-{index}'}
                call('GET', '/publications/' + publication['id'])
                run = call('POST', invoke, 202, headers=idem, json={'parameters': {}}).json()['run_id']
                replay = call('POST', invoke, 202, headers=idem, json={'parameters': {}}).json()
                assert replay['run_id'] == run and replay['replayed']
                call('POST', invoke, 409, headers=idem, json={'parameters': {}, 'prompt': 'different'})
                for _ in range(120):
                    status = call('GET', '/runs/' + run).json()
                    if status['status'] not in ('queued', 'running', 'resuming'):
                        break
                    time.sleep(.05)
                assert status['status'] == 'succeeded', status
                assert status['outputs']['text'] == 'synthetic answer'
    print('PASS published workflow and Agent, knowledge sync, retained originals, concurrent revision guards, search, XLSX and token isolation')


if __name__ == '__main__':
    check()
