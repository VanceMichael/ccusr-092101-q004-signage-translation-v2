"""端到端测试：规范发布与采用追踪的全部业务规则。

每个用例使用独立临时 SQLite 文件，通过 FastAPI TestClient 走真实 HTTP 栈，
并在需要推进时间时 monkeypatch app.clock.now。
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app import clock
from app.main import app

ADMIN_HEADERS = {"X-Admin-Key": "dev-admin-key"}


def iso(dt) -> str:
    return clock.to_iso(dt)


class TrackingTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self._tmp.close()
        os.environ["DATABASE_PATH"] = self._tmp.name
        self.client = TestClient(app)
        self.client.__enter__()  # 触发 lifespan 迁移

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        Path(self._tmp.name).unlink(missing_ok=True)
        os.environ.pop("DATABASE_PATH", None)

    # ------------------------------------------------------------ helpers
    def register_unit(self, unit_id: str, categories: list[str],
                      locations: list[str] | None = None) -> str:
        resp = self.client.post("/admin/units", headers=ADMIN_HEADERS, json={
            "unit_id": unit_id, "name": f"单位-{unit_id}",
            "managed_categories": categories,
            "managed_locations": locations or [],
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["token"]

    def unit_headers(self, unit_id: str, token: str) -> dict:
        return {"X-Unit-Id": unit_id, "X-Unit-Token": token}

    def issue_package(self, category: str, language: str, entries: dict,
                      effective_at: str | None = None, exceptions: list | None = None,
                      basis: list | None = None) -> dict:
        resp = self.client.post("/admin/packages", headers=ADMIN_HEADERS, json={
            "category": category, "language_code": language,
            "effective_at": effective_at or clock.now_iso(),
            "issued_by": "外事办",
            "basis_docs": basis or [{"doc_ref": "DOC-2026-01", "title": "福州公共场所译写规范",
                                     "sha256": "abc"}],
            "entries": entries, "exceptions": exceptions or [],
        })
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def report(self, headers: dict, batch_id: str, items: list[dict]):
        return self.client.post("/units/me/catalog-reports", headers=headers,
                                json={"batch_id": batch_id, "items": items})

    def item(self, sign_ref, location, category, language, term_key, package_id, text):
        return {"sign_ref": sign_ref, "location_code": location, "category": category,
                "language_code": language, "term_key": term_key,
                "package_id": package_id, "observed_text": text}
