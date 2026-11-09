"""Resource JSON adapter; all decisions belong to the central data policy service."""
import json
from app import db
from app.data_access import authorize_data_resource, link_data_resource


def decision(session, kind, resource_id, context, action="use", agent_scope=None):
    return authorize_data_resource(session=session, context=context, kind=kind, resource_id=resource_id,
                                   action=action, agent_scope=agent_scope)


def get_resource(session, kind, resource_id, context, access="read", action=None):
    if action is not None:
        return json.loads(decision(session, kind, resource_id, context, action).resource.payload)
    return db.get_resource(session, kind, resource_id, context, access=access)


def list_resources(session, kind, context):
    return db.list_resources(session, kind, context)


def save_resource(session, kind, resource_id, payload, context):
    return db.save_resource(session, kind, resource_id, payload, context)


def link(session, context, kind, resource_id, parent_kind, parent_id, inherit=True):
    return link_data_resource(session=session, context=context, kind=kind, resource_id=resource_id,
        parent_kind=parent_kind, parent_id=parent_id, inherit_access=inherit)
