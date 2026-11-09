"""Explicit human image-text review, tied to original bytes and native geometry."""
from copy import deepcopy
import math
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from app.db import Resource, utc_now
from .slicing_contracts import digest, fail


class ReviewedImageLine(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    text:str=Field(min_length=1,max_length=2000)
    region:list[float|int]=Field(min_length=4,max_length=4)


class ImageReviewWrite(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    asset_id:str=Field(min_length=1,max_length=160)
    source_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    expected_revision:int=Field(ge=0)
    status:str=Field(pattern=r'^(approved|revoked)$')
    disposition:str=Field(default='text_evidence',pattern=r'^(text_evidence|non_evidence)$')
    reason:str=Field(default='',max_length=2000)
    confirmed_against_original:bool
    lines:list[ReviewedImageLine]=Field(max_length=1000)


def validate_lines(lines, asset):
    if not lines or sum(len(line['text']) for line in lines)>50000:fail('请提供完整且不超过5万字的原图文字核查记录','SLICING_IMAGE_REVIEW_INVALID')
    seen=set()
    for line in lines:
        region=line['region']
        if (not isinstance(line['text'],str) or not line['text'].strip() or '\x00' in line['text']
                or not isinstance(region,list) or len(region)!=4
                or any(type(n) not in (int,float) or not math.isfinite(n) for n in region)
                or not 0<=region[0]<region[2]<=asset['width'] or not 0<=region[1]<region[3]<=asset['height']):
            fail('文字行或图框不符合原始像素尺寸','SLICING_IMAGE_REVIEW_INVALID')
        key=(line['text'],tuple(region))
        if key in seen:fail('同一文字行及图框不能重复登记','SLICING_IMAGE_REVIEW_INVALID')
        seen.add(key)


def apply_verified_reviews(registry, file):
    reviews=file.get('image_evidence_reviews') or {}
    if type(reviews) is not dict:fail('图像核查记录结构无效','SLICING_IMAGE_REVIEW_INVALID')
    for oid,review in reviews.items():
        occurrence=registry['occurrences'].get(oid)
        if (type(review) is not dict or not occurrence or review.get('occurrence_id')!=oid or review.get('asset_id')!=occurrence['asset_id']
                or review.get('source_sha256')!=registry['source_sha256'] or review.get('locator')!=occurrence['locator']
                or not review.get('reviewer_id') or review.get('confirmed_against_original') is not True):
            fail('图像核查不再对应原件、出现位置或人工确认','SLICING_IMAGE_REVIEW_CHANGED')
        if review.get('status')=='revoked':continue
        if review.get('status')!='approved' or occurrence.get('ocr_lines'):fail('人工核查须基于未追加OCR的原生来源','SLICING_IMAGE_REVIEW_INVALID')
        lines=review.get('lines',[])
        if review.get('disposition','text_evidence')=='non_evidence':
            if lines or not review.get('reason','').strip():fail('非依据图片须明确人工理由并且不登记文字证据','SLICING_IMAGE_REVIEW_INVALID')
        else:validate_lines(lines,registry['assets'][occurrence['asset_id']])
        source=registry['sources'][occurrence['source_id']]
        for line in lines:
            prefix='\n' if source['text'] else '';start=len(source['text'])+len(prefix)
            source['text']+=prefix+line['text']
            occurrence['ocr_lines'].append({**deepcopy(line),'start':start,'end':start+len(line['text'])})
        occurrence.update(ocr_reviewed=True,human_review_sha256=digest(review),reviewed_disposition=review.get('disposition','text_evidence'),review_reason=review.get('reason',''))
    if sum(len(s['text']) for s in registry['sources'].values())>500000:fail('完整原文及人工核查文字超过限额，未截断','SLICING_PARSE_LIMIT')
    registry['registry_sha256']=digest({key:registry[key] for key in ('sources','assets','occurrences')})
    return registry


def original_image_registry(document_id, *, context, session):
    from .semantic_document import _source
    from .slicing_finalizer import authorize_export
    from . import service
    from app.data_access import _unrestricted
    from .slicing_parser import parse_document
    root,file,content=_source(document_id,context,session)
    if not file['id'].startswith('document_'):fail('请通过正常文档上传入口创建原件核查记录','SLICING_IMAGE_REVIEW_INVALID')
    authorize_export(None,root,file,context=context,session=session)
    if not _unrestricted(service.decision(session,'document',file['id'],context,'download_original')):
        fail('对照原图需要当前原件下载权限','SLICING_IMAGE_REVIEW_DENIED')
    if file['format'] in ('doc','.doc'):fail('DOC原图核查须先接入持久化转换资产','SLICING_DOC_ADAPTER_UNAVAILABLE')
    images={}
    from .slicing_parser import IMAGE_LAYOUT_V2
    registry=parse_document(content,file['format'],file_id=file['id'],version_sha256=file['sha256'],perform_ocr=False,asset_sink=lambda aid,b:images.setdefault(aid,b),image_layout_version=IMAGE_LAYOUT_V2)
    return root,file,registry,images


def review_catalog(document_id, *, context, session):
    _,file,registry,_=original_image_registry(document_id,context=context,session=session)
    reviews=file.get('image_evidence_reviews') or {}
    return {'document_id':document_id,'source_sha256':file['sha256'],'revision':file.get('image_evidence_revision',0),
        'occurrences':[{'occurrence_id':oid,'asset':registry['assets'][o['asset_id']],'locator':o['locator'],
                        'review':deepcopy(reviews.get(oid))} for oid,o in registry['occurrences'].items()]}


def original_image(document_id, occurrence_id, asset_id, *, context, session):
    _,_,registry,images=original_image_registry(document_id,context=context,session=session)
    occurrence=registry['occurrences'].get(occurrence_id)
    if not occurrence or occurrence['asset_id']!=asset_id or asset_id not in images:fail('图片出现或原件不匹配','SLICING_IMAGE_REVIEW_INVALID')
    from PIL import Image
    from io import BytesIO
    content=images[asset_id]
    with Image.open(BytesIO(content)) as opened:mime=Image.MIME.get(opened.format,'application/octet-stream')
    return content,mime


def save_review(document_id, occurrence_id, body, *, context, session):
    root,file,registry,_=original_image_registry(document_id,context=context,session=session)
    occurrence=registry['occurrences'].get(occurrence_id)
    if not occurrence or occurrence['asset_id']!=body.asset_id or file['sha256']!=body.source_sha256:
        fail('请重新读取当前原图及出现位置','SLICING_IMAGE_REVIEW_CHANGED')
    if body.confirmed_against_original is not True:fail('请先对照原图明确确认文字与图框','SLICING_IMAGE_REVIEW_INVALID')
    if body.expected_revision!=file.get('image_evidence_revision',0):fail('图像核查已被他人修改，请保留当前输入并重新读取','SLICING_IMAGE_REVIEW_CONFLICT')
    lines=[line.model_dump() for line in body.lines]
    if body.status=='revoked' and lines:fail('撤销核查不应提交新的文字证据','SLICING_IMAGE_REVIEW_INVALID')
    if body.status=='approved':
        if body.disposition=='non_evidence':
            if lines or not body.reason.strip():fail('非依据图片须填写明确理由并移除文字证据','SLICING_IMAGE_REVIEW_INVALID')
        else:validate_lines(lines,registry['assets'][body.asset_id])
    reviews=deepcopy(file.get('image_evidence_reviews') or {})
    reviews[occurrence_id]={'occurrence_id':occurrence_id,'asset_id':body.asset_id,'source_sha256':body.source_sha256,
        'locator':deepcopy(occurrence['locator']),'status':body.status,'disposition':body.disposition,'reason':body.reason,'lines':lines,'confirmed_against_original':True,
        'reviewer_id':context.operator_id,'reviewed_at':utc_now()}
    if sum(len(line['text']) for review in reviews.values() for line in review.get('lines',[]))>500000:
        fail('全文图像核查文字超过50万字，未截断','SLICING_IMAGE_REVIEW_INVALID')
    row=session.scalar(select(Resource).where(Resource.kind=='document',Resource.resource_id==file['id'],
        Resource.tenant_id==context.tenant_id,Resource.workspace_id==context.workspace_id))
    if row is None:fail('原件已不存在','SLICING_IMAGE_REVIEW_CHANGED')
    previous=row.payload
    if session.execute(update(Resource).where(Resource.id==row.id,Resource.payload==previous).values(payload=previous)).rowcount!=1:
        fail('原件并发变化，请重新读取','SLICING_IMAGE_REVIEW_CONFLICT')
    from . import service
    fresh=service.get_resource(session,'document',file['id'],context,access='write')
    if fresh['sha256']!=body.source_sha256 or fresh.get('image_evidence_revision',0)!=body.expected_revision:
        fail('原件或核查修订并发变化','SLICING_IMAGE_REVIEW_CONFLICT')
    fresh.update(image_evidence_reviews=reviews,image_evidence_revision=body.expected_revision+1)
    service.save_resource(session,'document',file['id'],fresh,context)
    session.commit()
    return {'document_id':document_id,'revision':fresh['image_evidence_revision'],'review':reviews[occurrence_id]}
