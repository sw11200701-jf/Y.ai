import hashlib
import re
import struct
import wave
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from fastapi import APIRouter,Depends,Request,UploadFile,File
from fastapi.responses import FileResponse
from PIL import Image,UnidentifiedImageError
from app.db import get_resource,save_resource,new_id,utc_now,get_session
from app.auth import identity
from app.messages import public_attachment
from app.errors import AppError
from app.settings import settings

router=APIRouter()
MAX_BYTES=10*1024*1024
FORMATS={".png":("image/png","image"),".jpg":("image/jpeg","image"),".jpeg":("image/jpeg","image"),".webp":("image/webp","image"),".wav":("audio/wav","audio"),".mp3":("audio/mpeg","audio"),".m4a":("audio/mp4","audio"),".txt":("text/plain","document"),".md":("text/markdown","document"),".pdf":("application/pdf","document"),".docx":("application/vnd.openxmlformats-officedocument.wordprocessingml.document","document"),".xlsx":("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet","document")}

def invalid(message="Attachment content does not match allowed MIME/format"):
    raise AppError("ATTACHMENT_INVALID",message,422)

def check_mp3(data):
    offset=0;count=0
    if data.startswith(b"ID3"):
        if len(data)<10 or any(b>127 for b in data[6:10]):invalid()
        offset=10+sum(b<<(7*(3-i)) for i,b in enumerate(data[6:10]))
    while offset+4<=len(data):
        if data[offset:offset+3]==b"TAG" and len(data)-offset==128:break
        header=int.from_bytes(data[offset:offset+4],"big")
        version=(header>>19)&3;layer=(header>>17)&3;rate=(header>>12)&15;sample=(header>>10)&3;padding=(header>>9)&1
        if header>>21!=0x7ff or version==1 or layer!=1 or rate in (0,15) or sample==3:invalid()
        bitrates=[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320] if version==3 else [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160]
        frequency=[44100,48000,32000][sample]//(1 if version==3 else 2 if version==2 else 4)
        size=(144 if version==3 else 72)*bitrates[rate]*1000//frequency+padding
        if size<4 or offset+size>len(data):invalid()
        offset+=size;count+=1
    if not count or offset<len(data) and not (len(data)-offset==128 and data[offset:offset+3]==b"TAG"):invalid()

def check_m4a(data):
    offset=0;boxes={}
    while offset+8<=len(data):
        size=int.from_bytes(data[offset:offset+4],"big");kind=data[offset+4:offset+8];header=8
        if size==1:
            if offset+16>len(data):invalid()
            size=int.from_bytes(data[offset+8:offset+16],"big");header=16
        if size==0:size=len(data)-offset
        if size<header or offset+size>len(data):invalid()
        boxes[kind]=data[offset+header:offset+size];offset+=size
    if offset!=len(data) or not boxes.get(b"mdat") or b"soun" not in boxes.get(b"moov",b"") or b"vide" in boxes[b"moov"] or b"ftyp" not in boxes:invalid()
    if boxes[b"ftyp"][:4] not in (b"M4A ",b"M4B ",b"isom",b"mp41",b"mp42"):invalid()

def validate_content(filename,declared,data):
    suffix=Path(filename).suffix.lower()
    if suffix not in FORMATS or not data or len(data)>MAX_BYTES:invalid("Unsupported, empty or oversized attachment")
    mime,kind=FORMATS[suffix]
    aliases={"audio/x-wav":"audio/wav","audio/wave":"audio/wav","audio/x-m4a":"audio/mp4"}
    declared=aliases.get((declared or "").split(";")[0],(declared or "").split(";")[0])
    if declared not in ("","application/octet-stream",mime):invalid()
    try:
        if kind=="image":
            with Image.open(BytesIO(data)) as image:
                if image.width*image.height>16000000 or Image.MIME.get(image.format)!=mime:invalid()
                image.verify()
        elif suffix==".wav":
            with wave.open(BytesIO(data),"rb") as audio:
                if not 1<=audio.getnchannels()<=2 or not 8000<=audio.getframerate()<=192000 or audio.getnframes()/audio.getframerate()>600 or not audio.getnframes():invalid()
                expected=audio.getnframes()*audio.getnchannels()*audio.getsampwidth()
                if len(audio.readframes(audio.getnframes()))!=expected:invalid()
        elif suffix==".mp3":check_mp3(data)
        elif suffix==".m4a":check_m4a(data)
        elif suffix in (".txt",".md"):
            if b"\x00" in data:invalid()
            data.decode("utf-8-sig")
        elif suffix==".pdf":
            from pypdf import PdfReader
            if not data.startswith(b"%PDF-"):invalid()
            pdf=PdfReader(BytesIO(data),strict=True)
            if pdf.is_encrypted or not 1<=len(pdf.pages)<=500:invalid()
        else:
            with ZipFile(BytesIO(data)) as archive:
                if len(archive.infolist())>2000 or sum(i.file_size for i in archive.infolist())>40*1024*1024 or archive.testzip() is not None:invalid()
                required="word/document.xml" if suffix==".docx" else "xl/workbook.xml"
                if required not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():invalid()
    except AppError:raise
    except Exception as exc:raise AppError("ATTACHMENT_INVALID","Malformed attachment",422) from exc
    return mime,kind

@router.post("/attachments",status_code=201)
async def upload(request:Request,file:UploadFile=File(...),session=Depends(get_session)):
    context=identity(request);data=await file.read(MAX_BYTES+1)
    filename=(file.filename or "").replace("\\","/").split("/")[-1][:150]
    mime,kind=validate_content(filename,file.content_type,data)
    aid=new_id("attachment");folder=settings.data_dir/"attachments";folder.mkdir(parents=True,exist_ok=True)
    path=folder/(aid+".bin");path.write_bytes(data)
    payload={"id":aid,"filename":filename,"mime":mime,"media_kind":kind,"size_bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),"created_at":utc_now(),"path":str(path.resolve())}
    try:save_resource(session,"attachment",aid,payload,context)
    except Exception:
        path.unlink(missing_ok=True);raise
    return public_attachment(payload)

@router.get("/attachments/{attachment_id}")
def detail(attachment_id:str,request:Request,session=Depends(get_session)):
    return public_attachment(get_resource(session,"attachment",attachment_id,identity(request)))

def load_attachment(*,attachment_id,context,session):
    payload=get_resource(session,"attachment",attachment_id,context)
    path=Path(payload["path"]).resolve()
    if not path.is_relative_to((settings.data_dir/"attachments").resolve()) or not path.is_file():raise AppError("NOT_FOUND","Attachment unavailable",404)
    with path.open("rb") as handle:data=handle.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES or len(data)!=payload["size_bytes"]:raise AppError("CONFLICT","Attachment size mismatch",409)
    if hashlib.sha256(data).hexdigest()!=payload["sha256"]:raise AppError("CONFLICT","Attachment checksum mismatch",409)
    return public_attachment(payload),data

@router.get("/attachments/{attachment_id}/download")
def download(attachment_id:str,request:Request,session=Depends(get_session)):
    payload=get_resource(session,"attachment",attachment_id,identity(request));load_attachment(attachment_id=attachment_id,context=identity(request),session=session)
    return FileResponse(payload["path"],filename=payload["filename"],media_type=payload["mime"])
