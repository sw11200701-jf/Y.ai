"""Bounded DOC-to-DOCX byte conversion with macros and link updates disabled."""
from pathlib import Path
import os,sys,tempfile,subprocess,secrets,time
import uno
from com.sun.star.beans import PropertyValue

MAX=40*1024*1024
def property_value(name,value):
    result=PropertyValue();result.Name=name;result.Value=value;return result
def convert(raw):
    if not 0<len(raw)<=MAX or raw[:8]!=bytes.fromhex('d0cf11e0a1b11ae1'):raise ValueError('DOC_INPUT_INVALID')
    with tempfile.TemporaryDirectory(prefix='native-doc-') as folder:
        root=Path(folder);source=root/'source.doc';target=root/'result.docx';source.write_bytes(raw)
        profile=root/'profile';user=profile/'user';user.mkdir(parents=True)
        (user/'registrymodifications.xcu').write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry"><item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>''',encoding='utf-8')
        pipe='native_doc_'+secrets.token_hex(12)
        env={**os.environ,'HOME':str(root),'SAL_USE_VCLPLUGIN':'svp'}
        process=subprocess.Popen(['/usr/bin/libreoffice','-env:UserInstallation='+profile.as_uri(),'--headless','--nologo','--nodefault','--norestore','--accept=pipe,name='+pipe+';urp;StarOffice.ComponentContext'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,start_new_session=False)
        desktop=None;document=None
        try:
            local=uno.getComponentContext();resolver=local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver',local)
            until=time.monotonic()+15;context=None
            while time.monotonic()<until:
                try:context=resolver.resolve('uno:pipe,name='+pipe+';urp;StarOffice.ComponentContext');break
                except Exception:
                    if process.poll() is not None:raise RuntimeError('DOC_CONVERTER_EXITED')
                    time.sleep(.1)
            if context is None:raise RuntimeError('DOC_CONVERTER_START_TIMEOUT')
            desktop=context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop',context)
            never=uno.getConstantByName('com.sun.star.document.MacroExecMode.NEVER_EXECUTE')
            no_update=uno.getConstantByName('com.sun.star.document.UpdateDocMode.NO_UPDATE')
            assert never==0 and no_update==0
            document=desktop.loadComponentFromURL(source.as_uri(),'_blank',0,tuple(property_value(k,v) for k,v in [('Hidden',True),('ReadOnly',True),('MacroExecutionMode',never),('UpdateDocMode',no_update)]))
            if document is None or not document.supportsService('com.sun.star.text.TextDocument'):raise ValueError('DOC_NOT_WRITER_DOCUMENT')
            document.storeToURL(target.as_uri(),tuple(property_value(k,v) for k,v in [('FilterName','Office Open XML Text'),('Overwrite',False)]))
            result=target.read_bytes()
            if not 0<len(result)<=MAX or not result.startswith(b'PK'):raise ValueError('DOC_OUTPUT_INVALID')
            return result
        finally:
            if document is not None:
                try:document.close(True)
                except Exception:pass
            if desktop is not None:
                try:desktop.terminate()
                except Exception:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                import signal
                process.kill();process.wait(timeout=5)

if __name__=='__main__':
    os.umask(0o077)
    try:
        raw=sys.stdin.buffer.read(MAX+1);result=convert(raw);sys.stdout.buffer.write(result);sys.stdout.buffer.flush()
    except Exception:
        sys.stderr.write('DOC_CONVERSION_FAILED\n');sys.exit(2)
