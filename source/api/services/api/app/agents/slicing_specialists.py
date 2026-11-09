"""Three independently configured strategies; legacy plans remain unchanged."""
from copy import deepcopy
from app.errors import AppError
from app.imports.document_container import canonical_format

DOCUMENT='builtin_document_chunker'
TABLE='builtin_table_chunker'
PRESENTATION='builtin_presentation_chunker'
SPECIALISTS={
    DOCUMENT: {'name':'文档智能体','formats':('text','markdown','doc','docx','pdf'), 'policy':'''按章节、完整任务和业务问题进行语义切分。连续长文本中的定义、前提、指代、限制、例外、脚注与结论必须连贯；跨页续句或“三是”等连续枚举不得错断。功能概述、操作步骤、注意事项是同一任务的角色章节，不是三个不同任务。保留完整任务总览，再按独立检索价值补充细节。报告中的错误案例和正确准则须成对表达，不得把错误做法变成规则。业务日期与报告分析期间分别保留。'''} ,
    TABLE: {'name':'表格智能体','formats':('xlsx','csv','tsv'), 'policy':'''表格不按字符数、单元格或固定行数切知识。先识别工作簿、工作表中的完整业务表/独立区块，保留表名、真实表头、行主键、单位、条件、例外、审核结论及共享规则。能独立回答问题的一张小表可整体作为一条知识；有多个检索目的时保留完整表格总览并生成有意义的规则/记录明细，不为每格制造碎片。表格的物理源分组只是传输窗口，不是最终知识边界。仅在实际合并范围内继承合并单元格，不得向范围外填充或把下方独立表块强归上方业务。全称、对象及用途须有原文明确映射；空白不等于零，负数不能变正，公式不冒充已计算结果；疑问、计划、建议不是已生效制度。跨工作表的真实映射和编撰要求应读回原文后关联，不按邻近关系猜测。'''} ,
    PRESENTATION: {'name':'PPT智能体','formats':('pptx',), 'policy':'''先理解整个演示文稿的主题、场景和页间延续，再按完整业务场景或操作流程切知识。标题页、同一示例的原始状态/调整前/调整后以及续页应联合阅读，保留对比和完整流程总览；不同操作方式或相反处理不可错误合并。按独立检索目的拆出关键概念、完整表格对比、示例与步骤明细，而不是一页一片或一个文本框一片。图表需保留行列、空白、零、负数、借贷方向及对象。原文按钮实际名称优先，不能用常识重写截图/文字的分录。图片若原生文字已充分覆盖无需重复OCR；仅图像存在的必要事实无可靠文字时标待审，不臆测。'''}
}

def select(kind):
    kind = canonical_format(kind)
    for identifier, spec in SPECIALISTS.items():
        if kind in spec['formats']: return identifier
    raise AppError('SLICING_FORMAT_UNSUPPORTED','该格式尚无可用语义切片智能体',422)

def prompts(identifier, settings):
    from app.knowledge.slicing_contracts import DEFAULTS
    values=deepcopy(settings) if settings else {k:{'system_prompt':v['system_prompt'],'user_prompt':v['user_prompt']} for k,v in DEFAULTS['stages'].items()}
    # Pin the specialist instruction into every actual stage, including both
    # independent reviews and repairs. It is not a display-only Agent name.
    for value in values.values():
        value['system_prompt']+='\n【'+SPECIALISTS[identifier]['name']+'策略】\n'+SPECIALISTS[identifier]['policy']
    return values

def groups(registry, identifier, limit):
    """Use native sheet/slide/task boundaries before bounded transport packing."""
    from app.knowledge.slicing_contracts import encoded, fail
    from app.knowledge.slicing_parser import text_projection
    from app.knowledge.slicing_reading_order import ordered_identifiers
    ids=ordered_identifiers(registry,'sources'); result=[]; group=[]; size=0; previous=None
    for sid in ids:
        loc=registry['sources'][sid]['locator']
        # A slide sequence is deliberately packed together. No automatic
        # boundary is introduced at each slide. Sheets retain their identity.
        boundary=loc.get('sheet') if identifier==TABLE else None
        cost=len(encoded(text_projection(registry,[sid])).encode())
        if cost>limit: fail('完整来源对象超过预算，未截断','SLICING_CONTEXT_BUDGET_EXCEEDED')
        if group and (size+cost>limit or len(group)>=128 or boundary!=previous):
            result.append(group);group=[];size=0
        group.append(sid);size+=cost;previous=boundary
    if group: result.append(group)
    return result

def contexts(registry, groups, identifier):
    from app.knowledge.slicing_reading_order import ordered_identifiers
    ids=ordered_identifiers(registry,'sources')
    if identifier==TABLE:
        from app.knowledge.slicing_native_context_windows import windows
        from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries
        initial=windows(registry,groups); result=[]
        cells={sid:(s['locator'].get('sheet'),coordinate_to_tuple(s['locator']['cell']))
            for sid,s in registry['sources'].items() if s['locator'].get('kind')=='cell'}
        for group, loaded in zip(groups,initial):
            keep=set(loaded); rows={(cells[sid][0],cells[sid][1][0]) for sid in group if sid in cells}; anchors=set()
            for sid in group:
                if sid not in cells: continue
                sheet,(row,col)=cells[sid]
                for merged in registry['sources'][sid].get('metadata',{}).get('merged_ranges',[]):
                    c1,r1,c2,r2=range_boundaries(merged)
                    if r1<=row<=r2 and c1<=col<=c2: anchors.add((sheet,(r1,c1)))
            # Row peers retain column meaning; only actual merged anchors inherit.
            for sid,(sheet,(row,col)) in cells.items():
                if (sheet,(row,col)) in anchors or (sheet,row) in rows: keep.add(sid)
            result.append([sid for sid in ids if sid in keep])
        return result
    if identifier==PRESENTATION:
        if len(ids)<=128: return [list(ids) for _ in groups]
        result=[]
        for group in groups:
            slides={registry['sources'][sid]['locator'].get('slide') for sid in group}
            nearby={p+d for p in slides if isinstance(p,int) for d in (-1,0,1)}
            result.append([sid for sid in ids if sid in group or registry['sources'][sid]['locator'].get('slide') in nearby])
        return result
    if identifier==DOCUMENT:
        result=[]
        for group in groups:
            pages={registry['sources'][sid]['locator'].get('page') for sid in group}
            nearby={p+d for p in pages if isinstance(p,int) for d in (-1,0,1)}
            result.append([sid for sid in ids if sid in group or registry['sources'][sid]['locator'].get('page') in nearby])
        return result
    return [list(group) for group in groups]
