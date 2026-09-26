"""限时豁免决定链（批准/到期/撤销不可覆盖）与离线回执幂等。"""

import sqlite3
from datetime import timedelta
from unittest.mock import patch

from app import clock
from tests.test_tracking_base import ADMIN_HEADERS, TrackingTestBase


class ExemptionTest(TrackingTestBase):
    def setUp(self):
        super().setUp()
        self.token = self.register_unit("U-S", ["scenic"])
        self.h = self.unit_headers("U-S", self.token)
        p1 = self.issue_package("scenic", "en",
                                {"guide": {"term": "导览", "translation": "Guide"}})
        self.report(self.h, "B1", [
            self.item("S1", "FZ-S1", "scenic", "en", "guide", p1["package_id"], "Guide")])
        p2 = self.issue_package("scenic", "en",
                                {"guide": {"term": "导览", "translation": "Visitor Guide"}})
        diff = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                                json={"new_package_id": p2["package_id"]}).json()
        cid = diff["candidates"][0]["id"]
        self.p2_id = p2["package_id"]
        self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS,
                         json={"action": "confirm", "promised_by": clock.now_iso()})
        self.req_id = self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json()[0]["req_id"]

    def test_full_exemption_chain_never_overwritten(self):
        until = clock.now() + timedelta(days=30)
        # 单位申请（历史建筑）
        r = self.client.post(f"/units/me/requirements/{self.req_id}/exemption", headers=self.h, json={
            "reason": "匾额为历史建筑构件需定制", "valid_until": clock.to_iso(until),
            "kind": "historic_building"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["status"], "pending")
        # 未申请时不能直接批准；此处申请存在，管理员批准
        r = self.client.post(f"/admin/requirements/{self.req_id}/exemption/approve",
                             headers=ADMIN_HEADERS,
                             json={"valid_until": clock.to_iso(until), "reason": "同意"})
        self.assertEqual(r.json()["status"], "active")
        # 修订影响视图中豁免生效、不算逾期
        impact = self.client.get(f"/admin/revisions/{self.p2_id}/impact",
                                 headers=ADMIN_HEADERS).json()
        self.assertEqual(impact["exempt_active"], 1)
        self.assertEqual(impact["overdue"], 0)
        # 撤销：原批准决定仍保留在历史中
        r = self.client.post(f"/admin/requirements/{self.req_id}/exemption/revoke",
                             headers=ADMIN_HEADERS, json={"reason": "施工提前"})
        self.assertEqual(r.json()["status"], "revoked")
        actions = [e["action"] for e in r.json()["history"]]
        self.assertEqual(actions, ["request", "approve", "revoke"])
        # 撤销后可重新批准（新决定叠加，不覆盖）
        until2 = clock.now() + timedelta(days=60)
        r = self.client.post(f"/admin/requirements/{self.req_id}/exemption/approve",
                             headers=ADMIN_HEADERS,
                             json={"valid_until": clock.to_iso(until2), "reason": "再次同意"})
        self.assertEqual(r.status_code, 200, r.text)
        state = self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json()[0]["exemption"]
        self.assertEqual(state["status"], "active")
        self.assertEqual([e["action"] for e in state["history"]],
                         ["request", "approve", "revoke", "approve"])

    def test_expiration_is_recorded_without_erasing_approval(self):
        until = clock.now() + timedelta(days=5)
        self.client.post(f"/units/me/requirements/{self.req_id}/exemption", headers=self.h, json={
            "reason": "施工周期", "valid_until": clock.to_iso(until), "kind": "construction"})
        self.client.post(f"/admin/requirements/{self.req_id}/exemption/approve",
                         headers=ADMIN_HEADERS,
                         json={"valid_until": clock.to_iso(until), "reason": "同意"})
        with patch("app.clock.now", return_value=until + timedelta(days=1)):
            swept = self.client.post("/admin/exemptions/sweep", headers=ADMIN_HEADERS).json()
            self.assertIn(self.req_id, swept["expired"])
            req = self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json()[0]
            self.assertEqual(req["exemption"]["status"], "expired")
            actions = [e["action"] for e in req["exemption"]["history"]]
            self.assertEqual(actions, ["request", "approve", "expire"])  # 批准仍在
            # 过期后撤销被拒绝
            r = self.client.post(f"/admin/requirements/{self.req_id}/exemption/revoke",
                                 headers=ADMIN_HEADERS, json={"reason": "x"})
            self.assertEqual(r.status_code, 409)


class OfflineReceiptTest(TrackingTestBase):
    def test_repeated_batch_replays_same_receipt_without_progress(self):
        token = self.register_unit("U-R", ["road"])
        h = self.unit_headers("U-R", token)
        p1 = self.issue_package("road", "en",
                                {"park": {"term": "停车", "translation": "Parking"}})
        payload = {"batch_id": "OFFLINE-7", "items": [
            self.item("R1", "FZ-R1", "road", "en", "park", p1["package_id"], "Parking")]}
        first = self.client.post("/units/me/catalog-reports", headers=h, json=payload)
        self.assertEqual(first.json()["replayed"], False)
        first_vid = first.json()["items"][0]["vid"]
        # 完全相同的离线批次重复同步
        second = self.client.post("/units/me/catalog-reports", headers=h, json=payload)
        body = second.json()
        self.assertTrue(body["replayed"])
        self.assertEqual(body["items"][0]["vid"], first_vid)
        # 数据库里只有一个现场版本，批次只有一行
        conn = sqlite3.connect(self._tmp.name)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM sign_versions WHERE sign_ref='R1'").fetchone()[0], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM sync_batches WHERE batch_id='OFFLINE-7'").fetchone()[0], 1)
        conn.close()

    def test_subscription_backfill_and_ack(self):
        p1 = self.issue_package("airport", "en",
                                {"exit": {"term": "出口", "translation": "Exit"}})
        token = self.register_unit("U-A", ["airport"])
        h = self.unit_headers("U-A", token)
        # 后订阅也能收到已发布包（离线补投）
        self.client.post("/units/me/subscriptions", headers=h, json={"categories": ["airport"]})
        notes = self.client.get("/units/me/notifications", headers=h).json()["notifications"]
        self.assertEqual(notes[0]["package_id"], p1["package_id"])
        self.assertIsNone(notes[0]["acked_at"])
        self.report(h, "B1", [
            self.item("A1", "FOC-A1", "airport", "en", "exit", p1["package_id"], "Exit")])
        notes = self.client.get("/units/me/notifications", headers=h).json()["notifications"]
        self.assertIsNotNone(notes[0]["acked_at"])
