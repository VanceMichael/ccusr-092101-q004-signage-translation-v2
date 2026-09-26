"""规范发布与采用追踪后端。

启动时自动执行数据库迁移；领域错误统一转成带 code 的 JSON 响应。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import DomainError
from app.routers import admin, public, unit
from scripts.migrate import migrate


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate(os.getenv("DATABASE_PATH", "data/app.sqlite3"))
    yield


app = FastAPI(title="规范发布与采用追踪", version="1.0.0", lifespan=lifespan)


@app.exception_handler(DomainError)
async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content={"error": exc.code, "message": exc.message})


app.include_router(admin.router)
app.include_router(unit.router)
app.include_router(public.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
