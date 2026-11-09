"""Maintenance-only append-once retention journal. Not outcome settlement.

Separate journal avoids modifying historical46 schema. Runtime enforcement must
be wired and independently qualified before this module may be enabled.
"""
import hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path

_PIN_FIELDS = {'PIN': {'tenant_id': 'str', 'workspace_id': 'str', 'run_id': 'str', 'execution_id': 'str', 'node_key': 'str', 'input_ref': 'str', 'generation': 'int', 'engine_key': 'str', 'dsl_hash': 'str', 'plan_sha256': 'str'}, 'EXTRA_UNKNOWN': {'run_id': 'str', 'seal': 'str'}, 'LATEST_UNKNOWN': {'run_id': 'str', 'execution_id': 'str', 'seal': 'str'}, 'FOURTH_UNKNOWN': {'run_id': 'str', 'execution_id': 'str', 'seal': 'str'}}

def _load_private_pins():
    from app.settings import settings
    path = settings.data_dir / 'retention-pins.json'
    if not path.exists() and not path.is_symlink():
        return {name: {'run_id': None} for name in _PIN_FIELDS}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('RETENTION_PRIVATE_PINS_INVALID')
    values = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(values, dict) or set(values) != set(_PIN_FIELDS):
        raise ValueError('RETENTION_PRIVATE_PINS_INVALID')
    for name, fields in _PIN_FIELDS.items():
        binding = values[name]
        if not isinstance(binding, dict) or set(binding) != set(fields):
            raise ValueError('RETENTION_PRIVATE_PINS_INVALID')
        for key, kind in fields.items():
            value = binding[key]
            if type(value).__name__ != kind or not value:
                raise ValueError('RETENTION_PRIVATE_PINS_INVALID')
    if len({pin['run_id'] for pin in values.values()}) != len(values):
        raise ValueError('RETENTION_PRIVATE_PINS_INVALID')
    return values


_PRIVATE_PINS = _load_private_pins()
PIN = _PRIVATE_PINS['PIN']

EXTRA_UNKNOWN = _PRIVATE_PINS['EXTRA_UNKNOWN']

def extra_record_verified():
    # Opaque equality only. Never parse/publish model inputs, secrets or bodies.
    from app.settings import settings
    path=settings.data_dir/'demo.sqlite3'
    if not path.is_file():return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            row=c.execute("SELECT json_extract(payload,'$.status'),json_extract(payload,'$.error.details.cause') FROM resources WHERE kind='run' AND resource_id=?",(EXTRA_UNKNOWN['run_id'],)).fetchone()
            if row!=('outcome_unknown','CHUNK_AGENT_RESPONSE_INCOMPLETE'):return False
            seal=hashlib.sha256()
            for table,column in (('resources','resource_id'),('execution_runs','run_id'),('execution_steps','run_id'),('execution_blobs','run_id'),('run_authorizations','run_id')):
                rows=c.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY 1',(EXTRA_UNKNOWN['run_id'],)).fetchall()
                seal.update(table.encode());seal.update(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode())
            return seal.hexdigest()==EXTRA_UNKNOWN['seal']
    except Exception:return False



LATEST_UNKNOWN = _PRIVATE_PINS['LATEST_UNKNOWN']

def latest_record_verified():
    from app.settings import settings
    path=settings.data_dir/'demo.sqlite3'
    if not path.is_file():return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if c.execute("SELECT json_extract(payload,'$.status') FROM resources WHERE kind='run' AND resource_id=?",(LATEST_UNKNOWN['run_id'],)).fetchone()!=('outcome_unknown',):return False
            seal=hashlib.sha256()
            for table,column in [('resources','resource_id'),('execution_runs','run_id'),('execution_steps','run_id'),('execution_blobs','run_id'),('run_authorizations','run_id'),('run_events','run_id')]:
                rows=c.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY 1',(LATEST_UNKNOWN['run_id'],)).fetchall()
                seal.update(table.encode());seal.update(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode())
            return seal.hexdigest()==LATEST_UNKNOWN['seal']
    except Exception:return False

FOURTH_UNKNOWN = _PRIVATE_PINS['FOURTH_UNKNOWN']

def fourth_record_verified():
    from app.settings import settings
    path=settings.data_dir/'demo.sqlite3'
    if not path.is_file():return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if c.execute("SELECT json_extract(payload,'$.status') FROM resources WHERE kind='run' AND resource_id=?",(FOURTH_UNKNOWN['run_id'],)).fetchone()!=('outcome_unknown',):return False
            seal=hashlib.sha256()
            for table,column in [('resources','resource_id'),('execution_runs','run_id'),('execution_steps','run_id'),('execution_blobs','run_id'),('run_authorizations','run_id'),('run_events','run_id')]:
                rows=c.execute('SELECT * FROM '+table+' WHERE '+column+'=? ORDER BY 1',(FOURTH_UNKNOWN['run_id'],)).fetchall()
                seal.update(table.encode());seal.update(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode())
            return seal.hexdigest()==FOURTH_UNKNOWN['seal']
    except Exception:return False

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def validate_binding(value):
    if PIN['run_id'] is None:raise ValueError('RETENTION_PRIVATE_PINS_REQUIRED')
    if not isinstance(value,dict) or value!=PIN:raise ValueError('RETENTION_BINDING_MISMATCH')

