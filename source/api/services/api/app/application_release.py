"""Immutable application releases and explicit, non-transitive delegation."""
from copy import deepcopy
import hashlib,json
from sqlalchemy import select
from jsonschema import Draft202012Validator
from app.db import Resource,get_resource,list_resources,new_id,utc_now
from app.contracts import WorkflowDefinition
from app.errors import AppError
from app.execution.storage import encode


def digest(value):return hashlib.sha256(encode(value).encode()).hexdigest()


def _schema(value,label):
    # A published API must declare its public fields, including empty outputs.
    if not isinstance(value,dict) or value.get('type')!='object' or not isinstance(value.get('properties'),dict):
        raise AppError('APP_SCHEMA_REQUIRED',f'发布前需声明{label}对象字段契约',422)
    def visit(item,depth=0):
        if depth>24:raise ValueError()
        if isinstance(item,dict):
            if any(key in item for key in ('$ref','$dynamicRef','$recursiveRef')):raise ValueError()
            private={'password','apikey','secretref','credentialref','authorization','cookie','systemprompt','reasoning','reasoningcontent','path','filepath','traceback','rawresponse'}
            if any(''.join(c.lower() for c in key if c.isalnum()) in private for key in item.get('properties',{})):raise ValueError()
            for child in item.values():visit(child,depth+1)
        elif isinstance(item,list):
            for child in item:visit(child,depth+1)
    try:visit(value);Draft202012Validator.check_schema(value)
    except Exception:raise AppError('APP_SCHEMA_INVALID','应用字段契约无效或包含外部引用',422) from None
    return deepcopy(value)


