"""核心修订闭环：差异影响 → 候选需确认 → 更新要求 → 承诺 → 真实完成比例。"""

from app import clock
from tests.test_tracking_base import ADMIN_HEADERS, TrackingTestBase


class RevisionWorkflowTest(TrackingTestBase):
    def setUp(self):
        super().setUp()
        self.token = self.register_unit("U-AIRPORT", ["airport"])
        self.h = self.unit_headers("U-AIRPORT", self.token)
        self.client.post("/units/me/subscriptions", headers=self.h, json={"categories": ["airport"]})

        self.p1 = self.issue_package("airport", "en", {
            "exit": {"term": "安全出口", "translation": "Exit"},
            "info": {"term": "问讯处", "translation": "Information"},
        })
        # 两个标识引用首版
        self.assertTrue(self.report(self.h, "B1", [
            self.item("S1", "FOC-T1", "airport", "en", "exit", self.p1["package_id"], "Exit"),
            self.item("S2", "FOC-T1", "airport", "en", "info", self.p1["package_id"], "Information"),
        ]).json()["accepted"])

        self.p2 = self.issue_package("airport", "en", {
            "exit": {"term": "安全出口", "translation": "Emergency Exit"},
            "info": {"term": "问讯处", "translation": "Information"},
        })

    def test_diff_lists_changes_and_impact_and_candidates(self):
        r = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                             json={"new_package_id": self.p2["package_id"]})
        self.assertEqual(r.status_code, 201, r.text)
        diff = r.json()
        self.assertEqual([c["term_key"] for c in diff["changes"]["changed"]], ["exit"])
        affected = {i["sign_ref"] for i in diff["impact"]}
        self.assertEqual(affected, {"S1"})  # info 未变，不受影响
        self.assertEqual(len(diff["candidates"]), 1)
        self.assertEqual(diff["candidates"][0]["status"], "proposed")

    def test_candidate_must_be_confirmed_before_requirement_exists(self):
        diff = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                                json={"new_package_id": self.p2["package_id"]}).json()
        cid = diff["candidates"][0]["id"]
        # 确认前不存在更新要求
        self.assertEqual(self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json(), [])
        # 自动产生的候选不能自动要求更新：单位端没有强制要求
        reqs = self.client.get("/units/me/requirements", headers=self.h).json()
        self.assertEqual(reqs, [])
        # 专业人员确认
        r = self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS, json={
            "action": "confirm", "reason": "译法已审定",
            "promised_by": clock.to_iso(clock.now().replace(microsecond=0)),
        })
        self.assertEqual(r.status_code, 200, r.text)
        req = r.json()
        self.assertEqual(req["sign_ref"], "S1")
        self.assertIsNone(req["completed_at"])
        # 重复确认冲突，不能产生第二条要求
        r2 = self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS,
                              json={"action": "confirm"})
        self.assertEqual(r2.status_code, 409)

    def test_reject_candidate_leaves_no_requirement(self):
        diff = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                                json={"new_package_id": self.p2["package_id"]}).json()
        cid = diff["candidates"][0]["id"]
        r = self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS,
                             json={"action": "reject", "reason": "现场空间不足暂缓"})
        self.assertEqual(r.json()["status"], "rejected")
        self.assertEqual(self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json(), [])

    def _confirmed_req(self):
        diff = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                                json={"new_package_id": self.p2["package_id"]}).json()
        cid = diff["candidates"][0]["id"]
        self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS,
                         json={"action": "confirm", "promised_by": clock.now_iso()})
        req = self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json()[0]
        return req["req_id"]

    def test_completion_and_revision_impact_rate(self):
        req_id = self._confirmed_req()
        impact = self.client.get(f"/admin/revisions/{self.p2['package_id']}/impact",
                                 headers=ADMIN_HEADERS).json()
        self.assertEqual(impact["requirement_total"], 1)
        self.assertEqual(impact["completed"], 0)
        self.assertEqual(impact["completion_rate"], 0.0)
        self.assertIn("U-AIRPORT", impact["affected_units"])
        self.assertIn("S1", impact["affected_signs"])
        self.assertEqual(impact["pending_candidates"], 0)

        # 现场换版上报 → 自动回填真实完成
        r = self.report(self.h, "B2", [
            self.item("S1", "FOC-T1", "airport", "en", "exit", self.p2["package_id"],
                      "Emergency Exit")])
        self.assertTrue(r.json()["accepted"])
        req = self.client.get("/admin/requirements", headers=ADMIN_HEADERS).json()[0]
        self.assertIsNotNone(req["completed_at"])
        impact = self.client.get(f"/admin/revisions/{self.p2['package_id']}/impact",
                                 headers=ADMIN_HEADERS).json()
        self.assertEqual(impact["completion_rate"], 1.0)
        self.assertEqual(impact["completed"], 1)

    def test_public_view_shows_recommended_vs_actual_gap(self):
        # 换版前：S1 现场实际与当前推荐不一致
        view = self.client.get("/public/signs/S1").json()
        item = next(i for i in view["items"] if i["term_key"] == "exit")
        self.assertEqual(item["state"], "differs")
        self.assertEqual(item["actual"]["observed_text"], "Exit")
        self.assertEqual(item["recommended"]["translation"], "Emergency Exit")
        self.assertEqual(view["summary"]["differs"], 1)
