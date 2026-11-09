"""Explicit customer metadata projection; never infer missing business facts."""
import json
from .chunk_export import invalid
DEFAULT_COLUMNS=('knowledge_id','knowledge_content')
SELECTION_CONTRACT='customer-selected-columns-v1'
COLUMNS={'knowledge_id':('知识ID',24),'knowledge_content':('知识内容',124),'source_file':('来源文件',44),'version_id':('知识版本ID',44),'source_location':('原文位置',80),'knowledge_layer':('知识层级',24),'applicable_scope':('适用范围',60),'enabled':('启用状态',16)}

def select_columns(value=None):
    if value is None:return DEFAULT_COLUMNS
    if not isinstance(value,(tuple,list)) or not 2<=len(value)<=len(COLUMNS) or any(type(k) is not str or k not in COLUMNS for k in value):invalid('导出列包含未知字段或数量无效')
    if len(set(value))!=len(value) or tuple(value[:2])!=DEFAULT_COLUMNS:invalid('知识ID和知识内容须为前两列，其余字段不可重复')
    return tuple(value)

def text(value):
    if value is None:return ''
    return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,sort_keys=True)

def metadata_values(chunk,version,file):
    from . import service as s
    registry=version.get('portable_registry',{}).get('sources',{})
    locations=[]
    for sid in chunk.get('source_ids',[]):
        if sid not in registry:invalid('已提交切片的来源定位缺失')
        locations.append(registry[sid]['locator'])
    if not locations:locations=[span['locator'] for span in chunk.get('source_spans',[])]
    if not locations and chunk.get('locator'):locations=[chunk['locator']]
    # Reuse the verified native locator; no inferred scene/version/date.
    def location(loc):
        if loc.get('kind')=='slide_shape':return '幻灯片 '+str(loc['slide'])+'｜对象 '+str(loc['native_shape_id'])
        return s.source_label(loc)
    return {'source_file':file['filename'],'version_id':version['id'],'source_location':'\n'.join(dict.fromkeys(location(loc) for loc in locations)),
            'knowledge_layer':{'scenario_overview':'完整任务','atomic_detail':'独立知识'}.get(chunk.get('knowledge_layer'),text(chunk.get('knowledge_layer'))),
            'applicable_scope':text(chunk.get('applicable_scope')),'enabled':'停用' if chunk.get('enabled') is False else '启用'}
