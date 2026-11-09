"""Mounted by central only at /api/v1, human session+CSRF only."""
from fastapi import APIRouter,Depends,Query,Response
from app.context import get_context
from app.db import get_session
from .validation import ApplicationRoute
from . import schemas as dto,service
from .callbacks import callback

router=APIRouter(tags=['applications-management'],route_class=ApplicationRoute)


@router.get('/applications')
def applications(page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),session=Depends(get_session),context=Depends(get_context)):
    return service.list_applications(session,context,page_number=page,page_size=page_size)


@router.post('/applications',status_code=201,response_model=dto.ApplicationView)
def create(data:dto.ApplicationCreate,session=Depends(get_session),context=Depends(get_context)):
    return service.create_application(session,context,data)


@router.get('/applications/{app_id}',response_model=dto.ApplicationView)
def get(app_id:str,session=Depends(get_session),context=Depends(get_context)):
    return service.app_view(session,service.application(session,context,app_id),context)


@router.patch('/applications/{app_id}',response_model=dto.ApplicationView)
def patch(app_id:str,data:dto.ApplicationPatch,session=Depends(get_session),context=Depends(get_context)):
    return service.patch_application(session,context,app_id,data)


@router.get('/applications/{app_id}/versions')
def versions(app_id:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),session=Depends(get_session),context=Depends(get_context)):
    return service.list_versions(session,context,app_id,page_number=page,page_size=page_size)


@router.post('/applications/{app_id}/versions/preview')
def preview(app_id:str,data:dto.VersionPreview,session=Depends(get_session),context=Depends(get_context)):
    return service.preview_version(session,context,app_id,data,callback('prepare_release'))


@router.post('/applications/{app_id}/versions',status_code=201,response_model=dto.VersionView)
def publish(app_id:str,data:dto.VersionPublish,session=Depends(get_session),context=Depends(get_context)):
    return service.publish_version(session,context,app_id,data,callback('prepare_release'))


@router.post('/applications/{app_id}/activate-version',response_model=dto.ApplicationView)
def activate(app_id:str,data:dto.ActivateVersion,session=Depends(get_session),context=Depends(get_context)):
    return service.activate_version(session,context,app_id,data)


@router.get('/applications/{app_id}/clients')
def clients(app_id:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),session=Depends(get_session),context=Depends(get_context)):
    return service.list_clients(session,context,app_id,page_number=page,page_size=page_size)


@router.get('/applications/{app_id}/client-options',response_model=dto.ClientOptionsView)
def client_options(app_id:str,version_id:str=Query(...,min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$'),session=Depends(get_session),context=Depends(get_context)):
    return service.client_options(session,context,app_id,version_id,callback('client_options'))


from .public_schemas import OpenAPIContract


@router.get('/applications/{app_id}/contract',response_model=OpenAPIContract,response_model_exclude_unset=True)
def public_contract(app_id:str,version_id:str=Query(...,min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$'),
                    client_id:str=Query(...,min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$'),session=Depends(get_session),context=Depends(get_context)):
    from .documentation import application_contract
    return application_contract(session,context,app_id,version_id,client_id)


@router.post('/applications/{app_id}/clients',status_code=201,response_model=dto.ClientView)
def create_client(app_id:str,data:dto.ClientCreate,session=Depends(get_session),context=Depends(get_context)):
    return service.create_client(session,context,app_id,data,callback('provision_principal'),callback('authorize_resources'))


@router.patch('/applications/{app_id}/clients/{client_id}',response_model=dto.ClientView)
def patch_client(app_id:str,client_id:str,data:dto.ClientPatch,session=Depends(get_session),context=Depends(get_context)):
    return service.patch_client(session,context,app_id,client_id,data)


@router.get('/applications/{app_id}/clients/{client_id}/keys')
def keys(app_id:str,client_id:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),session=Depends(get_session),context=Depends(get_context)):
    return service.list_keys(session,context,app_id,client_id,page_number=page,page_size=page_size)


@router.post('/applications/{app_id}/clients/{client_id}/keys',status_code=201,response_model=dto.KeyReceipt)
def create_key(app_id:str,client_id:str,data:dto.KeyCreate,response:Response,session=Depends(get_session),context=Depends(get_context)):
    response.headers['Cache-Control']='no-store';response.headers['Pragma']='no-cache'
    return service.create_key(session,context,app_id,client_id,data)


@router.post('/applications/{app_id}/clients/{client_id}/keys/{key_id}/revoke',response_model=dto.KeyMetadata)
def revoke(app_id:str,client_id:str,key_id:str,session=Depends(get_session),context=Depends(get_context)):
    return service.revoke_key(session,context,app_id,client_id,key_id)


@router.post('/applications/{app_id}/clients/{client_id}/keys/{key_id}/rotate',response_model=dto.KeyReceipt)
def rotate(app_id:str,client_id:str,key_id:str,data:dto.KeyRotate,response:Response,session=Depends(get_session),context=Depends(get_context)):
    response.headers['Cache-Control']='no-store';response.headers['Pragma']='no-cache'
    return service.rotate_key(session,context,app_id,client_id,key_id,data)


@router.get('/applications/{app_id}/calls',response_model=dto.CallPage,response_model_exclude_unset=True)
def calls(app_id:str,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),status:str|None=Query(None,max_length=30,pattern=r'^[a-z_]+$'),
          client_id:str|None=Query(None,max_length=100,pattern=r'^[A-Za-z0-9_-]+$'),session=Depends(get_session),context=Depends(get_context)):
    return service.list_calls(session,context,app_id,page_number=page,page_size=page_size,status=status,client_id=client_id)
