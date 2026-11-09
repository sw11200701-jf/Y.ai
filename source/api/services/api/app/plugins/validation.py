"""Chinese, located validation for module-owned REST inputs, without input echoes."""

from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.errors import AppError, validation_detail


MESSAGES = {"tool_description_required": "请填写工具用途说明，不能仅为空格"}


def details(errors):
    result = []
    for error in errors:
        item = validation_detail(error)
        if error["type"] in MESSAGES:
            item["message"] = MESSAGES[error["type"]]
        result.append(item)
    return result


class ValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise AppError("VALIDATION_ERROR", "提交内容不符合要求，请检查标记字段", 422,
                               details=details(exc.errors())) from exc

        return validated
