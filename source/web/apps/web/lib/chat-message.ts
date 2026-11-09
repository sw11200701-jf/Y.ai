import type { AttachmentMetadata, MessageDraft } from './workflow/types';
export function inferChatMessage(text:string,attachments:AttachmentMetadata[]):MessageDraft {
  if(!attachments.length)return {type:'text',text,attachments:[]};
  if(text.trim())return {type:'mixed',text,attachments};
  if(attachments.every(a=>a.media_kind==='image'))return {type:'image',text:'',attachments};
  if(attachments.length===1&&attachments[0].media_kind==='audio')return {type:'audio',text:'',attachments};
  if(attachments.every(a=>a.media_kind==='document'))return {type:'file',text:'',attachments};
  return {type:'mixed',text,attachments};
}
export function encodeWav(channels:Float32Array[],sampleRate:number):ArrayBuffer {
  const frames=channels[0]?.length||0;const count=Math.min(channels.length,2);const dataBytes=frames*count*2;
  if(!frames||!count||dataBytes>10*1024*1024-44)throw Error('录音为空或超过10MiB，请缩短录音后重试。');
  const buffer=new ArrayBuffer(44+dataBytes);const view=new DataView(buffer);const ascii=(offset:number,text:string)=>{for(let i=0;i<text.length;i++)view.setUint8(offset+i,text.charCodeAt(i));};
  ascii(0,'RIFF');view.setUint32(4,36+dataBytes,true);ascii(8,'WAVE');ascii(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,count,true);view.setUint32(24,sampleRate,true);view.setUint32(28,sampleRate*count*2,true);view.setUint16(32,count*2,true);view.setUint16(34,16,true);ascii(36,'data');view.setUint32(40,dataBytes,true);
  for(let frame=0;frame<frames;frame++)for(let channel=0;channel<count;channel++){const sample=Math.max(-1,Math.min(1,channels[channel][frame]||0));view.setInt16(44+(frame*count+channel)*2,sample<0?sample*32768:sample*32767,true);}return buffer;
}
