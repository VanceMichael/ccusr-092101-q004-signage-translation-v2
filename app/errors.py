"""业务错误：路由层统一转成 HTTP 响应。"""


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class AuthError(DomainError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"
