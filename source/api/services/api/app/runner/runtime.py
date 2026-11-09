"""Bounded DAG executor; every node kind has real dispatch or explicit rejection."""
import asyncio
import json
from decimal import localcontext
from copy import deepcopy
from app.contracts import WorkflowDefinition, FactQuery, ToolBinding, ToolExecutionResult, FinancialFact, ResultDataset, Artifact
from app.db import save_resource
from app.errors import AppError
from app.runner.graph import validate_graph, TERMINALS, STARTS, TRIGGERS
from app.runner.registry import definition, node_registry
from app.runner.variables import resolve, matches, template, decimal, check_schema, check_size, check_variable

class GraphRuntime:
    def __init__(self, *, request, run, context, session, invoke, event, persist):
        self.request,self.run,self.context,self.session=request,run,context,session
        self.invoke,self.event,self.persist=invoke,event,persist
        self.steps=0
        self.max_steps=256
        self.status={x["type"]:x for x in node_registry()["items"]}

    async def execute(self,workflow, *, scope=None,prefix="",depth=0):
        ordered=validate_graph(workflow,depth,scope[0] if scope else None)
        if depth==0:
            self.max_steps=workflow.limits.get("max_steps",256)
            check_schema(self.request.inputs,workflow.input_schema,"workflow inputs")
        pool={"inputs":deepcopy(self.request.inputs),"nodes":{},"vars":{name:deepcopy(decl["default"]) for name,decl in workflow.variables.items() if "default" in decl}}
        if scope: pool[scope[0]]=scope[1]
        active_edges={};outputs={}
        incoming={node.id:[] for node in ordered}
        for edge in workflow.edges: incoming[edge.target].append(edge)
        for node in ordered:
            from app.rbac import require_any_permission,RUN_PERMISSIONS
            require_any_permission(self.context,self.session,RUN_PERMISSIONS)
            from app.data_access import require_derivation_access
            require_derivation_access(session=self.session,context=self.context,kind="run",resource_id=self.run["id"])
            self.steps+=1
            if self.steps>self.max_steps: raise AppError("RESOURCE_LIMIT","Global execution step limit exceeded")
            await asyncio.sleep(0)  # cancellation even on CPU-only nodes
            node_key=prefix+node.id
            edges=incoming[node.id]
            flags=[active_edges[(e.source,e.target,e.source_port)] for e in edges]
            enabled=node.type in STARTS or (all(flags) if node.config.get("join","any")=="all" else any(flags))
            if node.type in TRIGGERS:enabled=node.type=="trigger_"+self.run["message"]["type"]
            selected=None
            if not enabled:
                self.event("node.skipped",node_key,"skipped",{"reason":"inactive branch"})
            else:
                self.event("node.started",node_key,"running",{"node_type":node.type,"scope":prefix})
                try:
                    if self.status[node.type]["status"]=="unavailable":
                        raise AppError("NODE_UNAVAILABLE",self.status[node.type]["reason"],503)
                    values=resolve(node.inputs,pool)
                    check_schema(values,definition(node.type)["input_schema"],node.type+" inputs")
                    result,selected=await self.dispatch(node,values,pool,workflow,node_key,depth)
                    check_size(result,1048576,"node output")
                    check_schema(result,definition(node.type)["output_schema"],node.type+" outputs")
                    pool["nodes"][node.id]=result
                    check_size(pool,4194304,"variable pool")
                    if node.type in TERMINALS and node.type!="end": outputs.update(result["values"])
                    if node.type=="output" and depth==0:self.run.setdefault("presentations",[]).append({"node_id":node_key,**result["presentation"]})
                    if depth==0:
                        self.run["variable_pool"]=pool
                        self.run["outputs"]=outputs
                        self.persist()
                    self.event("node.completed",node_key,"succeeded",{"outputs":result,"selected_port":selected})
                except asyncio.CancelledError: raise
                except Exception as exc:
                    if isinstance(exc, TimeoutError):
                        exc = AppError("RUN_TIMEOUT", "Node/repetition deadline exceeded", 504)
                    error=exc.as_dict() if isinstance(exc,AppError) else {"code":"INTERNAL_ERROR","message":"Node execution failed"}
                    self.event("node.failed",node_key,"failed",{"error":error})
                    raise exc
            for edge in workflow.edges:
                if edge.source==node.id:
                    active_edges[(edge.source,edge.target,edge.source_port)]=enabled and (selected is None or selected==edge.source_port)
        return {"outputs":outputs,"pool":pool}

    async def dispatch(self,node,values,pool,workflow,node_key,depth):
        from app.runner.fields import service_config,business
        kind=node.type;config=service_config(node)
        common={"context":self.context,"session":self.session}
        if kind in STARTS:
            from app.runner.fields import business
            if business(workflow,node):return deepcopy(pool['inputs']),None
            return {"inputs":pool["inputs"],"query":self.request.query.model_dump(mode="json"),"prompt":self.request.prompt,"message":self.run.get("message",{})},None
        if kind in {'transform','output','answer','end'} and 'output_mapping' in node.config and not (kind in TERMINALS and business(workflow,node) and node.config['output_mapping']=={}):values=resolve(node.config['output_mapping'],{'fields':values})
        if kind=="transform":return deepcopy(values),None
        if kind=="output":
            presentation={"kind":config.get("presentation","auto")}
            if presentation["kind"]=="json": presentation["json"]=deepcopy(values)
            if "text" in values:
                if not isinstance(values["text"],str):raise AppError("VARIABLE_TYPE_MISMATCH","Output text must be string")
                presentation["text"]=values["text"]
            if "table" in values:
                table=values["table"]
                if not isinstance(table,dict) or not isinstance(table.get("rows"),list) or not isinstance(table.get("columns"),list) or len(table["rows"])>1000 or len(table["columns"])>32:raise AppError("VARIABLE_TYPE_MISMATCH","Output table requires bounded rows/columns")
                presentation["table"]=table
            if "files" in values:
                from app.db import get_resource
                if not isinstance(values["files"],list) or len(values["files"])>16:raise AppError("VARIABLE_TYPE_MISMATCH","Output files requires at most16 controlled Artifact IDs")
                files=[get_resource(self.session,"artifact",aid,self.context) for aid in values["files"]]
                from app.reporting.document_artifacts import DocumentArtifactStored,DocumentArtifactPublic
                presentation["files"]=[DocumentArtifactStored.model_validate(item).model_dump(mode="json",include=set(DocumentArtifactPublic.model_fields))
                    if item.get('artifact_kind')=='generated_document' else {key:value for key,value in item.items() if key!="path"} for item in files]
            return {"values":values,"presentation":presentation},None
        if kind in TERMINALS:return {"values":values},None
        if kind=="if_else":
            if "conditions" in values:
                from app.runner.variables import condition_result
                result=condition_result(values["conditions"],config["logical_operator"])
            else: result=matches(values["left"],config["operator"],values.get("right"))
            return {"result":result},"true" if result else "false"
        if kind=="template": return {"text":template(config["template"],values)},None
        if kind=="assign":
            name=config["name"];value=deepcopy(values["value"]);operation=config.get("operation","set")
            if operation=="append":
                previous=pool["vars"].get(name,[])
                if not isinstance(previous,list): raise AppError("VARIABLE_TYPE_MISMATCH","append target must be array")
                value=previous+[value]
            elif operation=="increment":
                from app.runner.fields import business
                if business(workflow,node):
                    before=pool['vars'].get(name,0)
                    check_schema(before,{'type':'number'},'increment target');check_schema(value,{'type':'number'},'increment value')
                    value=before+value
                else:
                    with localcontext() as ctx:
                        ctx.prec=60
                        value=format(decimal(pool["vars"].get(name,"0"))+decimal(value),"f")
            from app.runner.fields import business
            if business(workflow,node):check_schema(value,{'type':workflow.variables[name]['type']},'variable assignment')
            else:check_variable(value,workflow.variables[name])
            pool["vars"][name]=value
            return {"value":value},None
        if kind=="aggregate":
            items=[v for v in values["values"] if v is not None];strategy=config.get("strategy","first_non_null")
            if strategy=="first_non_null":
                if not items: raise AppError("VARIABLE_NOT_FOUND","No active branch value to aggregate")
                value=items[0]
            elif strategy=="collect": value=items
            elif strategy=="concat":
                if not all(isinstance(item,list) for item in items): raise AppError("VARIABLE_TYPE_MISMATCH","concat requires lists")
                value=[item for array in items for item in array]
            else:
                value={}
                for item in items:
                    if not isinstance(item,dict) or value.keys() & item.keys(): raise AppError("VARIABLE_TYPE_MISMATCH","merge_objects requires non-overlapping objects")
                    value.update(item)
            return {"value":value},None
        if kind=="list":
            items=deepcopy(values["items"])
            if len(items)>1000: raise AppError("RESOURCE_LIMIT","List operator allows at most1000 items")
            operation=config["operation"];offset=config.get("offset",0);limit=config.get("limit",1000)
            if operation in ("take","slice"): items=items[offset:offset+limit]
            elif operation=="unique":
                seen=set();unique=[]
                for item in items:
                    key=json.dumps(item,ensure_ascii=False,sort_keys=True,allow_nan=False)
                    if key not in seen: seen.add(key);unique.append(item)
                items=unique
            elif operation=="flatten":
                if not all(isinstance(item,list) for item in items): raise AppError("VARIABLE_TYPE_MISMATCH","flatten expects arrays")
                items=[item for row in items for item in row]
            elif operation in ("sort","filter"):
                field=config.get("field")
                def key(item):
                    if field:
                        if not isinstance(item,dict) or field not in item: raise AppError("VARIABLE_NOT_FOUND","List field unavailable")
                        return item[field]
                    return item
                if operation=="sort":
                    # Decimal numeric text sorts numerically, other text lexically. No float arithmetic.
                    keys=[key(item) for item in items]
                    try:
                        for item in keys: decimal(item)
                        items=sorted(items,key=lambda item:decimal(key(item)),reverse=config.get("descending",False))
                    except AppError:
                        if not all(isinstance(item,str) for item in keys): raise AppError("VARIABLE_TYPE_MISMATCH","Sort keys must be comparable text/decimals")
                        items=sorted(items,key=key,reverse=config.get("descending",False))
                else: items=[item for item in items if matches(key(item),config.get("operator","eq"),values.get("value"))]
            if len(items)>1000: raise AppError("RESOURCE_LIMIT","List output exceeds1000 items")
            return {"items":items,"count":len(items)},None
        if kind in ("plugin","http"):
            if values.get("message") is not None:
                wrapped=await self.invoke("app.agents.nodes","execute_node",node_type=kind,config=config,inputs=values,run_id=self.run["id"],**common)
                return ToolExecutionResult.model_validate(wrapped["outputs"]).model_dump(mode="json"),None
            arguments=values.get("arguments",config.get("arguments",{}))
            tool=ToolExecutionResult.model_validate(await self.invoke("app.plugins.service","execute_tool",binding=ToolBinding.model_validate(config["binding"]),arguments=arguments,**common))
            return tool.model_dump(mode="json"),None
        if kind=="query":
            query=FactQuery.model_validate(values.get("query",self.request.query.model_dump(mode="json")))
            facts=[]
            if "sources" not in values:
                if not query.batch_id: raise AppError("VALIDATION_ERROR","v2 query requires explicit batch_id or selected sources")
                facts=await self.invoke("app.finance.service","query_facts",query=query,**common)
            else:
                if query.batch_id: raise AppError("VALIDATION_ERROR","Select sources or query.batch_id, not both")
                if not 1<=len(values["sources"])<=16: raise AppError("RESOURCE_LIMIT","Select1..16 financial sources")
                for source in values["sources"]:
                    if source.get("type")=="batch":
                        source_query=query.model_copy(update={"batch_id":source["batch_id"]})
                        facts.extend(await self.invoke("app.finance.service","query_facts",query=source_query,**common))
                    elif source.get("type")=="plugin":
                        arguments={"entity":query.entity,"periods":query.periods,**source.get("arguments",{})}
                        tool=ToolExecutionResult.model_validate(await self.invoke("app.plugins.service","execute_tool",binding=ToolBinding.model_validate(source["binding"]),arguments=arguments,**common))
                        facts.extend(tool.facts)
                    else: raise AppError("VALIDATION_ERROR","Unknown financial source type")
            facts=[FinancialFact.model_validate(f).model_dump(mode="json") for f in facts]
            # Validate duplicates and context immediately even if downstream skips calculation.
            from app.finance.domain import build_result, FinanceValidationError
            try: build_result(facts,query.model_dump(mode="json"))
            except FinanceValidationError as exc:
                code="CONFLICT" if "CONFLICT" in str(exc) else "FINANCIAL_CONTEXT_MISMATCH"
                raise AppError(code,str(exc),409 if code=="CONFLICT" else 422) from exc
            return {"facts":facts,"source_refs":sorted({f["source_ref"] for f in facts})},None
        if kind=="calculate":
            query=FactQuery.model_validate(values.get("query",self.request.query.model_dump(mode="json")))
            facts=[FinancialFact.model_validate(f) for f in values["facts"]]
            dataset=ResultDataset.model_validate(await self.invoke("app.finance.service","calculate_dataset",facts=facts,query=query,**common))
            payload=dataset.model_dump(mode="json")
            save_resource(self.session,"dataset",dataset.id,payload,self.context)
            if self.run.get("data_sources"):
                from app.data_access import record_derivation
                record_derivation(session=self.session,context=self.context,kind="dataset",resource_id=dataset.id,sources=self.run["data_sources"])
            self.run["dataset_id"]=dataset.id
            self.run.setdefault("dataset_ids",[]).append(dataset.id)
            return {"dataset":payload,"dataset_id":dataset.id,"snapshot_id":dataset.snapshot_id},None
        if kind=="chart":
            dataset=ResultDataset.model_validate(values["dataset"])
            from app.db import get_resource
            get_resource(self.session,"dataset",dataset.id,self.context)
            from app.reporting.service import validate_dataset
            validate_dataset(dataset=dataset,context=self.context)
            spec={"type":"bar","dataset_id":dataset.id,"snapshot_id":dataset.snapshot_id,"metric_versions":getattr(dataset,"metric_versions",{}),"source_refs":dataset.source_refs,"x":"period","series":config.get("series",["revenue","net_profit"])}
            self.run["chart_spec"]=spec
            return {"chart_spec":spec},None
        if kind=="report":
            dataset=ResultDataset.model_validate(values["dataset"])
            from app.db import get_resource
            get_resource(self.session,"dataset",dataset.id,self.context)
            export_sources=[{**source,"action":"export_detail"} for source in self.run.get("data_sources",[])]
            if export_sources:
                from app.data_access import authorize_data_resource
                for source in export_sources:authorize_data_resource(session=self.session,context=self.context,kind=source["kind"],resource_id=source["resource_id"],action="export_detail")
            artifacts=await self.invoke("app.reporting.service","generate_artifacts",dataset=dataset,run_id=self.run["id"],**common)
            public=[]
            for item in artifacts:
                artifact=Artifact.model_validate(item)
                if artifact.run_id!=self.run["id"] or artifact.dataset_id!=dataset.id: raise AppError("CONFLICT","Artifact ownership mismatch")
                data=artifact.model_dump(mode="json")
                save_resource(self.session,"artifact",artifact.id,data,self.context)
                if export_sources:
                    from app.data_access import record_derivation
                    record_derivation(session=self.session,context=self.context,kind="artifact",resource_id=artifact.id,sources=export_sources)
                self.run["artifact_ids"].append(artifact.id)
                public.append({k:v for k,v in data.items() if k!="path"})
            return {"artifacts":public,"artifact_ids":[a["id"] for a in public],"dataset_id":dataset.id},None
        if kind in ("iteration","loop"):
            body=WorkflowDefinition.model_validate(config["body"]);maximum=config["max_iterations"]
            if kind=="iteration":
                items=values["items"]
                if len(items)>maximum: raise AppError("ITERATION_LIMIT","Input array exceeds declared iteration bound")
                state=None;results=[];count=len(items)
            else:
                state=deepcopy(values.get("state",resolve(config.get("state",{}),pool)));count=maximum;results=[]
            async with asyncio.timeout(config["timeout_seconds"]):
                for index in range(count):
                    self.event("iteration.started",node_key,"running",{"index":index,"kind":kind})
                    child_scope={"item":items[index],"index":index} if kind=="iteration" else {"state":state,"index":index}
                    child=await self.execute(body,scope=(kind,child_scope),prefix=f"{node_key}[{index}].",depth=depth+1)
                    self.event("iteration.completed",node_key,"succeeded",{"index":index,"kind":kind})
                    if kind=="iteration": results.append(child["outputs"])
                    else:
                        state=child["outputs"]
                        condition=resolve(config["until"],{"loop":{"state":state,"index":index}})
                        if matches(condition["left"],condition["operator"],condition.get("right")):
                            return {"state":state,"count":index+1},None
            if kind=="loop": raise AppError("ITERATION_LIMIT","Loop hit bound before termination condition")
            return {"items":results,"count":count},None
        service=definition(kind).get("service")
        if service:
            if kind=="agent":
                from app.data_access import agent_sources,record_derivation
                selected_config=await self.invoke("app.agents.service","get_agent_config",agent_id=config["agent_id"],agent_version_id=config.get("agent_version_id"),context=self.context,session=self.session)
                configured_sources=agent_sources(selected_config)
                if configured_sources:
                    record_derivation(session=self.session,context=self.context,kind="run",resource_id=self.run["id"],sources=configured_sources)
                    existing=self.run.setdefault("data_sources",[])
                    for source in configured_sources:
                        if source not in existing:existing.append(source)
            if kind=="database_query":
                from app.data_access import authorize_data_resource
                authorize_data_resource(session=self.session,context=self.context,kind="data_table",resource_id=values["table_id"],agent_scope=self.run.get("agent_data_scope"))
            if kind in ("knowledge_retrieval","document_extractor") and "agent_knowledge_scope" in self.run:
                from app.data_access import authorize_data_resource
                for key,resource_kind in (("file_id","document"),("file_ids","document"),("knowledge_base_ids","knowledge_base"),("version_ids","knowledge_version")):
                    ids=values.get(key,[])
                    for resource_id in ids if isinstance(ids,list) else [ids]:
                        authorize_data_resource(session=self.session,context=self.context,kind=resource_kind,resource_id=resource_id,agent_scope=self.run["agent_knowledge_scope"])
            media=self.run.get("message",{}).get("capabilities",{})
            if kind in ("llm","agent") and (media.get("requires_audio") or media.get("requires_vision") and kind != "llm"):
                raise AppError("MODEL_CAPABILITY_UNAVAILABLE","Vision/transcription adapter is not configured; attachment filenames are not media understanding",503)
            result=await self.invoke(service,"execute_node",node_type=kind,config=config,inputs=values,run_id=self.run["id"],**common)
            if not isinstance(result,dict) or not isinstance(result.get("outputs"),dict): raise AppError("VARIABLE_TYPE_MISMATCH","Module node returned invalid output wrapper")
            outputs=result["outputs"]
            sources=[]
            if kind=="database_query":
                sources.append({"kind":"data_table","resource_id":values["table_id"],"action":"use"})
                if outputs.get("version_id"):sources.append({"kind":"data_table_version","resource_id":outputs["version_id"],"action":"use"})
            if kind in ("knowledge_retrieval","document_extractor"):
                for key,resource_kind in (("file_id","document"),("file_ids","document"),("knowledge_base_ids","knowledge_base"),("version_ids","knowledge_version")):
                    ids=values.get(key,[])
                    for resource_id in ids if isinstance(ids,list) else [ids]:sources.append({"kind":resource_kind,"resource_id":resource_id,"action":"use"})
            sources.extend(outputs.get("data_sources",[]))
            if sources:
                from app.data_access import record_derivation
                record_derivation(session=self.session,context=self.context,kind="run",resource_id=self.run["id"],sources=sources)
                existing=self.run.setdefault("data_sources",[])
                for source in sources:
                    if source not in existing:existing.append(source)
            if result.get("source_refs"): outputs.setdefault("source_refs",result["source_refs"])
            if result.get("warnings"): outputs.setdefault("warnings",result["warnings"])
            if outputs.get("mode")=="model": self.run["mode"]="model"
            selected=result.get("selected_port")
            if kind=="question_classifier":
                valid={e.source_port for e in workflow.edges if e.source==node.id}
                if selected not in valid: raise AppError("VALIDATION_ERROR","Classifier selected undeclared branch")
            return outputs,selected
        raise AppError("NODE_UNAVAILABLE","Node has no registered executor",503)
