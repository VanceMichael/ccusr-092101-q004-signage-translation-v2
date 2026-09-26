"""端到端场景：多载体、多责任单位在一次规范修订下的可观测性。"""

from datetime import timedelta

from app import clock
from tests.test_tracking_base import ADMIN_HEADERS, TrackingTestBase

CATEGORIES = ["airport", "hospital", "scenic", "road", "gov_web"]


class MultiCarrierScenarioTest(TrackingTestBase):
    def test_one_revision_shows_all_carriers_units_and_true_rate(self):
        units = {}
        headers = {}
        for idx, cat in enumerate(CATEGORIES):
            uid = f"U-{cat.upper()}"
            units[cat] = uid
            headers[cat] = self.unit_headers(uid, self.register_unit(uid, [cat]))
            self.client.post("/units/me/subscriptions", headers=headers[cat],
                             json={"categories": [cat]})

        p1 = self.issue_package("gov_web", "en",
                                {"service": {"term": "政务服务", "translation": "Government Affairs"}})
        # 政务网页与另一个（道路在同语种也建一个不同类别包，互不串扰）
        road_p1 = self.issue_package("road", "en",
                                     {"service": {"term": "服务", "translation": "Service"}})
        self.report(headers["gov_web"], "G1", [
            self.item("GW1", "FZ-GOV", "gov_web", "en", "service", p1["package_id"],
                      "Government Affairs")])
        self.report(headers["road"], "R1", [
            self.item("RD1", "FZ-RD", "road", "en", "service", road_p1["package_id"], "Service")])

        # 新版只改政务网页类别；道路类别不受影响（类别隔离）
        p2 = self.issue_package("gov_web", "en",
                                {"service": {"term": "政务服务", "translation": "Government Services"}})
        diff = self.client.post("/admin/diffs", headers=ADMIN_HEADERS,
                                json={"new_package_id": p2["package_id"]}).json()
        self.assertEqual([i["sign_ref"] for i in diff["impact"]], ["GW1"])
        cid = diff["candidates"][0]["id"]

        # 承诺期限已过但尚未完成 → 逾期
        past = clock.to_iso(clock.now() - timedelta(days=2))
        self.client.post(f"/admin/candidates/{cid}/decision", headers=ADMIN_HEADERS, json={
            "action": "confirm", "promised_by": past})
        impact = self.client.get(f"/admin/revisions/{p2['package_id']}/impact",
                                 headers=ADMIN_HEADERS).json()
        self.assertEqual(impact["affected_signs"], ["GW1"])
        self.assertEqual(impact["affected_units"], ["U-GOV_WEB"])
        self.assertEqual(impact["overdue"], 1)
        self.assertEqual(impact["completion_rate"], 0.0)

        # 道路标识的公开查询仍为对齐，证明跨类别修订不波及
        road_view = self.client.get("/public/signs/RD1").json()
        self.assertEqual(road_view["summary"]["aligned"], 1)

        # 政务网页完成换版
        self.report(headers["gov_web"], "G2", [
            self.item("GW1", "FZ-GOV", "gov_web", "en", "service", p2["package_id"],
                      "Government Services")])
        impact = self.client.get(f"/admin/revisions/{p2['package_id']}/impact",
                                 headers=ADMIN_HEADERS).json()
        self.assertEqual(impact["completion_rate"], 1.0)
        self.assertEqual(impact["overdue"], 0)

        # 从历史版本(vid=1) 反查它当时合法引用的发布包
        trace = self.client.get("/public/versions/1").json()
        self.assertTrue(trace["was_legally_referenceable"])
        self.assertEqual(trace["referenced_package"]["package_id"], p1["package_id"])
        # 该旧包如今已被取代，但历史仍可查、不可用于新建
        snap = self.client.get(f"/public/packages/{p1['package_id']}").json()
        self.assertEqual(snap["status"], "superseded")
        self.assertFalse(snap["usable_for_new_signs"])