def prepare_release(session,context,source,runtime_policy,permission='applications.publish'):
    from app.auth import validate_context
    from app.rbac import require_permission
    from app.agents.service import get_agent_config
    from app.data_access import agent_sources,DATA_KINDS,_row
    from app.data_models import DataResourceLink,DataTableBinding
    from app.model_catalog.service import pin_run_models
    from app.runner.validation import validate_workflow
    validate_context(context,session);require_permission(context,session,permission)
    if context.principal_type!='human':raise AppError('PERMISSION_DENIED','发布需要人类管理会话',403)
    refs={};agents={};root_config=None
    def dependency(kind,identifier):
        if not isinstance(identifier,str) or not identifier or len(refs)>=512:raise AppError('APP_DEPENDENCY_INVALID','应用依赖无效或过多',422)
        if (kind,identifier) in refs:return
        value=get_resource(session,kind,identifier,context)
        row=_row(session,context,kind,identifier)
        # Canonical physical names avoid treating an alias as an independent grant.
        refs[(row.kind,row.resource_id)]={'kind':row.kind,'resource_id':row.resource_id,'actions':['use']}
        if row.kind in {'model_configuration','model_provider','plugin_version','connection'}:
            refs[(row.kind,row.resource_id)]['revision']=value.get('revision')
        link=session.get(DataResourceLink,row.id)
        if link:
            parent=session.get(Resource,link.parent_pk);dependency(parent.kind,parent.resource_id)
        elif row.kind.startswith('knowledge_') and row.kind!='knowledge_base' and value.get('knowledge_base_id'):
            dependency('knowledge_base',value['knowledge_base_id'])
        if row.kind=='knowledge_version' and value.get('resource_kind') in DATA_KINDS and value.get('resource_id')!=row.resource_id:
            dependency(value['resource_kind'],value['resource_id'])
            if value.get('status')!='published':raise AppError('APP_DEPENDENCY_INVALID','知识依赖必须固定已发布版本',422)
            if value.get('file_id'):dependency('document' if value['file_id'].startswith('document_') else 'knowledge_file',value['file_id'])
        if row.kind=='knowledge_batch':
            if value.get('dataset_id'):dependency('knowledge_dataset',value['dataset_id'])
            if value.get('published_version_id'):dependency('knowledge_version',value['published_version_id'])
            if value.get('file_id'):dependency('knowledge_file',value['file_id'])
        if row.kind=='data_table_version':
            # New table versions have a separate exact original file. Its ACL
            # remains an AND layer at query/export time, so it must also be an
            # explicit immutable release dependency and client grant.
            if value.get('status')!='published' or not all(isinstance(value.get(field),str) and value[field] for field in ('table_id','database_id','file_id','source_sha256')):
                raise AppError('APP_DEPENDENCY_INVALID','数据表依赖必须固定完整的已发布版本',422)
            dependency('data_table',value['table_id'])
            dependency('database',value['database_id'])
            dependency('data_file',value['file_id'])
            table_value=get_resource(session,'data_table',value['table_id'],context)
            original_file=get_resource(session,'data_file',value['file_id'],context)
            if table_value.get('database_id')!=value['database_id'] or (original_file.get('table_id'),original_file.get('database_id'),original_file.get('sha256'))!=(value['table_id'],value['database_id'],value['source_sha256']):
                raise AppError('SOURCE_HASH_MISMATCH','数据表发布版本与精确导入来源不一致',409)
        table=session.get(DataTableBinding,row.id)
        if table:
            parent=session.get(Resource,table.database_pk);dependency(parent.kind,parent.resource_id)
        if row.kind=='model_configuration':dependency('model_provider',value['provider_id'])
    def knowledge_pins(selected):
        from app.knowledge.service import source_root,published_version
        pins=[]
        for identifier in selected:
            if identifier.startswith('knowledge_base_'):
                roots=[root for kind in ('knowledge_document','knowledge_dataset') for root in list_resources(session,kind,context)
                    if root.get('knowledge_base_id')==identifier and root.get('status')=='published']
            else:roots=[source_root(identifier,context,session)[2]]
            for root in roots:
                version=published_version(root,context,session)
                dependency('knowledge_version',version['id'])
                pin={'file_id':version['file_id'],'knowledge_base_id':root['knowledge_base_id'],'version_id':version['id']}
                if pin not in pins:pins.append(pin)
        if not pins or len(pins)>20:raise AppError('APP_DEPENDENCY_INVALID','应用知识范围需包含1至20个明确已发布版本',422)
        return pins
    def bindings(config):
        selected=[item['resource_id'] for item in config.get('knowledge_resources',[]) if item.get('enabled',True)]
        if selected:config['_application_knowledge_pins']=knowledge_pins(selected)
        fixed=[]
        for item in config.get('data_resources',[]):
            if not item.get('enabled',True):continue
            if item['kind']=='database':
                from app.knowledge.database_service import tables
                candidates=tables(database_id=item['resource_id'],context=context,session=session)['items']
                dependency('database',item['resource_id'])
            else:candidates=[get_resource(session,'data_table',item['resource_id'],context)]
            for table in candidates:
                version_id=item.get('version_id') or table.get('published_version_id')
                if not version_id:raise AppError('APP_DEPENDENCY_INVALID','应用数据表需要固定已发布版本',422)
                fixed.append({'kind':'data_table','resource_id':table['id'],'version_id':version_id,'enabled':True})
                dependency('data_table',table['id']);dependency('data_table_version',version_id)
        if fixed:config['data_resources']=fixed
        for binding in config.get('bindings',[]):
            dependency('plugin_version',binding['plugin_version_id'])
            if binding.get('connection_id'):dependency('connection',binding['connection_id'])
        for item in agent_sources(config):dependency(item['kind'],item['resource_id'])
    kind=source.get('kind');identifier=source.get('id');version=source.get('version_id')
    if kind=='workflow':
        original=get_resource(session,'workflow',identifier,context);dependency('workflow',identifier)
        workflow=deepcopy(original)
    elif kind=='agent':
        if not version:raise AppError('APP_PUBLISHED_VERSION_REQUIRED','应用必须固定已发布智能体版本',422)
        published=get_resource(session,'agent_version',version,context)
        if published.get('status')!='published' or published.get('agent_id')!=identifier:raise AppError('APP_PUBLISHED_VERSION_REQUIRED','智能体发布版本不可用',422)
        dependency('agent',identifier);dependency('agent_version',version)
        original=deepcopy(published);root_config=get_agent_config(agent_id=identifier,agent_version_id=version,context=context,session=session)
        bindings(root_config)
        if root_config.get('main_workflow_id'):
            dependency('workflow',root_config['main_workflow_id']);workflow=deepcopy(published['workflow_snapshot'])
        else:
            if root_config.get('config_version')!='2.0':raise AppError('APP_SOURCE_UNSUPPORTED','请先将智能体发布为2.0可持久化执行版本',422)
            structured=isinstance(root_config.get('output_schema',{}).get('properties'),dict)
            workflow={'schemaVersion':'2.0','name':published.get('name','Application'),'mode':root_config.get('planner_mode','demo'),
                'input_schema':root_config.get('input_schema',{'type':'object','properties':{}}),
                'output_schema':{'type':'object','properties':{'result':root_config['output_schema']} if structured else {'text':{'type':'string'}},'required':['result'] if structured else ['text'],'additionalProperties':False},
                'nodes':[{'id':'start','type':'start'},{'id':'agent','type':'agent','config':{'agent_id':identifier,'agent_version_id':version},
                    'inputs':{name:{'$ref':'nodes.start.'+name} for name in ('prompt','query','inputs','message')}},
                    {'id':'answer','type':'answer','inputs':{'result':{'$ref':'nodes.agent.structured_output'}} if structured else {'text':{'$ref':'nodes.agent.text'}}}],
                'edges':[{'source':'start','target':'agent'},{'source':'agent','target':'answer'}]}
            agents['root/agent']=deepcopy(root_config)
    else:raise AppError('APP_SOURCE_UNSUPPORTED','应用来源类型无效',422)
    if workflow.get('schemaVersion')!='2.0':raise AppError('APP_SOURCE_UNSUPPORTED','应用需要已保存的2.0持久化流程',422)
    def walk(definition,scope='root'):
        for node in definition.get('nodes',[]):
            config=node.get('config',{});node_type=node['type']
            if node_type=='agent':
                if not config.get('agent_version_id'):raise AppError('APP_PUBLISHED_VERSION_REQUIRED','流程内智能体必须固定已发布版本',422)
                dependency('agent',config['agent_id']);dependency('agent_version',config['agent_version_id'])
                item=get_resource(session,'agent_version',config['agent_version_id'],context)
                if item.get('status')!='published':raise AppError('APP_PUBLISHED_VERSION_REQUIRED','流程内智能体版本必须已发布',422)
                frozen=get_agent_config(agent_id=config['agent_id'],agent_version_id=config['agent_version_id'],context=context,session=session)
                if frozen.get('main_workflow_id'):raise AppError('APP_SOURCE_UNSUPPORTED','流程内嵌套主流程智能体尚不可发布为API',422)
                agents[scope+'/'+node['id']]=frozen;bindings(frozen)
            if node_type in {'plugin','http'}:
                binding=config['binding'];dependency('plugin_version',binding['plugin_version_id'])
                if binding.get('connection_id'):dependency('connection',binding['connection_id'])
            if node_type=='query':
                sources=node.get('inputs',{}).get('sources')
                query=node.get('inputs',{}).get('query') or {}
                if not isinstance(query,dict) or sources is not None and not isinstance(sources,list):raise AppError('APP_DEPENDENCY_INVALID','应用财务查询来源必须固定',422)
                batch=query.get('batch_id')
                if sources is None and not batch:raise AppError('APP_DEPENDENCY_INVALID','应用财务查询需要明确固定来源',422)
                for item in ([{'type':'batch','batch_id':batch}] if batch else sources):
                    if not isinstance(item,dict):raise AppError('APP_DEPENDENCY_INVALID','应用财务查询来源必须固定',422)
                    if item.get('type')=='batch':
                        identifier=item.get('batch_id')
                        if not isinstance(identifier,str) or not identifier.startswith('knowledge_batch_'):raise AppError('APP_SOURCE_UNSUPPORTED','应用财务批次需使用已发布的受治理知识批次',422)
                        dependency('knowledge_batch',identifier)
                    elif item.get('type')=='plugin':
                        binding=item.get('binding',{});dependency('plugin_version',binding.get('plugin_version_id'))
                        if binding.get('connection_id'):dependency('connection',binding['connection_id'])
                    else:raise AppError('APP_DEPENDENCY_INVALID','应用财务查询来源类型无效',422)
            if node_type in {'iteration','loop'}:walk(config['body'],scope+'/'+node['id']+'/'+node_type)
            if node_type=='database_query':
                table=node.get('inputs',{}).get('table_id')
                if not isinstance(table,str):raise AppError('APP_DEPENDENCY_INVALID','应用结构化查询必须固定数据表',422)
                dependency('data_table',table)
                if config.get('database_id'):dependency('database',config['database_id'])
                if not config.get('table_version_id'):config['table_version_id']=get_resource(session,'data_table',table,context).get('published_version_id')
                if not config.get('table_version_id'):raise AppError('APP_DEPENDENCY_INVALID','应用数据表需要固定已发布版本',422)
                dependency('data_table_version',config['table_version_id'])
            if node_type=='knowledge_retrieval' and not node.get('inputs',{}).get('version_ids'):
                files=node.get('inputs',{}).get('file_ids',[]);libraries=node.get('inputs',{}).get('knowledge_base_ids',[])
                if not isinstance(files,list) or not isinstance(libraries,list):raise AppError('APP_DEPENDENCY_INVALID','应用知识来源必须固定',422)
                selected=files+libraries
                if any(not isinstance(v,str) for v in selected):raise AppError('APP_DEPENDENCY_INVALID','应用知识来源必须固定',422)
                node['inputs']['version_ids']=[pin['version_id'] for pin in knowledge_pins(selected)]
            # All configured resource identities are fixed. Runtime references
            # are permitted only for partition-bound message attachments.
            for field,resource_kind in (('file_id','document'),('file_ids','document'),('knowledge_base_ids','knowledge_base'),('version_ids','knowledge_version')):
                values=node.get('inputs',{}).get(field,config.get(field))
                if field=='file_id' and isinstance(values,str):values=[values]
                if isinstance(values,list):
                    for value in values:
                        if isinstance(value,str):dependency(resource_kind,value)
                        else:raise AppError('APP_DEPENDENCY_INVALID','发布资源选择必须固定',422)
                elif values is not None:raise AppError('APP_DEPENDENCY_INVALID','发布资源选择必须固定',422)
    walk(workflow)
    definition=WorkflowDefinition.model_validate(workflow);validate_workflow(definition)
    payload={'workflow_snapshot':definition.model_dump(mode='json'),'agent_snapshots':agents}
    pin_run_models(payload,context=context,session=session)
    for model_id in payload['model_bindings']:dependency('model_configuration',model_id)
    def token_ceiling(definition,scope='root'):
        total=0
        for node in definition.get('nodes',[]):
            config=node.get('config',{});kind=node['type']
            if kind in {'llm','parameter_extractor','question_classifier'} and config.get('mode')!='demo':total+=config.get('context_budget',8192)
            if kind=='agent':
                selected=payload['agent_snapshots'][scope+'/'+node['id']]
                if selected.get('planner_mode')=='model':total+=selected.get('context_budget',8192)*selected.get('max_rounds',4)
            if kind in {'iteration','loop'}:total+=config['max_iterations']*token_ceiling(config['body'],scope+'/'+node['id']+'/'+kind)
        return total
    ceiling=token_ceiling(payload['workflow_snapshot'])
    if ceiling>runtime_policy['token_reservation']:raise AppError('APP_TOKEN_RESERVATION_REQUIRED',f'此固定发布最多需要预留{ceiling}个令牌，请提高每次调用令牌预留或降低来源执行预算',422,{'required_token_reservation':ceiling})
    payload['workflow_snapshot']['limits']['timeout_seconds']=min(payload['workflow_snapshot']['limits'].get('timeout_seconds',120),runtime_policy['timeout_seconds'])
    from app.application_schema import output_schema as infer_output_schema
    input_value=deepcopy(workflow.get('input_schema',{'type':'object'}));input_value.setdefault('properties',{});input_value.setdefault('additionalProperties',False)
    input_schema=_schema(input_value,'输入')
    output_schema=_schema(infer_output_schema(workflow,agents),'输出')
    from app.application_files import contains_file_schema
    if contains_file_schema(output_schema):
        if not runtime_policy.get('allowed_file_types'):raise AppError('APP_FILE_POLICY_REQUIRED','来源包含文件输出，请在发布策略明确允许相应文件类型',422)
        from app.data_access import authorize_data_resource,_delegable,_can_delegate,RowScope,Columns
        for item in refs.values():
            if item['kind'] not in DATA_KINDS:continue
            manager=authorize_data_resource(session=session,context=context,kind=item['kind'],resource_id=item['resource_id'],action='manage')
            decision=authorize_data_resource(session=session,context=context,kind=item['kind'],resource_id=item['resource_id'],action='export_detail')
            if 'export_detail' not in _delegable(manager) or not _can_delegate(decision,RowScope().model_dump(),Columns().model_dump()):raise AppError('APP_EXPORT_GRANT_REQUIRED','生成文件来源缺少可委托的明细导出权限',403)
            item['actions'].append('export_detail')
    return {'schema_version':'application-release-v1',**payload,'dependency_manifest':sorted(refs.values(),key=lambda v:(v['kind'],v['resource_id'])),
        'agent_data_scope':root_config.get('data_resources',[]) if root_config else [],'agent_knowledge_scope':[{'kind':'knowledge_base' if item['resource_id'].startswith('knowledge_base_') else 'document','resource_id':item['resource_id']} for item in root_config.get('knowledge_resources',[]) if item.get('enabled',True)] if root_config else [],
        'scoped_bindings':root_config.get('bindings',[]) if root_config else [],'input_schema':input_schema,'output_schema':output_schema,
        'runtime_policy':deepcopy(runtime_policy),'source_identity':{'kind':kind,'id':identifier,'version_id':version,'source_sha256':digest(original)}}


