"""Platform-token adapters for the database workbench."""
from typing import Annotated

from fastapi import Depends, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import Field, model_validator

from app.database import lifecycle
from app.database.router import write_transaction
from app.db import get_session
from app.knowledge import database_router as workbench
from app.knowledge import database_service as s
from app.knowledge.database_schemas import DatabaseWrite, DataRevision, TableQuery, TableWrite
from app.knowledge.schemas import StrictModel
from app.platform_api import runtime
from app.platform_resources import context

SPACE = "/spaces/{workspace_id}"
DATABASES = SPACE + "/databases"
DATABASE = DATABASES + "/{database_id}"
TABLE = SPACE + "/database/tables/{table_id}"
BATCH = SPACE + "/database/batches/{batch_id}"

Expected = Annotated[str | None, Header(alias="If-Match", max_length=128)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]
SERIALIZED_WRITE = [Depends(write_transaction)]


class DatabasePatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def has_change(self):
        if self.name is None and self.description is None:
            raise ValueError("请提供名称或说明")
        return self


def page(items, offset, limit):
    return {"items": items[offset:offset + limit], "offset": offset, "limit": limit, "total": len(items)}


def representation(kind, value, response, current, session):
    response.headers["ETag"] = lifecycle.etag(value)
    metadata = s.database_metadata if kind == "database" else s.table_metadata
    return metadata(value, current, session)


@runtime.get(DATABASES, summary="列出空间内可读取的数据库")
def databases(workspace_id: str, request: Request, offset: Offset = 0, limit: Limit = 20, session=Depends(get_session)):
    result = s.databases(context=context(request, session, workspace_id), session=session)
    return page([item for item in result["items"] if not item.get("metadata_only")], offset, limit)


