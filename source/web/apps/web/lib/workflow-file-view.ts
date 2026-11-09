/** Presentation values; backend wire DTOs are adapted separately after contract freeze. */
export type FileDiagnostic={severity:'blocking'|'warning'|'info';code:string;message:string;nodeId?:string;field?:string};
export type FileBindingChoice={id:string;label:string;description?:string};
export type FileBindingRequirement={key:string;kind:string;label:string;nodeId?:string;required:boolean;multiple?:boolean;choices:FileBindingChoice[]};
export type WorkflowFilePreview={token:string;inputHash:string;formatLabel:string;versionLabel:string;name:string;nodeCount:number;edgeCount:number;nodes:{id:string;label:string;typeLabel:string}[];diagnostics:FileDiagnostic[];bindings:FileBindingRequirement[];canCreate:boolean};
export type FileBindings=Record<string,string|string[]>;
export function bindingProblems(requirements:FileBindingRequirement[],bindings:FileBindings){return requirements.filter(item=>{const value=bindings[item.key];if(!item.required&&value===undefined)return false;return item.multiple? !Array.isArray(value)||!value.length||value.length>64||new Set(value).size!==value.length||value.some(id=>!item.choices.some(choice=>choice.id===id)):typeof value!=='string'||!item.choices.some(choice=>choice.id===value);}).map(item=>`请为“${item.label}”选择当前可访问的资源。`);}
export function bindingFingerprint(bindings:FileBindings){return JSON.stringify(Object.entries(bindings).sort(([a],[b])=>a.localeCompare(b)));}
export function canCreateFilePreview(preview:WorkflowFilePreview,bindings:FileBindings){return preview.canCreate&&preview.diagnostics.every(item=>item.severity!=='blocking')&&bindingProblems(preview.bindings,bindings).length===0;}
