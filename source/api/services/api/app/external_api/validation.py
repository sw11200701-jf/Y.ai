"""Validation errors never echo malformed credential-shaped field names."""
import re
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from app.errors import AppError,validation_detail


class ApplicationRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def validated(request):
            try:return await handler(request)
            except RequestValidationError as exc:
                details=[]
                for item in exc.errors()[:32]:
                    safe=validation_detail(item)
                    safe['loc']=[v if type(v) is int or isinstance(v,str) and re.fullmatch(r'[a-z_]{1,50}',v) else '[field]' for v in item.get('loc',())]
                    details.append(safe)
                raise AppError('APP_INPUT_INVALID','提交内容不符合应用接口要求',422,details) from None
        return validated