def initialize_empty(path):
    path=Path(path)
    if path.exists():raise ValueError('RETENTION_JOURNAL_ALREADY_EXISTS')
    with closing(sqlite3.connect(path)) as c:
        c.executescript("""CREATE TABLE retained_intent (singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding TEXT NOT NULL, seal TEXT NOT NULL, audit TEXT NOT NULL);
CREATE TRIGGER no_update BEFORE UPDATE ON retained_intent BEGIN SELECT RAISE(ABORT,'IMMUTABLE_RETENTION'); END;
CREATE TRIGGER no_delete BEFORE DELETE ON retained_intent BEGIN SELECT RAISE(ABORT,'IMMUTABLE_RETENTION'); END;""")

def append_once(path,*,binding,seal,audit):
    validate_binding(binding)
    if not isinstance(seal,str) or len(seal)!=64 or any(c not in '0123456789abcdef' for c in seal):raise ValueError('RETENTION_SEAL_INVALID')
    if not isinstance(audit,dict) or set(audit)!={'actor','at','source_manifest_sha256','target_manifest_sha256','purpose'} or audit['purpose']!='retain_unknown_no_replay' or any(not isinstance(v,str) or not v for v in audit.values()):raise ValueError('RETENTION_AUDIT_INVALID')
    with closing(sqlite3.connect(path)) as c:
        c.execute('BEGIN IMMEDIATE')
        prior=c.execute('SELECT binding,seal,audit FROM retained_intent WHERE singleton=1').fetchone();expected=(canonical(binding),seal,canonical(audit))
        if prior is not None:
            if prior!=expected:raise ValueError('RETENTION_APPEND_CONFLICT')
            return False
        c.execute('INSERT INTO retained_intent VALUES(1,?,?,?)',expected)
        c.commit()
    return True

def read_exact(path):
    path=Path(path)
    if not path.is_file():raise ValueError('RETENTION_JOURNAL_REQUIRED')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        c.execute('PRAGMA query_only=ON');rows=c.execute('SELECT binding,seal,audit FROM retained_intent').fetchall()
    if len(rows)!=1:raise ValueError('RETENTION_RECORD_REQUIRED')
    binding=json.loads(rows[0][0]);validate_binding(binding)
    return {'binding':binding,'seal':rows[0][1],'audit':json.loads(rows[0][2])}


def journal_path():
    from app.settings import settings
    return settings.data_dir/'retained-intent-v1.sqlite3'

def startup_check():
    path=journal_path()
    if path.exists():read_exact(path)

def is_retained(run_id):
    # This immutable target fence is independent of journal availability.
    # The journal permits maintenance review; it never permits execution.
    return isinstance(run_id, str) and run_id in (PIN['run_id'],EXTRA_UNKNOWN['run_id'],LATEST_UNKNOWN['run_id'],FOURTH_UNKNOWN['run_id'])

def deny_execution(run_id):
    if not is_retained(run_id):return
    from app.errors import AppError
    try:
        read_exact(journal_path())
        if run_id==EXTRA_UNKNOWN['run_id'] and not extra_record_verified():raise ValueError('EXTRA_UNKNOWN_UNVERIFIABLE')
        if run_id==LATEST_UNKNOWN['run_id'] and not latest_record_verified():raise ValueError('LATEST_UNKNOWN_UNVERIFIABLE')
        if run_id==FOURTH_UNKNOWN['run_id'] and not fourth_record_verified():raise ValueError('FOURTH_UNKNOWN_UNVERIFIABLE')
    except Exception:raise AppError('RETAINED_INTENT_UNVERIFIABLE','历史隔离记录无法核对，禁止执行',409) from None
    raise AppError('RETAINED_UNKNOWN_NO_REPLAY','历史结果仍未知，已保留并永久禁止原意图再执行',409)


def historical_notice(run_id):
    if not is_retained(run_id):return None
    verified=False
    try:
        read_exact(journal_path());verified=(run_id!=EXTRA_UNKNOWN['run_id'] or extra_record_verified()) and (run_id!=LATEST_UNKNOWN['run_id'] or latest_record_verified()) and (run_id!=FOURTH_UNKNOWN['run_id'] or fourth_record_verified())
    except Exception:pass
    return {'status':'outcome_unknown','execution_prohibited':True,'outcome_settled':False,'record_verified':verified,'message':'历史结果仍未知，原意图永久禁止再执行；隔离凭据'+('已核对' if verified else '未能核对')}
