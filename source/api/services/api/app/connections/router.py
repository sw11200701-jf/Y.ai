from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.context import get_context
from app.contracts import BusinessContext
from app.db import get_resource, get_session, list_resources
from .service import ConnectionWrite, public_connection, save_connection
from app.plugins.validation import ValidationRoute

router = APIRouter(tags=["connections"], route_class=ValidationRoute)


@router.get("/connections")
def connections(session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return {"items": [public_connection(c) for c in list_resources(session, "connection", context)]}


@router.post("/connections", status_code=201)
async def create(data: ConnectionWrite, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return await save_connection(data, context, session)


@router.get("/connections/{connection_id}")
def get(connection_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return public_connection(get_resource(session, "connection", connection_id, context))


@router.put("/connections/{connection_id}")
async def update(connection_id: str, data: ConnectionWrite, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    return await save_connection(data, context, session, connection_id)


@router.get("/connections/{connection_id}/plugins")
def connection_plugins(connection_id: str, session: Session = Depends(get_session, scope="function"), context: BusinessContext = Depends(get_context)):
    from app.plugins.service import list_plugins
    return list_plugins(context=context, session=session, connection_id=connection_id, page_size=100)
