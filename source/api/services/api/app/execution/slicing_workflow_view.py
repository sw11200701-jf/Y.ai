"""Read-only projection of the exact configured slicing prompt pipeline."""
from copy import deepcopy

STAGES=[
 ('plan','任务计划与上下文补读','识别完整业务任务；缺少前提、续页或表头时补读已授权原文。',['完整原文与版面','核心来源范围','补读导航'],['任务计划','上下文请求'],'按每组上下文补读上限循环'),
 ('slice','语义切片','按智能体策略生成完整业务知识，保留条件、例外、步骤和来源证据。',['任务计划','完整已读取来源'],['完整知识候选','来源覆盖账'],'每组生成一次；结构无效时进入有限纠正'),
 ('audit','两轮独立审核','分别检查完整业务任务与适用范围、逐结论证据与操作条件；不能改正文。',['同一份不可变候选','完整原文'],['逐条审核判定','具体问题'],'每轮返修后重新进行两轮独立审核'),
 ('repair','有限返修','对照原文修复实际问题，返回完整候选；不得只把审核标记改成通过。',['完整候选','具体审核或结构问题','完整原文'],['修复后的完整候选','仍未解决的问题'],'受每组返修上限约束；仍有阻断项则停止'),
 ('merge','跨组归并','核对同一任务、同一知识层级和同一父任务的知识；只有同一检索目的才归并。',['全部分组候选','完整来源'],['归并后知识','全部旧ID的唯一去向'],'分批遍历符合条件的知识对，不按相似词直接合并'),
 ('candidates','业务关系候选','检查跨片的前提、下一步、替代、例外等真实业务关系，不为凑数连边。',['归并后的知识','相关完整来源'],['关系候选','本批已评估ID'],'全量分批覆盖；超出候选上限会停止，不裁掉尾部关系'),
 ('relation','逐对关系审核','核对每对关系的端点、方向、类型与原文依据；无依据则拒绝。',['候选关系','两端完整知识','相关原文'],['接受、拒绝或待审判定','关系证据'],'分批逐对判定；待审关系不能伪装为通过'),
 ('finalaudit','最终独立审核','分别复核全部知识和全文件来源覆盖；最终候选正文不可修改。',['不可变最终候选','原文覆盖清单','规划义务'],['逐条最终判定','全文件覆盖判定'],'知识分片审核和来源分片审核都须完成')
]

def effective(identifier, value):
    from app.agents.slicing_specialists import SPECIALISTS,prompts
    from app.knowledge.slicing_native_prompts import pin
    from app.knowledge.slicing_contracts import stage_settings
    configured=value.get('stage_prompts')
    strategy=value.get('strategy',identifier)
    if strategy in SPECIALISTS:configured=prompts(strategy,configured)
    pinned=pin(configured)
    # Validate the real combined prompts now, not only the shorter editable text.
    stage_settings({k:{n:v[n] for n in ('system_prompt','user_prompt')} for k,v in pinned.items()})
    return pinned

def workflow_view(identifier,value):
    from .builtin_agents import RecipeOptions,portable_defaults
    from app.agents.slicing_specialists import SPECIALISTS
    stages=effective(identifier,value)
    strategy=value.get('strategy',identifier)
    options=value.get('recipe_options') or portable_defaults(strategy)['recipe_options']
    fields=deepcopy(RecipeOptions.model_json_schema()['properties'])
    for name,field in fields.items():field['default']=options[name]
    return {'contract':'slicing-workflow-view-v1','agent_id':identifier,'revision':value['revision'],
        'strategy':deepcopy(SPECIALISTS.get(strategy)), 'effective_recipe_options':options,'recipe_option_schema':fields,
        'stages':[{'id':key,'name':name,'purpose':purpose,'inputs':inputs,'outputs':outputs,'execution':execution,
            'effective_system_prompt':stages[key]['system_prompt'],'effective_user_prompt':stages[key]['user_prompt']} for key,name,purpose,inputs,outputs,execution in STAGES],
        'before':{'name':'来源准备与任务准入','purpose':'校验原件、当前人员与资料权限、模型版本、完整请求预算；解析并固定原生版面和来源分组。'},
        'after':{'name':'原子保存草稿','purpose':'来源、版本、审核与执行凭据都通过后才保存新草稿；不会自动发布。默认导出核对最新保存版本。'},
        'runtime_notes':['此处为实际有效基础提示词；运行时会按当前输入追加JSON结构、分片协议和不可变候选约束。',
            '每组语义返修上限与JSON/证据结构纠正是不同计数；后者最多两次，全部计入整项任务的模型调用上限。',
            '上下文预算覆盖完整请求与输出预留；超限会明确停止，不静默截断原件或候选。',
            '资料、OCR文字与模型旧回复都是待处理数据，不能改变系统规则或扩大权限。']}
