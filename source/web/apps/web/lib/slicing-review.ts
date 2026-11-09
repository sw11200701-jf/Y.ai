export type SlicingReview={contract:'readonly_pending_slicing_v1';read_only:true;manual_review_required:true;can_publish:false;document_id:string;run_id:string;source_sha256:string;candidate_sha256:string;final_audit_execution_id:string;items:Record<string,any>[];issues:Record<string,any>[];coverage:Record<string,any>[];item_count:number};
export function slicingReview(value:any,documentId:string,runId:string):SlicingReview{
 if(value?.contract!=='readonly_pending_slicing_v1'||value.read_only!==true||value.manual_review_required!==true||value.can_publish!==false
  ||value.document_id!==documentId||value.run_id!==runId||!Array.isArray(value.items)||value.item_count!==value.items.length
  ||!Array.isArray(value.issues)||!Array.isArray(value.coverage)||! /^[a-f0-9]{64}$/.test(value.source_sha256)||! /^[a-f0-9]{64}$/.test(value.candidate_sha256)
  ||! /^[a-f0-9]{64}$/.test(value.final_audit_execution_id)||value.items.some((item:any)=>!item||typeof item.knowledge_id!=='string'||typeof item.content!=='string'||!Array.isArray(item.evidence)))
  throw Error('待审结果与当前文档及运行不一致，请重新读取。');
 return value;
}
