"""Remove only converter-generated missing REF field results; preserve business text."""
from io import BytesIO
from zipfile import ZipFile
from lxml import etree as E

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
TAG=lambda name:'{'+W+'}'+name
ERRORS=frozenset(('Error: Reference source not found','Error: Reference source not found.'))

def normalize(raw):
    output=BytesIO();removed=0
    with ZipFile(BytesIO(raw)) as source,ZipFile(output,'w') as target:
        for member in source.infolist():
            data=source.read(member.filename)
            if member.filename.startswith('word/') and member.filename.endswith('.xml'):
                root=E.fromstring(data,parser=E.XMLParser(resolve_entities=False,no_network=True))
                stack=[];changed=False
                for node in root.iter():
                    if node.tag==TAG('fldChar'):
                        kind=node.get(TAG('fldCharType'))
                        if kind=='begin':stack.append({'instruction':'','result':False})
                        elif kind=='separate' and stack:stack[-1]['result']=True
                        elif kind=='end' and stack:stack.pop()
                    elif node.tag==TAG('instrText') and stack:
                        stack[-1]['instruction']+=node.text or ''
                    elif node.tag==TAG('t') and (node.text or '').strip() in ERRORS:
                        eligible=any(s['result'] and s['instruction'].strip().split(' ')[0] in ('REF','PAGEREF','NOTEREF') for s in stack)
                        simple=next((a for a in node.iterancestors(TAG('fldSimple'))),None)
                        if simple is not None:eligible=eligible or simple.get(TAG('instr'),'').strip().split(' ')[0] in ('REF','PAGEREF','NOTEREF')
                        if eligible:node.text='';removed+=1;changed=True
                if changed:data=E.tostring(root,encoding='UTF-8',xml_declaration=True,standalone=True)
            target.writestr(member,data)
    return output.getvalue(),removed
