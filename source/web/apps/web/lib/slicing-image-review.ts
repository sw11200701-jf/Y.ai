export type ImageLine={text:string;region:number[]};
export function originalPixelRegion(start:number[],end:number[],box:{left:number;top:number;width:number;height:number},size:{width:number;height:number}):number[]{
 if(![...start,...end,box.left,box.top,box.width,box.height,size.width,size.height].every(Number.isFinite)||box.width<=0||box.height<=0||size.width<=0||size.height<=0)throw Error('无法确认原图尺寸，请重新读取图片。');
 const point=(p:number[])=>[Math.max(0,Math.min(size.width,(p[0]-box.left)*size.width/box.width)),Math.max(0,Math.min(size.height,(p[1]-box.top)*size.height/box.height))];
 const a=point(start),b=point(end);return [Math.floor(Math.min(a[0],b[0])),Math.floor(Math.min(a[1],b[1])),Math.ceil(Math.max(a[0],b[0])),Math.ceil(Math.max(a[1],b[1]))];
}
export function imageReviewBody({asset,sourceSha,revision,lines,confirmed,disposition,reason,status='approved'}:{asset:{asset_id:string;width:number;height:number};sourceSha:string;revision:number;lines:ImageLine[];confirmed:boolean;disposition:string;reason:string;status?:'approved'|'revoked'}){
 if(!confirmed)throw Error('请对照原图后勾选确认文字与图框。');
 if(status==='approved'&&disposition==='text_evidence'&&(!lines.length||lines.some(line=>!line.text.trim()||line.region.length!==4||!line.region.every(Number.isFinite)||line.region[0]<0||line.region[1]<0||line.region[2]>asset.width||line.region[3]>asset.height||line.region[0]>=line.region[2]||line.region[1]>=line.region[3])))throw Error('请逐行填写原图文字，并在原图上拖框定位。');
 if(status==='approved'&&disposition==='non_evidence'&&!reason.trim())throw Error('请填写该图片不作为政策依据的核查理由。');
 return {asset_id:asset.asset_id,source_sha256:sourceSha,expected_revision:revision,status,disposition,reason,confirmed_against_original:true,
         lines:status==='revoked'||disposition==='non_evidence'?[]:lines};
}
