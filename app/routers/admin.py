"""管理端：单位、发布包签发/废止、差异、候选决策、豁免审批、修订影响。"""

import sqlite3

from fastapi import APIRouter, Depends

from app.db import get_db
from app.deps import require_admin
from app.models import (
    CandidateDecisionRequest,
    ExemptionApproveRequest,
    ExemptionRevokeRequest,
    PackageIssueRequest,
    SubscribeRequest,
    UnitRegisterRequest,
)
from app.services import diffs, exemptions, packages, units

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------- 单位
@router.post("/units", status_code=201)
def register_unit(req: UnitRegisterRequest, conn: sqlite3.Connection = Depends(get_db)):
    return units.register_unit(conn, req)


@router.get("/units")
def list_units(conn: sqlite3.Connection = Depends(get_db)):
    return units.list_units(conn)


@router.post("/units/{unit_id}/subscriptions", status_code=201)
def admin_subscribe(unit_id: str, req: SubscribeRequest,
                    conn: sqlite3.Connection = Depends(get_db)):
    return units.subscribe(conn, unit_id, req)


# ---------------------------------------------------------------- 发布包
@router.post("/packages", status_code=201)
def issue_package(req: PackageIssueRequest, conn: sqlite3.Connection = Depends(get_db)):
    return packages.issue_package(conn, req)


@router.get("/packages")
def list_packages(category: str | None = None, language_code: str | None = None,
                  conn: sqlite3.Connection = Depends(get_db)):
    return packages.list_packages(conn, category, language_code)


@router.get("/packages/{package_id}")
def get_package(package_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return packages.get_package(conn, package_id)


@router.post("/packages/{package_id}/withdraw")
def withdraw_package(package_id: str, body: ExemptionRevokeRequest,
                     conn: sqlite3.Connection = Depends(get_db)):
    return packages.withdraw_package(conn, package_id, "admin", body.reason)


# ---------------------------------------------------------------- 差异 / 影响 / 候选
@router.post("/diffs", status_code=201)
def create_diff(body: dict, conn: sqlite3.Connection = Depends(get_db)):
    return diffs.generate_diff(conn, body["new_package_id"], body.get("old_package_id"))


@router.get("/diffs/{diff_id}")
def get_diff(diff_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return diffs.get_diff(conn, diff_id)


@router.get("/candidates")
def list_candidates(status: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    return diffs.list_candidates(conn, status)


@router.post("/candidates/{candidate_id}/decision")
def decide_candidate(candidate_id: int, req: CandidateDecisionRequest,
                     conn: sqlite3.Connection = Depends(get_db)):
    return diffs.decide_candidate(conn, candidate_id, "admin", req)


@router.get("/requirements")
def list_requirements(conn: sqlite3.Connection = Depends(get_db)):
    return diffs.list_requirements(conn)


# ---------------------------------------------------------------- 豁免审批
@router.post("/exemptions/sweep")
def sweep_exemptions(conn: sqlite3.Connection = Depends(get_db)):
    return {"expired": exemptions.sweep_expirations(conn)}


@router.post("/requirements/{req_id}/exemption/approve")
def approve_exemption(req_id: str, req: ExemptionApproveRequest,
                      conn: sqlite3.Connection = Depends(get_db)):
    return exemptions.approve(conn, req_id, "admin", req.valid_until, req.reason)


@router.post("/requirements/{req_id}/exemption/revoke")
def revoke_exemption(req_id: str, req: ExemptionRevokeRequest,
                     conn: sqlite3.Connection = Depends(get_db)):
    return exemptions.revoke(conn, req_id, "admin", req.reason)


# ---------------------------------------------------------------- 修订影响视图
@router.get("/revisions/{package_id}/impact")
def revision_impact(package_id: str, conn: sqlite3.Connection = Depends(get_db)):
    return diffs.revision_impact(conn, package_id)
