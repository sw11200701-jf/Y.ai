"""Local native context windows; never drops a core source or bypasses readback."""
import re
from .slicing_reading_order import ordered_identifiers
from .slicing_contracts import fail

CONTRACT='native-xlsx-context-windows-v1'

def windows(registry,groups):
    ids=ordered_identifiers(registry,'sources');positions={sid:i for i,sid in enumerate(ids)}
    flat=[sid for group in groups for sid in group]
    if len(flat)!=len(ids) or len(set(flat))!=len(ids) or set(flat)!=set(ids):fail('核心分组遗漏或重复来源')
    headers={}
    for sid in ids:
        loc=registry['sources'][sid]['locator'];cell=loc.get('cell','');match=re.fullmatch(r'[A-Z]+([1-9][0-9]*)',cell)
        if loc.get('sheet') and match and int(match[1])<=12:headers.setdefault(loc['sheet'],set()).add(sid)
    result=[]
    for group in groups:
        keep=set(group)
        sheets={registry['sources'][sid]['locator'].get('sheet') for sid in group}
        for sheet in sheets:keep.update(headers.get(sheet,set()))
        for sid in group:
            index=positions[sid];loc=registry['sources'][sid]['locator'];sheet=loc.get('sheet')
            for other in ids[max(0,index-8):index+9]:
                if registry['sources'][other]['locator'].get('sheet')==sheet:keep.add(other)
        result.append([sid for sid in ids if sid in keep])
    return result
