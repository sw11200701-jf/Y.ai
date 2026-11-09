"""Reuse owner-registered, hash-verified images already uploaded publicly.

No upload, credential, model URL or new public exposure occurs here. New images
without such a receipt retain the existing authenticated original-byte URL.
"""
from pathlib import Path
import hashlib,json,re
from urllib.parse import urlsplit
from app.settings import settings
from .slicing_contracts import fail

CONTRACT='verified-existing-public-image-urls-v1'
def path():return settings.data_dir/'knowledge-public-images/cache.json'
def snapshot():
    p=path()
    return {'contract':CONTRACT,'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
def validate_rows(value):
    if not isinstance(value,dict) or value.get('contract')!=CONTRACT or not isinstance(value.get('images'),list) or len(value['images'])>5000:
        fail('既有公开图片上传凭据格式无效','SLICING_ASSET_URL_UNAVAILABLE')
    rows={}
    for row in value['images']:
        if not isinstance(row,dict):fail('图片上传凭据无效','SLICING_ASSET_URL_UNAVAILABLE')
        original=row.get('original_sha256');uploaded=row.get('uploaded_sha256');p=urlsplit(row.get('url',''))
        if (not isinstance(original,str) or not re.fullmatch('[0-9a-f]{64}',original)
                or not isinstance(uploaded,str) or not re.fullmatch('[0-9a-f]{64}',uploaded)
                or p.scheme!='https' or p.hostname!='iili.io' or p.port not in (None,443)
                or p.username or p.password or p.query or p.fragment
                or not re.fullmatch(r'/[A-Za-z0-9]+\.(?:png|jpe?g|webp|gif|bmp)',p.path)
                or row.get('http_status')!=200 or row.get('downloaded_sha256')!=uploaded):
            fail('公开图片未绑定可信上传及实际下载字节','SLICING_ASSET_URL_UNAVAILABLE')
        if original!=uploaded:
            if (row.get('equivalence')!='identical_decoded_pixels' or not re.fullmatch('[0-9a-f]{64}',row.get('original_pixel_sha256',''))
                    or row.get('original_pixel_sha256')!=row.get('uploaded_pixel_sha256')):
                fail('图片转码未保留原图像素证明','SLICING_ASSET_URL_UNAVAILABLE')
        if original in rows and rows[original]!=row:fail('同一原图存在冲突上传凭据','SLICING_ASSET_URL_UNAVAILABLE')
        rows[original]=row
    return rows
def lookup(plan,asset):
    pinned=plan.get('public_image_cache')
    if pinned is None:return None  # Historical plans keep original URL logic.
    if pinned!=snapshot():fail('公开图片上传凭据已变化，请保留本次任务并重新核对','SLICING_SOURCE_CHANGED')
    if pinned.get('sha256') is None:return None
    p=path()
    if p.stat().st_size>10*1024*1024:fail('公开图片上传凭据超过限制','SLICING_ASSET_URL_UNAVAILABLE')
    try:rows=validate_rows(json.loads(p.read_text(encoding='utf-8')))
    except (ValueError,OSError):fail('公开图片上传凭据不可用','SLICING_ASSET_URL_UNAVAILABLE')
    row=rows.get(asset['sha256'])
    return row['url'] if row is not None else None