def authorize_resources(session,*,context,principal_id,application_id,version_id,bundle,resource_constraints):
    from app.auth import validate_context
    from app.rbac import require_permission
    from app.machine_models import ServicePrincipal,ServiceResourceGrant
    from app.machine_auth import delegation_anchor_current
    from app.data_access import DATA_KINDS,authorize_data_resource,_delegable,_can_delegate,RowScope,Columns,bump_policy_version
    from app.data_models import DataGrant
    user=validate_context(context,session);require_permission(context,session,'applications.keys.manage')
    if context.principal_type!='human':raise AppError('PERMISSION_DENIED','授权需要人类管理会话',403)
    principal=session.get(ServicePrincipal,principal_id,populate_existing=True)
    if principal is None or (principal.application_id,principal.tenant_id,principal.workspace_id)!=(application_id,context.tenant_id,context.workspace_id):raise AppError('NOT_FOUND','机器主体不存在',404)
    if hasattr(bundle,'model_dump'):bundle=bundle.model_dump(mode='json')
    if hasattr(resource_constraints,'model_dump'):resource_constraints=resource_constraints.model_dump(mode='json')
    constraints={(v['kind'],v['resource_id']):v for v in resource_constraints['resources']}
    if len(constraints)!=len(resource_constraints['resources']):raise AppError('APP_CONSTRAINT_INVALID','资源范围重复',422)
    for dependency in bundle['dependency_manifest']:
        key=(dependency['kind'],dependency['resource_id']);selected=constraints.get(key)
        if selected is None or 'use' not in selected['actions']:raise AppError('APP_RESOURCE_GRANT_REQUIRED','客户端需逐项选择发布依赖的显式授权',403)
        value=get_resource(session,*key,context)
        row=session.scalar(select(Resource).where(Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,Resource.kind==key[0],Resource.resource_id==key[1]))
        if row is None or not delegation_anchor_current(session,row,user.id,context):raise AppError('DATA_DELEGATION_DENIED','当前身份不能转授此发布依赖',403)
        mapping={'use':'use','read':'use','download_original':'download_original','export':'export_detail'}
        actions=sorted({mapping[action] for action in selected['actions']})
        if not set(actions)<=set(dependency.get('actions',['use'])):raise AppError('APP_CONSTRAINT_INVALID','授权动作超出发布依赖范围',403)
        if row.kind in DATA_KINDS:
            manager=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action='manage')
            if not set(actions)<=_delegable(manager):raise AppError('DATA_DELEGATION_DENIED','当前身份不能转授所选数据动作',403)
            scope=RowScope().model_dump();columns=Columns().model_dump()
            for action in actions:
                own=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action=action)
                if not _can_delegate(own,scope,columns):raise AppError('DATA_DELEGATION_DENIED','当前客户端DTO不支持此受限行列转授，请先建立精确服务授权',403)
            # Append only this principal, never replace other subjects' rules.
            session.add(DataGrant(id=new_id('data_grant'),resource_pk=row.id,subject_type='service',subject_id=principal_id,effect='allow',
                actions=json.dumps(actions),row_scope=json.dumps(scope),columns=json.dumps(columns),delegable_actions='[]',created_by=user.id))
        session.add(ServiceResourceGrant(id=new_id('svcgrant'),principal_id=principal_id,version_id=version_id,resource_pk=row.id,actions=json.dumps(actions),
            active=True,revision=1,created_by=user.id,created_at=utc_now()))
    principal.allowed_entities=json.dumps([value for value in resource_constraints.get('allowed_entities',[]) if value in context.allowed_entities])
    bump_policy_version(session,context);session.flush()


