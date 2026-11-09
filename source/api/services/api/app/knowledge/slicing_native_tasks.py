"""Native task context, not generated knowledge. Does not change source text."""
from .slicing_contracts import digest,fail
CONTRACT='native-task-role-context-v1'
ROLES={'功能概述','操作步骤','注意事项','前置条件','操作流程'}
W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'w':W}

def paragraph_tasks(archive,part,root,read_xml):
    styles={}
    if 'word/styles.xml' in archive.namelist():
        for style in read_xml(archive,'word/styles.xml').findall('w:style',NS):
            outline=style.find('w:pPr/w:outlineLvl',NS);base=style.find('w:basedOn',NS)
            styles[style.get('{'+W+'}styleId')]=(outline.get('{'+W+'}val') if outline is not None else None,base.get('{'+W+'}val') if base is not None else None)
    def level(style_id):
        visited=set()
        while style_id in styles and style_id not in visited:
            visited.add(style_id);value,parent=styles[style_id]
            if value is not None:return int(value) if value.isdigit() and 0<=int(value)<=8 else None
            style_id=parent
        return None
    path={};role='';result={}
    for index,paragraph in enumerate(root.findall('.//w:p',NS),1):
        text=''.join(t.text or '' for t in paragraph.findall('.//w:t',NS)).strip()
        style=paragraph.find('w:pPr/w:pStyle',NS);style_id=style.get('{'+W+'}val','') if style is not None else ''
        if style_id.upper().startswith('TOC'):
            result[index]={'task_key':'navigation:'+part,'task_path':[],'role':'navigation'};continue
        direct=paragraph.find('w:pPr/w:outlineLvl',NS);value=direct.get('{'+W+'}val','') if direct is not None else ''
        heading=int(value) if value.isdigit() and 0<=int(value)<=8 else level(style_id)
        if text in ROLES:role=text
        elif heading is not None and text:
            path={k:v for k,v in path.items() if k<heading};path[heading]=text;role=''
        task_path=[path[k] for k in sorted(path)]
        result[index]={'task_key':digest({'part':part,'path':task_path})[:32],'task_path':task_path,'role':role}
    return result

def expand_task_context(registry,selected):
    if registry.get('native_task_outline_contract')!=CONTRACT:return list(selected)
    outline=registry.get('native_task_outline',{})
    if registry.get('native_task_outline_sha256')!=digest(outline) or not set(outline)<=set(registry['sources']):fail('原生任务上下文索引校验失败','SLICING_SCOPE_DENIED')
    if not set(selected)<=set(registry['sources']):fail('核心来源越界','SLICING_SCOPE_DENIED')
    if not selected:return []
    paths=[outline[sid]['task_path'] for sid in selected if sid in outline and outline[sid]['role']!='navigation']
    keys={task['task_key'] for task in outline.values() if task['role']!='navigation' and any(task['task_path']==path[:len(task['task_path'])] for path in paths)}
    chosen=set(selected)|{sid for sid,task in outline.items() if task['task_key'] in keys}
    from .slicing_reading_order import ordered_identifiers
    return [sid for sid in ordered_identifiers(registry,'sources') if sid in chosen]
