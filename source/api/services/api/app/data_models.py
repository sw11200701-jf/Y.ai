"""Central data authorization records. No business rows or credential plaintext."""
from sqlalchemy import String,Text,Integer,Boolean
from sqlalchemy.orm import Mapped,mapped_column
from app.db import Base

class UserRoleMembership(Base):
    __tablename__="user_role_memberships"
    user_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    role_id:Mapped[str]=mapped_column(String(100),primary_key=True)

class DataPolicyState(Base):
    __tablename__="data_policy_states"
    tenant_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    workspace_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    revision:Mapped[int]=mapped_column(Integer,default=1)

class DataGrant(Base):
    __tablename__="data_grants"
    id:Mapped[str]=mapped_column(String(100),primary_key=True)
    resource_pk:Mapped[int]=mapped_column(Integer,index=True)
    subject_type:Mapped[str]=mapped_column(String(20))
    subject_id:Mapped[str]=mapped_column(String(100),index=True)
    effect:Mapped[str]=mapped_column(String(20))
    actions:Mapped[str]=mapped_column(Text)
    row_scope:Mapped[str]=mapped_column(Text)
    columns:Mapped[str]=mapped_column(Text)
    delegable_actions:Mapped[str]=mapped_column(Text)
    created_by:Mapped[str]=mapped_column(String(100))

class DataResourceLink(Base):
    __tablename__="data_resource_links"
    resource_pk:Mapped[int]=mapped_column(Integer,primary_key=True)
    parent_pk:Mapped[int]=mapped_column(Integer,index=True)
    inherit_access:Mapped[bool]=mapped_column(Boolean,default=False)

class DataDerivation(Base):
    __tablename__="data_derivations"
    resource_pk:Mapped[int]=mapped_column(Integer,primary_key=True)
    source_pk:Mapped[int]=mapped_column(Integer,primary_key=True)
    actor_id:Mapped[str]=mapped_column(String(100))
    policy_revision:Mapped[int]=mapped_column(Integer)
    required_action:Mapped[str]=mapped_column(String(30))

class DataResourceAlias(Base):
    __tablename__="data_resource_aliases"
    tenant_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    workspace_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    kind:Mapped[str]=mapped_column(String(50),primary_key=True)
    resource_id:Mapped[str]=mapped_column(String(100),primary_key=True)
    resource_pk:Mapped[int]=mapped_column(Integer,index=True)

class DataTableBinding(Base):
    __tablename__="data_table_bindings"
    resource_pk:Mapped[int]=mapped_column(Integer,primary_key=True)
    database_pk:Mapped[int]=mapped_column(Integer,index=True)
