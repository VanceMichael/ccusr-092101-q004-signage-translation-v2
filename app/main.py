
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import JSONResponse

from app import db, domain

app = FastAPI(title="规范发布与采用追踪")


@contextmanager
def connection() -> Iterator:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@app.exception_handler(domain.DomainError)
def domain_error_handler(_: Request, exc: domain.DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"error": exc.code, "message": exc.message}
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------- 基础档案

@app.post("/units", status_code=201)
def create_unit(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.create_unit(conn, body.get("unit_ref", ""), body.get("name", ""))


@app.post("/subscriptions", status_code=201)
def subscribe(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.subscribe(
            conn,
            body.get("subscription_id", ""),
            body.get("unit_ref", ""),
            body.get("scope_type", ""),
            body.get("scope_value", ""),
        )


@app.post("/signs", status_code=201)
def register_sign(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.register_sign(
            conn,
            body.get("sign_ref", ""),
            body.get("unit_ref", ""),
            body.get("location_code", ""),
            body.get("place_category", ""),
            body.get("carrier_type", ""),
        )


# ---------------------------------------------------------------- 发布包

@app.post("/packages", status_code=201)
def publish_package(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.publish_package(conn, body)


@app.get("/packages")
def list_packages() -> list[dict]:
    with connection() as conn:
        return domain.list_packages(conn)


@app.get("/packages/{package_id}")
def get_package(package_id: str) -> dict:
    with connection() as conn:
        return domain.get_package_view(conn, package_id)


@app.post("/packages/{package_id}/deprecate")
def deprecate_package(package_id: str, body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.deprecate_package(
            conn, package_id, body.get("effective_at", ""), body.get("note", "")
        )


@app.get("/packages/{package_id}/download")
def download_package(package_id: str, purpose: str = Query(default="history_query")) -> dict:
    with connection() as conn:
        return domain.download_package(conn, package_id, purpose)


@app.get("/packages/{package_id}/adoption")
def package_adoption(package_id: str) -> dict:
    with connection() as conn:
        return domain.package_adoption_analytics(conn, package_id)


@app.post("/packages/{package_id}/candidates")
def regenerate_candidates(package_id: str) -> dict:
    """为新包重新生成候选（幂等），用于新上报汇入后补齐影响清单。"""
    with connection() as conn:
        created = domain.generate_candidates(conn, package_id)
        return {"package_id": package_id, "candidates_generated": created}


# ---------------------------------------------------------------- 差异与影响

@app.get("/diffs")
def list_diffs(from_package: str = Query(...), to_package: str = Query(...)) -> dict:
    with connection() as conn:
        return domain.list_diffs(conn, from_package, to_package)


# ---------------------------------------------------------------- 候选与整改要求

@app.get("/candidates")
def list_candidates(status: str | None = None, sign_ref: str | None = None) -> list[dict]:
    with connection() as conn:
        return domain.list_candidates(conn, status=status, sign_ref=sign_ref)


@app.post("/candidates/{candidate_id}/decision")
def decide_candidate(candidate_id: str, body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.decide_candidate(
            conn,
            candidate_id,
            body.get("action", ""),
            body.get("decided_by", ""),
            body.get("note", ""),
        )


@app.get("/requirements")
def list_requirements(
    unit_ref: str | None = None,
    status: str | None = None,
    target_package_id: str | None = None,
) -> list[dict]:
    with connection() as conn:
        return domain.list_requirements(
            conn, unit_ref=unit_ref, status=status, target_package_id=target_package_id
        )


@app.post("/requirements/{requirement_id}/commitment")
def commit_requirement(requirement_id: str, body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.commit_requirement(conn, requirement_id, body.get("committed_deadline", ""))


# ---------------------------------------------------------------- 同步

@app.post("/sync/reports")
def sync_reports(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.ingest_batch(
            conn,
            body.get("source_system", ""),
            body.get("batch_key", ""),
            body.get("events") or [],
        )


# ---------------------------------------------------------------- 限时豁免

@app.post("/exemptions", status_code=201)
def apply_exemption(body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.apply_exemption(
            conn,
            body.get("exemption_id", ""),
            body.get("sign_ref", ""),
            body.get("reason", ""),
            body.get("valid_from", ""),
            body.get("valid_until", ""),
            body.get("decided_by", ""),
            requirement_id=body.get("requirement_id"),
            note=body.get("note", ""),
        )


@app.get("/exemptions")
def list_exemptions(sign_ref: str | None = None) -> list[dict]:
    with connection() as conn:
        return domain.list_exemptions(conn, sign_ref=sign_ref)


@app.get("/exemptions/{exemption_id}")
def get_exemption(exemption_id: str) -> dict:
    with connection() as conn:
        return domain.exemption_state(conn, exemption_id)


@app.post("/exemptions/{exemption_id}/approve")
def approve_exemption(exemption_id: str, body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.approve_exemption(conn, exemption_id, body.get("decided_by", ""), body.get("note", ""))


@app.post("/exemptions/{exemption_id}/revoke")
def revoke_exemption(exemption_id: str, body: dict = Body(...)) -> dict:
    with connection() as conn:
        return domain.revoke_exemption(conn, exemption_id, body.get("decided_by", ""), body.get("note", ""))


@app.post("/exemptions/{exemption_id}/expire")
def expire_exemption(exemption_id: str, body: dict = Body(default={})) -> dict:
    with connection() as conn:
        return domain.expire_exemption(conn, exemption_id, body.get("decided_by", "system"))


# ---------------------------------------------------------------- 追溯与公开查询

@app.get("/signs/{sign_ref}/trace")
def sign_trace(sign_ref: str) -> dict:
    with connection() as conn:
        return domain.sign_trace(conn, sign_ref)


@app.get("/signs/{sign_ref}/versions/{translation_revision}/package")
def version_package(sign_ref: str, translation_revision: int) -> dict:
    with connection() as conn:
        return domain.version_package_lookup(conn, sign_ref, translation_revision)


@app.get("/public/signs/{sign_ref}")
def public_sign(sign_ref: str) -> dict:
    with connection() as conn:
        return domain.public_sign_view(conn, sign_ref)
