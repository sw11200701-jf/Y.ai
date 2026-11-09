export type UploadEnvelope={bytes:number;code:string;message:string};
export function uploadEnvelope(pathname:string,profiles:string[]=[]):UploadEnvelope|undefined{
 if(pathname==='/api/v1/attachments')return {bytes:11*1024*1024,code:'ATTACHMENT_INVALID',message:'附件内容、格式或大小不符合要求'};
 if(!/^\/api\/v1\/knowledge-bases\/[^/]+\/documents$/.test(pathname))return;
 if(profiles.length>1||profiles.length===1&&!['legacy','portable-slicing-1.0.0'].includes(profiles[0]))throw Error('请选择有效的文档处理方式，不能重复指定');
 const portable=profiles[0]==='portable-slicing-1.0.0';
 return {bytes:(portable?41:6)*1024*1024,code:'DOCUMENT_LIMIT',message:`文档超过所选处理方式的${portable?40:5}MB上限（未截断上传内容）`};
}
