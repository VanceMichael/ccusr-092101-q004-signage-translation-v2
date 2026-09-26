"""发布包签名、生命周期、鉴权与订阅范围。"""

import sqlite3
from datetime import timedelta
from unittest.mock import patch

from app import clock, security
from tests.test_tracking_base import ADMIN_HEADERS, TrackingTestBase, iso


class PackageLifecycleTest(TrackingTestBase):
    def test_health_and_auth(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.post("/admin/packages", json={}).status_code, 401)
        # 错误管理密钥
        self.assertEqual(
            self.client.post("/admin/units", headers={"X-Admin-Key": "wrong"}, json={}).status_code,
            401)

    def test_unit_token_required(self):
        self.register_unit("U-AIRPORT", ["airport"])
        r = self.client.post("/units/me/catalog-reports",
                             headers={"X-Unit-Id": "U-AIRPORT", "X-Unit-Token": "bad"},
                             json={"batch_id": "b", "items": []})
        self.assertEqual(r.status_code, 401)

    def test_signed_package_and_tamper_detection(self):
        pkg = self.issue_package("airport", "en", {"exit": {"term": "出口", "translation": "Exit"}})
        self.assertTrue(pkg["signature_valid"])
        conn = sqlite3.connect(self._tmp.name)
        row = conn.execute("SELECT canonical_bytes, signature FROM packages").fetchone()
        self.assertTrue(security.verify(row[0], row[1]))
        tampered = bytes([row[0][0] ^ 1]) + row[0][1:]
        self.assertFalse(security.verify(tampered, row[1]))
        conn.close()

    def test_subscribe_must_be_within_managed_scope(self):
        token = self.register_unit("U-AIRPORT", ["airport"])
        h = self.unit_headers("U-AIRPORT", token)
        r = self.client.post("/units/me/subscriptions", headers=h, json={"categories": ["hospital"]})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/units/me/subscriptions", headers=h, json={"categories": ["airport"]})
        self.assertEqual(r.status_code, 201)

    def test_future_package_pending_then_active(self):
        future = clock.now() + timedelta(days=10)
        pkg = self.issue_package("airport", "en",
                                 {"exit": {"term": "出口", "translation": "Exit"}},
                                 effective_at=iso(future))
        self.assertEqual(pkg["status"], "pending")
        with patch("app.clock.now", return_value=future + timedelta(days=1)):
            got = self.client.get(f"/admin/packages/{pkg['package_id']}",
                                  headers=ADMIN_HEADERS).json()
            self.assertEqual(got["status"], "active")
