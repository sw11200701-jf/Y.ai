"""Trusted, optional native DOC conversion; original identity and immutable private cache."""
from pathlib import Path
from io import BytesIO
import hashlib,json,os,secrets,signal,subprocess,zipfile,shutil
from app.errors import AppError
from .slicing_doc_fields import normalize

MAX=40*1024*1024
CONTRACT='native-doc-converter-private-cache-v1'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def fail(code):raise AppError(code,'本地DOC转换未完成，未调用模型',409)
def validate(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX:fail('SLICING_DOC_CONVERSION_INVALID')
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            entries=z.infolist()
            if sum(e.file_size for e in entries)>80*1024*1024 or len(entries)>10000 or len({e.filename for e in entries})!=len(entries):fail('SLICING_DOC_CONVERSION_INVALID')
            if any(e.flag_bits&1 or e.filename.startswith('/') or '..' in e.filename.split('/') for e in entries):fail('SLICING_DOC_CONVERSION_INVALID')
            if 'word/document.xml' not in z.namelist():fail('SLICING_DOC_CONVERSION_INVALID')
    except (zipfile.BadZipFile,KeyError):fail('SLICING_DOC_CONVERSION_INVALID')

def configure(data_dir):
    """Only a protected installer-created configuration enables this adapter."""
    root=Path(data_dir)/'knowledge-doc-converter';config_path=root/'config.json';cache=root/'cache'
    if not config_path.exists():return None
    if root.is_symlink() or config_path.is_symlink() or not cache.is_dir() or cache.is_symlink():fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
    if os.name!='nt' and (root.stat().st_mode&0o077 or cache.stat().st_mode&0o077):fail('SLICING_DOC_CONVERTER_CACHE_PRIVATE_REQUIRED')
    cfg=json.loads(config_path.read_text(encoding='utf-8'))
    if cfg.get('contract')!=CONTRACT or cfg.get('normalizer_sha256')!=sha(Path(__file__).with_name('slicing_doc_fields.py').read_bytes()):fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
    backend=cfg.get('backend')
    if backend=='docker' and os.name=='nt':
        image=cfg.get('image_id','');docker=Path(cfg.get('docker_command',''))
        if not image.startswith('sha256:') or len(image)!=71 or not docker.is_absolute():fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
    elif backend=='local' and os.name!='nt':
        worker=Path('/opt/document-converter/convert_doc_stdin.py')
        if not worker.is_file() or sha(worker.read_bytes())!=cfg.get('worker_sha256'):fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
    else:fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
    # Conversion semantics remain identical when moving Windows Docker to the
    # pinned Linux worker. Host paths must not invalidate preserved source IDs.
    identity=sha(json.dumps({key:cfg[key] for key in ('contract','worker_sha256','normalizer_sha256','qualification_sha256')},sort_keys=True,separators=(',',':')).encode())

    def load(folder,source_hash):
        metadata_path=folder/'metadata.json';content_path=folder/'converted.docx'
        if folder.is_symlink() or metadata_path.is_symlink() or content_path.is_symlink():fail('SLICING_DOC_CONVERSION_INVALID')
        metadata=json.loads(metadata_path.read_text(encoding='utf-8'));raw=content_path.read_bytes()
        if metadata.get('source_sha256')!=source_hash or metadata.get('converter_identity')!=identity or metadata.get('converted_sha256')!=sha(raw):fail('SLICING_DOC_CONVERSION_CACHE_CHANGED')
        validate(raw);return raw

    def convert(*,content):
        if not isinstance(content,bytes) or not 0<len(content)<=MAX or content[:8]!=bytes.fromhex('d0cf11e0a1b11ae1'):fail('SLICING_DOC_CONVERSION_INVALID')
        source_hash=sha(content);destination=cache/source_hash
        if destination.exists():return load(destination,source_hash)
        if backend == 'docker':
            try:
                command_hash = sha(docker.read_bytes())
            except OSError as exc:
                raise AppError('SLICING_DOC_ADAPTER_UNAVAILABLE', '旧版 Word 转换器当前不可用。恢复本机转换环境后，可直接重试已上传的原件。', 409) from exc
            if command_hash != cfg.get('docker_command_sha256'):
                fail('SLICING_DOC_CONVERTER_CONFIG_INVALID')
        token=secrets.token_hex(16);name='yai-native-doc-'+token
        if backend=='docker':
            command=[str(docker),'run','-i','--rm','--name',name,'--label','yai.doc-converter.owner='+token,'--pull=never','--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','128','--memory','1024m','--cpus','2','--tmpfs','/tmp:size=256m,mode=1777',image]
        else:command=['/usr/bin/python3','/opt/document-converter/convert_doc_stdin.py']
        process=None
        try:
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=os.name!='nt',creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            output,error=process.communicate(content,timeout=150)
            if process.returncode!=0:fail('SLICING_DOC_CONVERSION_FAILED')
            validate(output);output,removed=normalize(output);validate(output)
        except subprocess.TimeoutExpired:
            fail('SLICING_DOC_CONVERSION_TIMEOUT')
        finally:
            if backend=='docker':
                # Only this freshly randomized, ownership-labelled container may be removed.
                probe=subprocess.run([str(docker),'inspect',name,'--format','{{index .Config.Labels "yai.doc-converter.owner"}}'],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
                if probe.returncode==0 and probe.stdout.decode().strip()==token:
                    subprocess.run([str(docker),'rm','-f',name],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
                if process is not None and process.poll() is None:process.kill();process.communicate(timeout=10)
            elif process is not None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                if process.poll() is None:process.communicate(timeout=10)
        temporary=cache/('.new-'+token);temporary.mkdir(mode=0o700)
        try:
            (temporary/'converted.docx').write_bytes(output)
            (temporary/'metadata.json').write_text(json.dumps({'contract':CONTRACT,'source_sha256':source_hash,'converted_sha256':sha(output),'converter_identity':identity,'generated_REF_error_results_removed':removed},sort_keys=True)+'\n',encoding='utf-8')
            try:temporary.rename(destination)
            except OSError:
                if not destination.is_dir():raise
                # A concurrent converter may have committed its own immutable output first.
                return load(destination,source_hash)
        finally:
            if temporary.exists():shutil.rmtree(temporary)
        return load(destination,source_hash)
    return convert
