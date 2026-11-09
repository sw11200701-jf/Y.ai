/** Local console diagnostics only: never record form values, response bodies or credentials. */
export function renderDiagnostic(error:unknown,componentStack?:string){
  const e=error as {name?:unknown;message?:unknown;stack?:unknown};const raw=typeof e?.message==='string'?e.message:'';
  const react=raw.match(/Minified React error #(\d+)/);
  const property=raw.match(/Cannot read properties of (?:undefined|null) \(reading '([A-Za-z_]+)'\)/);
  const field=property&&['map','filter','includes','role','permissions','role_ids'].includes(property[1])?property[1]:'field';
  const category=react?`react_${react[1]}`:property?`missing_${field}`:/Objects are not valid as a React child/.test(raw)?'invalid_react_child':/Maximum update depth|Too many re-renders/.test(raw)?'render_loop':'render_error';
  const route=typeof location!=='undefined'?new URLSearchParams(location.search).get('page'):null;
  const page=['accounts','roles','agents','data','runs','workbench','plugins','finance-sample'].includes(route||'')?route:'workspace';
  const frames=(String(componentStack||'')+'\n'+String(e?.stack||'')).split('\n').flatMap(line=>{
    const chunk=line.match(/\/_next\/static\/[A-Za-z0-9_./-]+\.js(?::\d+:\d+)?/);
    const component=line.trim().match(/^at ([A-Za-z_$][A-Za-z0-9_$]{0,80})(?:\s|$)/);
    return chunk?[chunk[0]]:component?[component[1]]:[];
  }).slice(0,16);
  return {category,page,name:['TypeError','ReferenceError','RangeError','Error'].includes(String(e?.name))?String(e.name):'Error',frames};
}
