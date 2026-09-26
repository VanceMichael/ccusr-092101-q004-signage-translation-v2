"""单位端：订阅、目录批次上报（离线幂等）、通知回执、承诺期限、豁免申请。"""

import sqlite3

from fastapi import APIRouter, Depends

from app.db import get_db
from app.deps import require_unit
from app.models import (
    CatalogReport,
    ExemptionRequest,
    PromiseRequest,
    SubscribeRequest,
)
from app.services import diffs, exemptions, signs, units

router = APIRouter(prefix="/units/me", tags=["unit"])


@router.get("")
def me(unit: dict = Depends(require_unit)):
    return {"unit_id": unit["unit_id"], "name": unit["name"],
            "managed_categories": unit["managed_categories"],
            "managed_locations": unit["managed_locations"]}


@router.post("/subscriptions", status_code=201)
def subscribe(req: SubscribeRequest, unit: dict = Depends(require_unit),
              conn: sqlite3.Connection = Depends(get_db)):
    return units.subscribe(conn, unit["unit_id"], req)


@router.post("/catalog-reports", status_code=202)
def report_catalog(report: CatalogReport, unit: dict = Depends(require_unit),
                   conn: sqlite3.Connection = Depends(get_db)):
    """离线回执：同 batch_id 重放返回首次回执，且不产生任何新状态。"""
    return signs.report_catalog(conn, unit["unit_id"], report)


@router.get("/notifications")
def list_notifications(unit: dict = Depends(require_unit),
                       conn: sqlite3.Connection = Depends(get_db)):
    rows = conn.execute(
        "SELECT n.package_id, n.category, n.notified_at, n.acked_at,"
        " p.effective_at FROM notifications n JOIN packages p ON p.package_id = n.package_id"
        " WHERE n.unit_id = ? ORDER BY n.nid", (unit["unit_id"],),
    ).fetchall()
    return {"notifications": [dict(r) for r in rows]}


@router.get("/requirements")
def my_requirements(unit: dict = Depends(require_unit),
                    conn: sqlite3.Connection = Depends(get_db)):
    return diffs.list_requirements(conn, unit["unit_id"])


@router.post("/requirements/{req_id}/promise")
def promise(req_id: str, req: PromiseRequest, unit: dict = Depends(require_unit),
            conn: sqlite3.Connection = Depends(get_db)):
    return diffs.set_promise(conn, unit["unit_id"], req_id, req.promised_by)


@router.post("/requirements/{req_id}/exemption", status_code=201)
def request_exemption(req_id: str, req: ExemptionRequest, unit: dict = Depends(require_unit),
                      conn: sqlite3.Connection = Depends(get_db)):
    return exemptions.request_exemption(
        conn, unit["unit_id"], req_id, req.reason, req.valid_until, req.kind)