def public_base_url():
    """One trusted URL validator for options and authenticated contract export."""
    from app.settings import settings
    from urllib.parse import urlsplit
    configured=settings.application_public_base_url
    if not configured:return None,'not_configured'
    try:
        parts=urlsplit(configured)
        valid_port=parts.port is None or 1<=parts.port<=65535
    except ValueError:raise AppError('APP_BASE_URL_INVALID','服务器应用调用地址配置无效',503) from None
    secure=parts.scheme=='https';local=parts.scheme=='http' and parts.hostname in {'127.0.0.1','localhost','::1'} and settings.development_http
    if not valid_port or not parts.hostname or not (secure or local) or any(c.isspace() or ord(c)<32 for c in configured) or parts.username or parts.password or parts.query or parts.fragment or parts.path!='/api/open/v1':
        raise AppError('APP_BASE_URL_INVALID','服务器应用调用地址配置无效',503)
    return configured,'configured' if secure else 'development'


def client_options(session,*,context,application_id,version_id,bundle):
    """Read-only offer; client creation independently repeats every check."""
    from app.auth import validate_context
    from app.rbac import require_permission
    from app.machine_auth import delegation_anchor_current
    from app.data_access import DATA_KINDS,authorize_data_resource,_delegable,_can_delegate,RowScope,Columns
    validate_context(context,session);require_permission(context,session,'applications.keys.manage')
    if context.principal_type!='human':raise AppError('PERMISSION_DENIED','可授范围需要人类管理会话',403)
    result=[];blockers=[]
    if len(bundle['dependency_manifest'])>256:blockers.append({'code':'APP_RESOURCE_LIMIT','message':'发布依赖数量超出客户端授权上限'})
    else:
        for item in bundle['dependency_manifest']:
            try:
                value=get_resource(session,item['kind'],item['resource_id'],context)
                row=session.scalar(select(Resource).where(Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id,
                    Resource.kind==item['kind'],Resource.resource_id==item['resource_id']))
                if row is None or not delegation_anchor_current(session,row,context.operator_id,context):continue
                if row.kind in DATA_KINDS:
                    manager=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action='manage')
                    own=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action='use')
                    if not set(item.get('actions',['use']))<=_delegable(manager) or not _can_delegate(own,RowScope().model_dump(),Columns().model_dump()):continue
                    if 'export_detail' in item['actions']:
                        exported=authorize_data_resource(session=session,context=context,kind=row.kind,resource_id=row.resource_id,action='export_detail')
                        if not _can_delegate(exported,RowScope().model_dump(),Columns().model_dump()):continue
                offered=['export' if action=='export_detail' else action for action in item.get('actions',['use'])]
                constraint={'kind':row.kind,'resource_id':row.resource_id,'actions':offered}
                if item.get('version_id'):constraint['version_id']=item['version_id']
                label=str(value.get('name') or value.get('title') or row.resource_id)[:150]
                if 'export' in offered:label=label[:135]+'（调用、导出）'
                result.append({'label':label,'constraint':constraint,'required':True})
            except AppError as exc:
                if exc.status_code not in {403,404,409}:raise
                # Do not expose an inaccessible dependency's name or identity.
    url,status=public_base_url()
    # Existing runtime framing is synthetic unless the fixed source explicitly
    # selects an authorized entity. Never list every account/tenant entity.
    entities={'SYNTHETIC-DEMO'}
    def visit(definition):
        for node in definition.get('nodes',[]):
            for value in (node.get('inputs',{}).get('query',{}),node.get('config',{}).get('arguments',{})):
                if isinstance(value,dict) and isinstance(value.get('entity'),str):entities.add(value['entity'])
            if node.get('type') in {'iteration','loop'}:visit(node['config']['body'])
    visit(bundle['workflow_snapshot'])
    return {'resource_options':result,'allowed_entities':sorted(entities&set(context.allowed_entities)),'blockers':blockers,
        'public_base_url':url,'public_base_url_status':status}
