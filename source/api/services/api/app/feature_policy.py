import json
from sqlalchemy import select
from app.db import Resource,utc_now
VERSION='yai-platform-permissions-v1'
def seed_feature_permissions(session,tenant_id,workspace_id):
 from app.auth_models import AccessRole
 marker=session.scalar(select(Resource).where(Resource.kind=='feature_policy_marker',Resource.resource_id==VERSION,Resource.tenant_id==tenant_id,Resource.workspace_id==workspace_id))
 if marker:return seed_platform_permissions(session,tenant_id,workspace_id)
 for role in session.scalars(select(AccessRole).where(AccessRole.tenant_id==tenant_id,AccessRole.workspace_id==workspace_id,AccessRole.builtin==True)):
  extra={'chunker.config.view','chunker.config.read','chunker.config.manage','platform.keys.manage','platform.api.publish','resources.execute'} if role.base_role=='super_admin' else {'chunker.config.view'} if role.base_role=='admin' else set()
  role.permissions=json.dumps(sorted(set(json.loads(role.permissions))|extra))
 session.add(Resource(kind='feature_policy_marker',resource_id=VERSION,tenant_id=tenant_id,workspace_id=workspace_id,owner_id='__system__',payload='{}',created_at=utc_now(),updated_at=utc_now()))
 seed_platform_permissions(session,tenant_id,workspace_id)

def seed_platform_permissions(session,tenant_id,workspace_id):
 from app.auth_models import AccessRole
 version='yai-platform-credential-capabilities-v1'
 if session.scalar(select(Resource.id).where(Resource.kind=='feature_policy_marker',Resource.resource_id==version,Resource.tenant_id==tenant_id,Resource.workspace_id==workspace_id)):return
 for role in session.scalars(select(AccessRole).where(AccessRole.tenant_id==tenant_id,AccessRole.workspace_id==workspace_id,AccessRole.builtin==True)):
  permissions=set(json.loads(role.permissions))
  if role.base_role=='super_admin':permissions.update({'platform.keys.admin','spaces.manage'})
  # Preserve prior own-resource execution for built-in roles that still have a
  # run entrance. Reading somebody else's resource never acquires execution.
  if permissions&{'page.ai','page.workbench'}:permissions.add('resources.execute')
  role.permissions=json.dumps(sorted(permissions))
 session.add(Resource(kind='feature_policy_marker',resource_id=version,tenant_id=tenant_id,workspace_id=workspace_id,owner_id='__system__',payload='{}',created_at=utc_now(),updated_at=utc_now()))
