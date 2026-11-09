"""Stable, safe domain errors (never include upstream bodies or credentials)."""


class PluginError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}