@runtime.post(DATABASES, status_code=201, summary="新建数据库")
async def create_database(workspace_id: str, body: DatabaseWrite, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = await s.create_database(definition=body.model_dump(), context=current, session=session)
    return representation("database", value, response, current, session)


@runtime.get(DATABASE, summary="读取数据库及 ETag")
def get_database(workspace_id: str, database_id: str, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = s.root("database", database_id, current, session)
    return representation("database", value, response, current, session)


@runtime.patch(DATABASE, summary="修改数据库名称或说明")
def edit_database(workspace_id: str, database_id: str, body: DatabasePatch, request: Request, response: Response,
                  expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = lifecycle.edit("database", database_id, changes=body.model_dump(exclude_none=True), expected=expected,
        context=current, session=session)
    return representation("database", value, response, current, session)


@runtime.get(DATABASE + "/tables", summary="列出数据库中的表")
def tables(workspace_id: str, database_id: str, request: Request, session=Depends(get_session)):
    return s.tables(database_id=database_id, context=context(request, session, workspace_id), session=session)


@runtime.post(DATABASE + "/tables", status_code=201, dependencies=SERIALIZED_WRITE, summary="声明字段并新建空表")
async def create_table(workspace_id: str, database_id: str, body: TableWrite, request: Request, response: Response,
                       session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = await s.create_table(database_id=database_id, definition=body.model_dump(), context=current, session=session)
    return representation("data_table", value, response, current, session)


@runtime.get(TABLE, summary="读取数据表及 ETag")
def get_table(workspace_id: str, table_id: str, request: Request, response: Response, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = s.root("data_table", table_id, current, session)
    return representation("data_table", value, response, current, session)


@runtime.patch(TABLE, summary="修改表名称或说明")
def edit_table(workspace_id: str, table_id: str, body: DatabasePatch, request: Request, response: Response,
               expected: Expected = None, session=Depends(get_session)):
    current = context(request, session, workspace_id)
    value = lifecycle.edit("data_table", table_id, changes=body.model_dump(exclude_none=True), expected=expected,
        context=current, session=session)
    return representation("data_table", value, response, current, session)


def change_state(kind, identifier, *, restore, expected, workspace_id, request, response, session):
    result = lifecycle.change(kind, identifier, restore=restore, expected=expected,
        context=context(request, session, workspace_id), session=session)
    response.headers["ETag"] = result["etag"]
    return result


@runtime.delete(DATABASE, summary="将数据库移入回收站")
def remove_database(workspace_id: str, database_id: str, request: Request, response: Response,
                    expected: Expected = None, session=Depends(get_session)):
    return change_state("database", database_id, restore=False, expected=expected, workspace_id=workspace_id,
        request=request, response=response, session=session)


@runtime.post(DATABASE + "/restore", summary="从回收站恢复数据库")
def restore_database(workspace_id: str, database_id: str, request: Request, response: Response,
                     expected: Expected = None, session=Depends(get_session)):
    return change_state("database", database_id, restore=True, expected=expected, workspace_id=workspace_id,
        request=request, response=response, session=session)


@runtime.delete(TABLE, summary="将数据表移入回收站")
def remove_table(workspace_id: str, table_id: str, request: Request, response: Response,
                 expected: Expected = None, session=Depends(get_session)):
    return change_state("data_table", table_id, restore=False, expected=expected, workspace_id=workspace_id,
        request=request, response=response, session=session)


@runtime.post(TABLE + "/restore", summary="从回收站恢复数据表")
def restore_table(workspace_id: str, table_id: str, request: Request, response: Response,
                  expected: Expected = None, session=Depends(get_session)):
    return change_state("data_table", table_id, restore=True, expected=expected, workspace_id=workspace_id,
        request=request, response=response, session=session)


@runtime.get(SPACE + "/database/recycle-bin", summary="列出可维护的已删除数据库或表")
def recycle_bin(workspace_id: str, request: Request, database_id: str | None = None, offset: Offset = 0,
                limit: Limit = 20, session=Depends(get_session)):
    items = lifecycle.recycle_bin(database_id=database_id, context=context(request, session, workspace_id), session=session)
    return page(items, offset, limit)


@runtime.post(DATABASE + "/imports", status_code=201, dependencies=SERIALIZED_WRITE, summary="从文件建表并生成待发布批次")
async def import_table(workspace_id: str, database_id: str, request: Request, file: UploadFile = File(...),
                       name: str | None = Form(None), sheet_name: str | None = Form(None), session=Depends(get_session)):
    return await workbench.import_new(database_id, file, name, sheet_name, session, context(request, session, workspace_id))


@runtime.post(TABLE + "/imports", status_code=201, dependencies=SERIALIZED_WRITE, summary="向已有表导入新批次")
async def replace_table(workspace_id: str, table_id: str, request: Request, file: UploadFile = File(...),
                        sheet_name: str | None = Form(None), session=Depends(get_session)):
    return await workbench.upload(table_id, file, sheet_name, session, context(request, session, workspace_id))


@runtime.get(TABLE + "/batches", summary="列出导入及修订批次")
def batches(workspace_id: str, table_id: str, request: Request, session=Depends(get_session)):
    return s.batches(table_id=table_id, context=context(request, session, workspace_id), session=session)


@runtime.get(BATCH, summary="查看批次校验和预览")
def get_batch(workspace_id: str, batch_id: str, request: Request, session=Depends(get_session)):
    return workbench.batch(batch_id, session, context(request, session, workspace_id))


@runtime.post(BATCH + "/revisions", status_code=201, dependencies=SERIALIZED_WRITE, summary="修订字段映射或批次单元格")
async def revise_batch(workspace_id: str, batch_id: str, body: DataRevision, request: Request, session=Depends(get_session)):
    return await workbench.revise(batch_id, body, session, context(request, session, workspace_id))


@runtime.post(BATCH + "/publish", dependencies=SERIALIZED_WRITE, summary="发布通过校验的批次")
async def publish_table(workspace_id: str, batch_id: str, request: Request, session=Depends(get_session)):
    return await s.publish_batch(batch_id=batch_id, context=context(request, session, workspace_id), session=session)


@runtime.get(TABLE + "/versions", summary="列出授权的已发布版本")
def versions(workspace_id: str, table_id: str, request: Request, session=Depends(get_session)):
    return s.versions(table_id=table_id, context=context(request, session, workspace_id), session=session)


@runtime.get(TABLE + "/dictionary", summary="读取已发布版本的授权字段字典")
def dictionary(workspace_id: str, table_id: str, request: Request, version_id: str | None = None, session=Depends(get_session)):
    return s.dictionary(table_id=table_id, version_id=version_id, context=context(request, session, workspace_id), session=session)


@runtime.post(TABLE + "/query", summary="按授权范围查询、筛选和统计数据")
async def query_table(workspace_id: str, table_id: str, body: TableQuery, request: Request, session=Depends(get_session)):
    return await workbench.query(table_id, body, session, context(request, session, workspace_id))


@runtime.get(SPACE + "/database/files/{file_id}/download", response_class=Response,
             responses={200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}},
             summary="下载完整授权的导入原件")
def download(workspace_id: str, file_id: str, request: Request, session=Depends(get_session)):
    return workbench.download(file_id, session, context(request, session, workspace_id))


@runtime.post(TABLE + "/export", response_class=Response,
              responses={200: {"content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}},
              summary="导出经行列权限过滤的数据")
async def export(workspace_id: str, table_id: str, request: Request, version_id: str | None = None, session=Depends(get_session)):
    return await workbench.export(table_id, version_id, session, context(request, session, workspace_id))
