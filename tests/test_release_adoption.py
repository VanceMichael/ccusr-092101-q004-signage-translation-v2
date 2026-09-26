
import os
import tempfile
import unittest

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="spec-adoption-"), "test.sqlite3")
os.environ["RELEASE_SIGNING_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

T_PAST = "2026-01-01T00:00:00+08:00"
T_NOW_FROM = "2026-09-01T00:00:00+08:00"
T_NOW_UNTIL = "2026-12-31T23:59:59+08:00"
T_FUTURE = "2027-01-01T00:00:00+08:00"


def entry(key, target, language="en", category="road", source="出口", note=""):
    return {
        "entry_key": key,
        "language_code": language,
        "place_category": category,
        "source_text": source,
        "target_text": target,
        "note": note,
    }


class ApiTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def setUp(self) -> None:
        # 每个用例独立数据库文件，互不影响
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(path)
        os.environ["DATABASE_PATH"] = path
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

    # ---- 通用准备 ----
    def make_unit(self, unit_ref="UNIT-TRAFFIC", scope=("place_category", "road")):
        resp = self.client.post("/units", json={"unit_ref": unit_ref, "name": "交管单位"})
        self.assertEqual(resp.status_code, 201, resp.text)
        resp = self.client.post(
            "/subscriptions",
            json={
                "subscription_id": f"SUB-{unit_ref}",
                "unit_ref": unit_ref,
                "scope_type": scope[0],
                "scope_value": scope[1],
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def make_sign(self, sign_ref="SIGN-001", unit_ref="UNIT-TRAFFIC", category="road"):
        resp = self.client.post(
            "/signs",
            json={
                "sign_ref": sign_ref,
                "unit_ref": unit_ref,
                "location_code": "FZ-001",
                "place_category": category,
                "carrier_type": "道路标牌",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def publish(self, package_id, revision, entries, supersedes=None, effective_from=T_PAST):
        body = {
            "package_id": package_id,
            "revision": revision,
            "title": f"规范第{revision}版",
            "effective_from": effective_from,
            "supersedes_package_id": supersedes,
            "entries": entries,
            "exceptions": [
                {
                    "exception_key": "EX-1",
                    "language_code": "en",
                    "place_category": "scenic",
                    "description": "历史建筑名称保留约定译法",
                }
            ],
            "basis_documents": [
                {"document_key": "DOC-1", "title": "公共场所外语译写规范", "reference": "sha256:abc"}
            ],
        }
        resp = self.client.post("/packages", json=body)
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def sync(self, batch_key, events, source="unit-gateway"):
        resp = self.client.post(
            "/sync/reports",
            json={"source_system": source, "batch_key": batch_key, "events": events},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    @staticmethod
    def report_event(event_id, seq, revision, package_id, translation,
                     sign_ref="SIGN-001", entry_key="E-EXIT", language="en"):
        return {
            "event_id": event_id,
            "seq": seq,
            "reported_at": "2026-09-20T10:00:00+08:00",
            "sign_ref": sign_ref,
            "language_code": language,
            "entry_key": entry_key,
            "translation_revision": revision,
            "package_id": package_id,
            "translation": translation,
        }


class ReleasePackageTest(ApiTestBase):
    def test_publish_signs_and_verifies(self):
        view = self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        self.assertTrue(view["signature_valid"])
        self.assertEqual(view["status"], "effective")
        self.assertEqual(view["entries"][0]["target_text"], "Exit")
        self.assertEqual(view["exceptions"][0]["exception_key"], "EX-1")
        self.assertEqual(view["basis_documents"][0]["document_key"], "DOC-1")

        again = self.client.get("/packages/PKG-1")
        self.assertTrue(again.json()["signature_valid"])

    def test_signature_stable_when_optional_fields_omitted(self):
        # 请求中省略 note / exceptions / basis_documents 时，签名负载与落库形态一致
        resp = self.client.post(
            "/packages",
            json={
                "package_id": "PKG-MIN",
                "revision": 1,
                "title": "最小包",
                "effective_from": T_PAST,
                "entries": [
                    {
                        "entry_key": "E-EXIT",
                        "language_code": "en",
                        "place_category": "road",
                        "source_text": "出口",
                        "target_text": "Exit",
                    }
                ],
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertTrue(resp.json()["signature_valid"])
        self.assertTrue(self.client.get("/packages/PKG-MIN").json()["signature_valid"])

    def test_publish_requires_offset_time_and_entries(self):
        body = {
            "package_id": "PKG-BAD",
            "revision": 1,
            "title": "x",
            "effective_from": "2026-01-01 00:00:00",
            "entries": [entry("E-EXIT", "Exit")],
        }
        resp = self.client.post("/packages", json=body)
        self.assertEqual(resp.status_code, 422)
        body["effective_from"] = T_PAST
        body["entries"] = []
        resp = self.client.post("/packages", json=body)
        self.assertEqual(resp.status_code, 422)

    def test_revision_must_increase_along_chain(self):
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        resp = self.client.post(
            "/packages",
            json={
                "package_id": "PKG-2",
                "revision": 1,
                "title": "x",
                "effective_from": T_PAST,
                "supersedes_package_id": "PKG-1",
                "entries": [entry("E-EXIT", "Way Out")],
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_old_package_history_query_but_not_new_sign_download(self):
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        self.publish("PKG-2", 2, [entry("E-EXIT", "Way Out")], supersedes="PKG-1")

        resp = self.client.get("/packages/PKG-1/download", params={"purpose": "new_sign"})
        self.assertEqual(resp.status_code, 409)
        resp = self.client.get("/packages/PKG-1/download", params={"purpose": "history_query"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["signature_valid"])
        resp = self.client.get("/packages/PKG-2/download", params={"purpose": "new_sign"})
        self.assertEqual(resp.status_code, 200)

        # 废止当前推荐包后，它也不能再用于新建标识
        resp = self.client.post(
            "/packages/PKG-2/deprecate", json={"effective_at": "2026-09-21T00:00:00+08:00"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deprecated")
        resp = self.client.get("/packages/PKG-2/download", params={"purpose": "new_sign"})
        self.assertEqual(resp.status_code, 409)
        # 历史查询仍然可用
        resp = self.client.get("/packages/PKG-2/download", params={"purpose": "history_query"})
        self.assertEqual(resp.status_code, 200)
        # 重复废止被拒绝，原废止事件不被覆盖
        resp = self.client.post(
            "/packages/PKG-2/deprecate", json={"effective_at": "2026-09-22T00:00:00+08:00"}
        )
        self.assertEqual(resp.status_code, 409)


class AdoptionCycleTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.make_unit()
        self.make_sign()
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        result = self.sync("B-1", [self.report_event("EV-1", 1, 1, "PKG-1", "Exit")])
        self.assertEqual(result["accepted"], 1)

    def publish_v2(self):
        return self.publish("PKG-2", 2, [entry("E-EXIT", "Way Out")], supersedes="PKG-1")

    def test_diff_and_candidates_generated_on_publish(self):
        view = self.publish_v2()
        self.assertEqual(view["candidates_generated"], 1)

        diffs = self.client.get("/diffs", params={"from_package": "PKG-1", "to_package": "PKG-2"})
        payload = diffs.json()
        self.assertEqual(payload["diffs"][0]["change_type"], "modified")
        self.assertEqual(payload["diffs"][0]["old_target"], "Exit")
        self.assertEqual(payload["diffs"][0]["new_target"], "Way Out")
        affected = payload["impacts"][0]["affected"]
        self.assertEqual([a["sign_ref"] for a in affected], ["SIGN-001"])

        candidates = self.client.get("/candidates", params={"status": "pending"}).json()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["current_translation"], "Exit")
        self.assertEqual(candidates[0]["suggested_translation"], "Way Out")
        self.assertEqual(candidates[0]["based_on_revision"], 1)

        # 重复生成幂等，不产生重复候选
        again = self.client.post("/packages/PKG-2/candidates")
        self.assertEqual(again.json()["candidates_generated"], 0)

    def confirm_first_candidate(self):
        """确认当前唯一候选并返回生成的整改要求编号。"""
        candidate = self.client.get("/candidates").json()[0]
        resp = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": "EXPERT-01"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()["requirement_id"]

    def test_candidate_requires_professional_confirmation(self):
        self.publish_v2()
        candidate = self.client.get("/candidates").json()[0]

        resp = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": ""},
        )
        self.assertEqual(resp.status_code, 422)

        resp = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": "EXPERT-01"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["requirement_id"])

        # 决定不可更改
        resp = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "dismiss", "decided_by": "EXPERT-02"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_full_cycle_commit_complete_and_analytics(self):
        self.publish_v2()
        requirement_id = self.confirm_first_candidate()

        resp = self.client.post(
            f"/requirements/{requirement_id}/commitment",
            json={"committed_deadline": "2026-10-31T23:59:59+08:00"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["committed_deadline"], "2026-10-31T23:59:59+08:00")

        # 责任单位上报换版后的新译文 -> 自动核验并完成
        result = self.sync("B-2", [self.report_event("EV-2", 2, 2, "PKG-2", "Way Out")])
        self.assertEqual(result["accepted"], 1)
        requirement = self.client.get(
            "/requirements", params={"unit_ref": "UNIT-TRAFFIC"}
        ).json()[0]
        self.assertEqual(requirement["status"], "completed")
        self.assertEqual(requirement["completed_at"], "2026-09-20T02:00:00+00:00")

        analytics = self.client.get("/packages/PKG-2/adoption").json()
        self.assertEqual(analytics["affected_signs"], ["SIGN-001"])
        self.assertEqual(analytics["units"], ["UNIT-TRAFFIC"])
        self.assertEqual(analytics["summary"]["total"], 1)
        self.assertEqual(analytics["summary"]["completed"], 1)
        self.assertEqual(analytics["summary"]["completion_ratio"], 1.0)

        # 公开查询：现场与推荐一致
        public = self.client.get("/public/signs/SIGN-001").json()
        language = public["languages"][0]
        self.assertTrue(language["matches_recommendation"])
        self.assertFalse(language["transitioning"])

    def test_completion_requires_newer_revision_and_exact_translation(self):
        self.publish_v2()
        candidate = self.client.get("/candidates").json()[0]
        self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": "EXPERT-01"},
        )
        # 译文不匹配 -> 不完成
        self.sync("B-2", [self.report_event("EV-2", 2, 2, "PKG-2", "Exit")])
        # 版本未前进 -> 不完成
        self.sync("B-3", [self.report_event("EV-3", 3, 1, "PKG-2", "Way Out")])
        requirement = self.client.get("/requirements").json()[0]
        self.assertEqual(requirement["status"], "open")
        # 正确译文 + 更高版本 -> 完成
        self.sync("B-4", [self.report_event("EV-4", 4, 2, "PKG-2", "Way Out")])
        requirement = self.client.get("/requirements").json()[0]
        self.assertEqual(requirement["status"], "completed")

    def test_dismissed_candidate_creates_no_requirement(self):
        self.publish_v2()
        candidate = self.client.get("/candidates").json()[0]
        resp = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "dismiss", "decided_by": "EXPERT-01", "note": "历史建筑保留"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["requirement_id"])
        self.assertEqual(self.client.get("/requirements").json(), [])


class SyncIntegrityTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.make_unit()
        self.make_sign()
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])

    def test_duplicate_batch_and_events_are_idempotent(self):
        event = self.report_event("EV-1", 1, 1, "PKG-1", "Exit")
        first = self.sync("B-1", [event])
        self.assertEqual(first["accepted"], 1)

        replay = self.sync("B-1", [event])
        self.assertTrue(replay["duplicate_batch"])
        self.assertEqual(replay["accepted"], 1)

        second = self.sync("B-2", [event])
        self.assertEqual(second["accepted"], 0)
        self.assertEqual(second["duplicates"], 1)

        trace = self.client.get("/signs/SIGN-001/trace").json()
        self.assertEqual(len(trace["versions"]), 1)

    def test_stale_sequence_rejected_and_progress_kept(self):
        self.sync("B-1", [self.report_event("EV-1", 5, 1, "PKG-1", "Exit")])
        stale = self.sync("B-2", [self.report_event("EV-2", 3, 1, "PKG-1", "EXIT!!!")])
        self.assertEqual(stale["accepted"], 0)
        self.assertEqual(stale["rejected"], 1)
        self.assertIn("序列号回退", stale["results"][0]["reason"])

        trace = self.client.get("/signs/SIGN-001/trace").json()
        self.assertEqual(len(trace["versions"]), 1)
        self.assertEqual(trace["versions"][0]["translation"], "Exit")

    def test_offline_receipt_after_completion_does_not_regress(self):
        self.publish("PKG-2", 2, [entry("E-EXIT", "Way Out")], supersedes="PKG-1")
        self.sync("B-1", [self.report_event("EV-1", 1, 1, "PKG-1", "Exit")])
        candidate = self.client.get("/candidates").json()[0]
        decided = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": "EXPERT-01"},
        ).json()
        self.sync("B-2", [self.report_event("EV-2", 2, 2, "PKG-2", "Way Out")])
        requirement = self.client.get("/requirements").json()[0]
        self.assertEqual(requirement["status"], "completed")

        # 离线迟到的旧事件（seq 回退）被拒绝，完成状态不倒退
        late = self.sync("B-3", [self.report_event("EV-3", 1, 1, "PKG-1", "Exit")])
        self.assertEqual(late["rejected"], 1)
        requirement = self.client.get("/requirements").json()[0]
        self.assertEqual(requirement["status"], "completed")
        self.assertEqual(requirement["requirement_id"], decided["requirement_id"])

    def test_report_requires_subscription_and_known_refs(self):
        result = self.sync(
            "B-1",
            [self.report_event("EV-1", 1, 1, "PKG-1", "Exit", sign_ref="SIGN-GHOST")],
        )
        self.assertEqual(result["rejected"], 1)

        resp = self.client.post("/units", json={"unit_ref": "UNIT-NOSUB", "name": "未订阅单位"})
        self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            "/signs",
            json={
                "sign_ref": "SIGN-002",
                "unit_ref": "UNIT-NOSUB",
                "location_code": "FZ-002",
                "place_category": "road",
                "carrier_type": "道路标牌",
            },
        )
        self.assertEqual(resp.status_code, 201)
        result = self.sync(
            "B-2",
            [self.report_event("EV-2", 1, 1, "PKG-1", "Exit", sign_ref="SIGN-002")],
        )
        self.assertEqual(result["rejected"], 1)
        self.assertIn("未订阅", result["results"][0]["reason"])

    def test_report_rejects_naive_time(self):
        event = self.report_event("EV-1", 1, 1, "PKG-1", "Exit")
        event["reported_at"] = "2026-09-20 10:00:00"
        result = self.sync("B-1", [event])
        self.assertEqual(result["rejected"], 1)


class ExemptionTest(ApiTestBase):
    def setUp(self):
        super().setUp()
        self.make_unit()
        self.make_sign()
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        self.sync("B-1", [self.report_event("EV-1", 1, 1, "PKG-1", "Exit")])
        self.publish("PKG-2", 2, [entry("E-EXIT", "Way Out")], supersedes="PKG-1")
        candidate = self.client.get("/candidates").json()[0]
        decided = self.client.post(
            f"/candidates/{candidate['candidate_id']}/decision",
            json={"action": "confirm", "decided_by": "EXPERT-01"},
        ).json()
        self.requirement_id = decided["requirement_id"]

    def apply(self, exemption_id="EXM-1", valid_from=T_NOW_FROM, valid_until=T_NOW_UNTIL):
        resp = self.client.post(
            "/exemptions",
            json={
                "exemption_id": exemption_id,
                "sign_ref": "SIGN-001",
                "requirement_id": self.requirement_id,
                "reason": "历史建筑外立面保护，暂缓更换标牌",
                "valid_from": valid_from,
                "valid_until": valid_until,
                "decided_by": "UNIT-TRAFFIC",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def test_approve_revoke_appends_and_never_overwrites(self):
        applied = self.apply()
        self.assertEqual(applied["status"], "applied")

        approved = self.client.post(
            "/exemptions/EXM-1/approve", json={"decided_by": "FAO-01"}
        ).json()
        self.assertEqual(approved["status"], "active")

        revoked = self.client.post(
            "/exemptions/EXM-1/revoke", json={"decided_by": "FAO-02", "note": "施工提前完成"}
        ).json()
        self.assertEqual(revoked["status"], "revoked")

        actions = [event["action"] for event in revoked["history"]]
        self.assertEqual(actions, ["applied", "approved", "revoked"])
        original = revoked["history"][0]
        self.assertEqual(original["valid_until"], T_NOW_UNTIL.replace("+08:00", "+08:00"))
        self.assertEqual(original["reason"], "历史建筑外立面保护，暂缓更换标牌")

        # 已撤销的豁免不能再批准或再撤销
        resp = self.client.post("/exemptions/EXM-1/approve", json={"decided_by": "FAO-01"})
        self.assertEqual(resp.status_code, 409)
        resp = self.client.post("/exemptions/EXM-1/revoke", json={"decided_by": "FAO-01"})
        self.assertEqual(resp.status_code, 409)

    def test_expiry_is_append_only(self):
        self.apply(valid_from="2026-01-01T00:00:00+08:00", valid_until="2026-06-30T23:59:59+08:00")
        self.client.post("/exemptions/EXM-1/approve", json={"decided_by": "FAO-01"})
        state = self.client.get("/exemptions/EXM-1").json()
        self.assertEqual(state["status"], "expired")  # 派生到期

        expired = self.client.post("/exemptions/EXM-1/expire", json={}).json()
        self.assertEqual(expired["status"], "expired")
        actions = [event["action"] for event in expired["history"]]
        self.assertEqual(actions, ["applied", "approved", "expired"])
        # 到期事件追加后，原批准决定仍然保留
        approved_event = expired["history"][1]
        self.assertEqual(approved_event["decided_by"], "FAO-01")

    def test_active_exemption_counts_in_analytics(self):
        self.apply()
        self.client.post("/exemptions/EXM-1/approve", json={"decided_by": "FAO-01"})
        analytics = self.client.get("/packages/PKG-2/adoption").json()
        summary = analytics["summary"]
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["exempted_open"], 1)
        self.assertEqual(summary["open"], 0)
        self.assertIsNone(summary["adjusted_completion_ratio"])
        requirement = analytics["requirements"][0]
        self.assertTrue(requirement["exempted"])
        self.assertFalse(requirement["overdue"])

    def test_overdue_requirement_flagged(self):
        self.client.post(
            f"/requirements/{self.requirement_id}/commitment",
            json={"committed_deadline": "2026-09-01T00:00:00+08:00"},
        )
        analytics = self.client.get("/packages/PKG-2/adoption").json()
        requirement = analytics["requirements"][0]
        self.assertTrue(requirement["overdue"])
        self.assertEqual(analytics["summary"]["overdue_open"], 1)


class PublicAndTraceTest(ApiTestBase):
    def test_transitioning_public_view_and_reverse_lookup(self):
        self.make_unit()
        self.make_sign()
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")])
        self.sync("B-1", [self.report_event("EV-1", 1, 1, "PKG-1", "Exit")])
        self.publish("PKG-2", 2, [entry("E-EXIT", "Way Out")], supersedes="PKG-1")

        # 分批换版期间：现场仍是旧译法，公开查询显式标注差异
        public = self.client.get("/public/signs/SIGN-001").json()
        language = public["languages"][0]
        self.assertEqual(language["on_site_translation"], "Exit")
        self.assertEqual(language["recommended_translation"], "Way Out")
        self.assertEqual(language["recommended_package_id"], "PKG-2")
        self.assertFalse(language["matches_recommendation"])
        self.assertTrue(language["transitioning"])

        # 从任一标识版本反查当时合法引用的发布包
        lookup = self.client.get("/signs/SIGN-001/versions/1/package").json()
        reference = lookup["references"][0]
        self.assertEqual(reference["package"]["package_id"], "PKG-1")
        self.assertEqual(reference["package_status_then"], "effective")
        self.assertTrue(reference["package"]["signature_valid"])

        # 换版后新版本的反查
        self.sync("B-2", [self.report_event("EV-2", 2, 2, "PKG-2", "Way Out")])
        lookup = self.client.get("/signs/SIGN-001/versions/2/package").json()
        self.assertEqual(lookup["references"][0]["package"]["package_id"], "PKG-2")

        # 轨迹包含两个版本且合法性各自判定
        trace = self.client.get("/signs/SIGN-001/trace").json()
        self.assertEqual(len(trace["versions"]), 2)
        self.assertTrue(all(v["reference_legitimate_then"] for v in trace["versions"]))

    def test_deprecated_package_reference_marked_illegitimate(self):
        self.make_unit()
        self.make_sign()
        self.publish("PKG-1", 1, [entry("E-EXIT", "Exit")], effective_from=T_PAST)
        self.client.post(
            "/packages/PKG-1/deprecate", json={"effective_at": "2026-09-19T00:00:00+08:00"}
        )
        # 废止之后上报的引用被标记为当时不合法
        self.sync("B-1", [self.report_event("EV-1", 1, 1, "PKG-1", "Exit")])
        trace = self.client.get("/signs/SIGN-001/trace").json()
        self.assertEqual(trace["versions"][0]["package_status_then"], "deprecated")
        self.assertFalse(trace["versions"][0]["reference_legitimate_then"])


if __name__ == "__main__":
    unittest.main()
