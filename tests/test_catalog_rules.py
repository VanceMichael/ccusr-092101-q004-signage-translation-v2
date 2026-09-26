"""标识上报规则：合法引用、废止限制、只进不退、历史查询与版本反查。"""

from app import clock
from tests.test_tracking_base import ADMIN_HEADERS, TrackingTestBase, iso


class CatalogRulesTest(TrackingTestBase):
    def setUp(self):
        super().setUp()
        self.token = self.register_unit("U-H", ["hospital", "road"])
        self.h = self.unit_headers("U-H", self.token)
        self.p1 = self.issue_package("hospital", "en",
                                     {"exit": {"term": "出口", "translation": "Exit"}})

    def test_new_sign_must_use_current_recommended(self):
        # 直接用未来才会存在的场景：先发 p2，再尝试用旧 p1 新建标识
        p2 = self.issue_package("hospital", "en",
                                {"exit": {"term": "出口", "translation": "Way Out"}})
        r = self.report(self.h, "B1", [
            self.item("SNEW", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        body = r.json()
        self.assertFalse(body["accepted"])
        self.assertFalse(body["items"][0]["accepted"])
        self.assertIn("只能引用当前推荐", body["items"][0]["reason"])
        self.assertEqual(body["items"][0]["recommended_package_id"], p2["package_id"])

    def test_withdrawn_package_cannot_be_referenced_but_history_remains(self):
        # 既有现场版本合法引用 p1
        self.assertTrue(self.report(self.h, "B1", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")
        ]).json()["accepted"])
        # 废止 p1（无新版本接续）
        r = self.client.post(f"/admin/packages/{self.p1['package_id']}/withdraw",
                             headers=ADMIN_HEADERS, json={"reason": "词条作废"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "withdrawn")
        # 废止包不能再被任何标识引用
        r = self.report(self.h, "B2", [
            self.item("S2", "FZ-H2", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        self.assertIn("废止", r.json()["items"][0]["reason"])
        # 旧版仍支持历史查询，但明确不可用于新建
        snap = self.client.get(f"/public/packages/{self.p1['package_id']}").json()
        self.assertEqual(snap["status"], "withdrawn")
        self.assertFalse(snap["usable_for_new_signs"])

    def test_existing_sign_can_keep_old_version_during_rollout(self):
        self.report(self.h, "B1", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        p2 = self.issue_package("hospital", "en",
                                {"exit": {"term": "出口", "translation": "Way Out"}})
        # 分批换版：既有标识重新上报同一旧版本（重复盘点）仍合法，不倒退也不报错
        r = self.report(self.h, "B2", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        self.assertTrue(r.json()["accepted"])
        self.assertTrue(r.json()["items"][0].get("duplicate"))
        # 旧版历史包可查
        snap = self.client.get(f"/public/packages/{self.p1['package_id']}").json()
        self.assertEqual(snap["status"], "superseded")
        self.assertFalse(snap["usable_for_new_signs"])
        snap2 = self.client.get(f"/public/packages/{p2['package_id']}").json()
        self.assertTrue(snap2["usable_for_new_signs"])

    def test_adoption_cannot_regress_to_older_package(self):
        self.report(self.h, "B1", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        p2 = self.issue_package("hospital", "en",
                                {"exit": {"term": "出口", "translation": "Way Out"}})
        self.report(self.h, "B2", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", p2["package_id"], "Way Out")])
        # 迟到的旧同步不得让进度倒退
        r = self.report(self.h, "B3", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        self.assertFalse(r.json()["accepted"])
        self.assertIn("倒退", r.json()["items"][0]["reason"])
        view = self.client.get("/public/signs/S1").json()
        self.assertEqual(view["items"][0]["actual"]["package_id"], p2["package_id"])

    def test_out_of_scope_unit_rejected_at_item_level(self):
        other = self.register_unit("U-R", ["road"])
        oh = self.unit_headers("U-R", other)
        r = self.report(oh, "B1", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        self.assertFalse(r.json()["items"][0]["accepted"])

    def test_trace_version_to_legal_package(self):
        self.report(self.h, "B1", [
            self.item("S1", "FZ-H1", "hospital", "en", "exit", self.p1["package_id"], "Exit")])
        versions = self.client.get("/public/signs/S1").json()
        trace = self.client.get("/public/versions/1").json()
        self.assertEqual(trace["sign_ref"], "S1")
        self.assertEqual(trace["referenced_package"]["package_id"], self.p1["package_id"])
        self.assertTrue(trace["was_legally_referenceable"])
        self.assertEqual(trace["referenced_package"]["status_at_report_time"], "active")
        self.assertTrue(trace["referenced_package"]["signature_valid"])

    def test_trace_judges_legality_at_report_time_not_now(self):
        from datetime import datetime
        from unittest.mock import patch
        from app import clock as clk

        aug25 = datetime(2026, 8, 25, 9, 0, tzinfo=clk.UTC)
        sep1 = datetime(2026, 9, 1, 0, 0, tzinfo=clk.UTC)
        sep10 = datetime(2026, 9, 10, 9, 0, tzinfo=clk.UTC)
        sep15 = datetime(2026, 9, 15, 9, 0, tzinfo=clk.UTC)
        sep20 = datetime(2026, 9, 20, 0, 0, tzinfo=clk.UTC)
        sep25 = datetime(2026, 9, 25, 9, 0, tzinfo=clk.UTC)

        with patch("app.clock.now", return_value=aug25):
            p1 = self.issue_package(
                "hospital", "en", {"exit": {"term": "出口", "translation": "Exit"}},
                effective_at=iso(sep1))
        with patch("app.clock.now", return_value=sep10):
            self.report(self.h, "B1", [
                self.item("S1", "FZ-H1", "hospital", "en", "exit", p1["package_id"], "Exit")])
        with patch("app.clock.now", return_value=sep15):
            self.issue_package(
                "hospital", "en", {"exit": {"term": "出口", "translation": "Way Out"}},
                effective_at=iso(sep20))

        # 如今（9/25）v1 已被取代
        with patch("app.clock.now", return_value=sep25):
            now_snap = self.client.get(f"/public/packages/{p1['package_id']}").json()
            self.assertEqual(now_snap["status"], "superseded")
            # 但该版本上报时刻（9/10）v1 仍 active，反查必须判定当时合法
            trace = self.client.get("/public/versions/1").json()
        self.assertEqual(trace["referenced_package"]["status_at_report_time"], "active")
        self.assertTrue(trace["was_legally_referenceable"])
