import { NextRequest, NextResponse } from 'next/server';
import {uploadEnvelope,type UploadEnvelope} from './lib/upload-envelope';

// Message attachments retain their existing limit. Knowledge documents select
// 5/40 MiB explicitly; reject oversized envelopes before the proxy truncates.
function oversized(limit:UploadEnvelope) {
  return NextResponse.json({ error: { code:limit.code, message:limit.message, details: null } }, { status: 422 });
}
export async function middleware(request: NextRequest) {
  if (request.method !== 'POST') return NextResponse.next();
  let limit;
  try{limit=uploadEnvelope(request.nextUrl.pathname,request.nextUrl.searchParams.getAll('profile'));}
  catch{ return NextResponse.json({error:{code:'VALIDATION_ERROR',message:'请选择有效的文档处理方式，不能重复指定'}},{status:422}); }
  if(!limit)return NextResponse.next();
  const length = request.headers.get('content-length');
  if (length !== null && /^\d+$/.test(length)) {
    if (Number(length) > limit.bytes) return oversized(limit);
    return NextResponse.next();
  }
  // Streaming clients may omit Content-Length. Bound that path too, without
  // logging, parsing, retaining or altering the original upload bytes.
  const reader = request.body?.getReader();
  if (!reader) return NextResponse.next();
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit.bytes) { void reader.cancel(); return oversized(limit); }
    }
  } finally { reader.releaseLock(); }
  return NextResponse.next();
}
export const config = { matcher: ['/api/v1/attachments','/api/v1/knowledge-bases/:kb_id/documents'] };
