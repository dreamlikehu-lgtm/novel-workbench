"""Synthetic runtime regressions; these fixtures are not AI literary reviews."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "novel.py"
SPEC = importlib.util.spec_from_file_location("novel", SCRIPT)
novel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(novel)
BODY = "林舟把钥匙放进口袋。他没有打开蓝信封。门外的脚步声停了下来。"


class TextTests(unittest.TestCase):
    def test_metadata_is_not_counted(self):
        text = "# 第1章 标题\n\n## 规划\n" + "注" * 200 + "\n## 正文\n甲乙丙丁\n## 备注\n" + "注" * 200
        self.assertEqual(novel.extract(text), "甲乙丙丁")
        self.assertEqual(novel.counts(novel.extract(text))["han"], 4)

    def test_crlf_and_explicit_body_mode(self):
        self.assertEqual(novel.extract("# 标题\r\n## 正文\r\n甲乙\r\n## 注释\r\n丙"), "甲乙")
        self.assertEqual(novel.extract("甲乙", "body"), "甲乙")

    def test_missing_or_duplicate_body_rejected(self):
        for text in ("没有分区", "## 正文\n甲\n## 正文\n乙", "## 正文\n\n"):
            with self.assertRaises(novel.GateError):
                novel.extract(text)

    def test_counts_are_named_and_distinct(self):
        self.assertEqual(novel.counts("甲乙ABC12，。 \n"), {"han": 2, "visible": 9})
        self.assertEqual(novel.counts("𠀀")["han"], 1)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="novel-test-")
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.book = self.work / "作品"
        self.cli("init", self.book, "--title", "雨停之前", "--planned-chapters", "2", "--minimum", "4", "--maximum", "60")
        self.candidate = None

    def cli(self, *args, ok=True):
        proc = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, encoding="utf-8")
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        return json.loads(proc.stderr)

    def status(self, book=None):
        return self.cli("status", book or self.book)

    def stage(self, number=1, body=BODY, summary="林舟带走钥匙，仍未开信。", delta=None, book=None, title="雨停之前"):
        book = book or self.book
        body_file, summary_file, delta_file = [self.work / name for name in ("candidate.md", "summary.txt", "delta.json")]
        body_file.write_text("# 标题\n\n## 正文\n" + body + "\n\n## 备注\n此处不计字数。", encoding="utf-8")
        summary_file.write_text(summary, encoding="utf-8")
        if delta is None:
            delta = [{"key": "character:林舟:钥匙", "value": "林舟持有", "evidence": "林舟把钥匙放进口袋"}]
        delta_file.write_text(json.dumps(delta, ensure_ascii=False), encoding="utf-8")
        context = self.cli("context", book)
        self.candidate = self.cli("stage", book, "--chapter", number, "--title", title,
            "--file", body_file, "--summary", summary_file, "--delta", delta_file,
            "--expected-head", context["head"], "--expected-source", context["source_sha"])
        return self.candidate

    def review(self, scope, verdict="pass", findings=None, book=None, **overrides):
        data = self.candidate
        report = {k: data[k] for k in ("body_sha", "base_head", "source_sha", "proposal_sha")}
        report.update({"reviewer": "synthetic-" + scope, "scope": scope,
            "verdict": verdict, "findings": findings or [], "notes": "Synthetic regression fixture, not a literary review."})
        report.update(overrides)
        file = self.work / (scope + ".json")
        file.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        return self.cli("review", book or self.book, "--chapter", data["chapter"], "--file", file)

    def approve(self, book=None):
        self.review("continuity", book=book)
        self.review("craft", book=book)

    def commit(self, number=1, book=None, ok=True, expected=None):
        book = book or self.book
        return self.cli("commit", book, "--chapter", number, "--expected-head", expected or self.status(book)["head"], ok=ok)

    def test_full_two_chapter_lifecycle(self):
        self.stage()
        self.approve()
        first = self.commit()
        ctx = self.cli("context", self.book, "--focus", "林舟")
        self.assertEqual(ctx["head"], first["head"])
        self.assertEqual(ctx["facts"]["character:林舟:钥匙"]["value"], "林舟持有")
        self.assertEqual(ctx["recent_prose"][0]["body"], BODY)
        self.stage(2, "林舟把钥匙交还给了门卫。门卫接过后，问起那个蓝信封。", delta=[{
            "key": "character:林舟:钥匙", "value": "门卫持有", "evidence": "林舟把钥匙交还给了门卫"}])
        self.approve()
        self.commit(2)
        done = self.status()
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["facts"]["character:林舟:钥匙"]["chapter"], 2)

    def test_no_reviews_and_one_review_do_not_pass(self):
        self.stage()
        self.commit(ok=False)
        self.review("continuity")
        self.commit(ok=False)
        self.assertEqual(self.status()["committed_chapters"], 0)

    def test_failed_review_is_never_completed(self):
        self.stage()
        self.review("continuity", verdict="fail", findings=[{"severity": "blocking", "message": "synthetic contradiction"}])
        self.review("craft")
        for _ in range(4):
            self.commit(ok=False)
        self.assertEqual(self.status()["status"], "in_progress")
        self.assertEqual(len(self.status()["drafts"]), 1)

    def test_changed_body_invalidates_reviews(self):
        self.stage()
        self.approve()
        self.stage(body=BODY + "他后退一步。")
        self.commit(ok=False)

    def test_candidate_is_frozen_even_if_source_file_changes(self):
        staged = self.stage()
        (self.work / "candidate.md").write_text("文件已经改写", encoding="utf-8")
        frozen = self.cli("candidate", self.book, "--chapter", 1)
        self.assertEqual(frozen["body"], BODY)
        self.assertEqual(frozen["body_sha"], staged["body_sha"])

    def test_wrong_review_binding_and_blocking_pass_are_rejected(self):
        self.stage()
        report = {k: self.candidate[k] for k in ("body_sha", "base_head", "source_sha", "proposal_sha")}
        report.update({"reviewer": "fixture", "scope": "continuity", "verdict": "pass", "findings": [], "notes": "fixture"})
        file = self.work / "invalid-review.json"
        for key in ("body_sha", "base_head", "source_sha", "proposal_sha"):
            invalid = dict(report, **{key: "incorrect"})
            file.write_text(json.dumps(invalid), encoding="utf-8")
            self.cli("review", self.book, "--chapter", 1, "--file", file, ok=False)
        report["findings"] = [{"severity": "blocking", "message": "inconsistent fixture"}]
        file.write_text(json.dumps(report), encoding="utf-8")
        self.cli("review", self.book, "--chapter", 1, "--file", file, ok=False)

    def test_invalid_fact_keys_and_threads_are_rejected(self):
        duplicate = {"key": "same", "value": 1, "evidence": "林舟把钥匙放进口袋"}
        cases = [[duplicate, duplicate], [{"key": "thread:信封", "value": {"status": "unknown"}, "evidence": "他没有打开蓝信封"}]]
        for delta in cases:
            self.stage(delta=delta)
            self.approve()
            self.commit(ok=False)
        self.assertEqual(self.status()["committed_chapters"], 0)

    def test_changed_summary_or_facts_invalidates_reviews(self):
        old = self.stage()
        self.approve()
        new = self.stage(summary="另一份摘要。")
        self.assertEqual(old["body_sha"], new["body_sha"])
        self.assertNotEqual(old["proposal_sha"], new["proposal_sha"])
        self.commit(ok=False)

    def test_changed_source_invalidates_reviews(self):
        self.stage()
        self.approve()
        (self.book / "style.md").write_text("改用不同叙述距离。", encoding="utf-8")
        self.commit(ok=False)
        self.assertTrue(self.status()["drafts"][0]["stale"])

    def test_custom_minimum_and_maximum_are_enforced(self):
        for body in ("甲乙", "甲" * 61):
            info = self.stage(body=body, delta=[])
            self.assertFalse(info["length_pass"])
            self.approve()
            self.commit(ok=False)

    def test_wrong_quote_rolls_back_entire_commit(self):
        self.stage(delta=[{"key": "key", "value": 1, "evidence": "这句话没有出现在正文"}])
        self.approve()
        self.commit(ok=False)
        self.assertEqual(self.status()["facts"], {})
        self.assertFalse((self.book / "chapters" / "0001.md").exists())

    def test_no_missing_or_out_of_order_chapter(self):
        self.stage()
        self.approve()
        self.commit(2, ok=False)
        self.commit()
        self.cli("context", self.book, "--chapter", "3", ok=False)
        self.assertEqual(self.status()["committed_chapters"], 1)

    def test_stale_head_rejected(self):
        self.stage()
        self.approve()
        self.commit(expected="not-the-current-head", ok=False)

    def test_source_changed_during_writing_cannot_be_relabelled_at_stage(self):
        old = self.cli("context", self.book)
        self.stage()
        (self.book / "bible.md").write_text("新设定内容", encoding="utf-8")
        self.cli("stage", self.book, "--chapter", 1, "--title", "候选",
            "--file", self.work / "candidate.md", "--summary", self.work / "summary.txt",
            "--delta", self.work / "delta.json", "--expected-head", old["head"],
            "--expected-source", old["source_sha"], ok=False)

    def test_manual_export_edit_is_detected_and_preserved(self):
        self.stage()
        self.approve()
        self.commit()
        path = self.book / "chapters" / "0001.md"
        path.write_text("作者的手工修改", encoding="utf-8")
        self.cli("context", self.book, ok=False)
        self.cli("export", self.book, ok=False)
        self.assertEqual(path.read_text(encoding="utf-8"), "作者的手工修改")
        self.cli("export", self.book, "--restore")
        self.assertEqual(len(list(path.parent.glob("*.backup-*"))), 1)
        self.assertIn(BODY, path.read_text(encoding="utf-8"))

    def test_missing_export_can_be_recovered_without_recommit(self):
        self.stage()
        self.approve()
        token = self.commit()["head"]
        (self.book / "chapters" / "0001.md").unlink()
        self.assertEqual(self.status()["issues"][0]["type"], "missing_export")
        self.cli("export", self.book)
        self.assertEqual(self.status()["head"], token)
        self.assertEqual(self.status()["issues"], [])

    def test_fork_keeps_original_and_discards_later_state(self):
        self.stage()
        self.approve()
        self.commit()
        self.stage(2, BODY + "他停了一会。", delta=[{"key": "later", "value": True, "evidence": "他停了一会"}])
        self.approve()
        self.commit(2)
        branch = self.work / "重写分支"
        self.cli("fork", self.book, "--from-chapter", 2, "--to", branch)
        self.assertEqual(self.status()["committed_chapters"], 2)
        self.assertEqual(self.status(branch)["committed_chapters"], 1)
        self.assertNotIn("later", self.status(branch)["facts"])
        self.stage(2, BODY + "他没有再回头。", book=branch)
        self.approve(book=branch)
        self.commit(2, book=branch)

    def test_due_threads_are_retained_when_focus_excludes_them(self):
        self.stage(delta=[{"key": "thread:信封", "value": {"status": "open", "due_chapter": 2}, "evidence": "他没有打开蓝信封"}])
        self.approve()
        self.commit()
        ctx = self.cli("context", self.book, "--focus", "不相关词")
        self.assertIn("thread:信封", ctx["facts"])
        self.assertIn("thread:信封", ctx["due_threads"])

    def test_state_tamper_blocks_continuation(self):
        self.stage()
        self.approve()
        self.commit()
        with sqlite3.connect(self.book / ".novel" / "ledger.sqlite3") as db:
            db.execute("UPDATE commits SET state='{}'")
        self.assertIn("state_mismatch", [i["type"] for i in self.status()["issues"]])
        self.cli("context", self.book, ok=False)

    def test_concurrent_commit_has_exactly_one_winner(self):
        self.stage()
        self.approve()
        command = [sys.executable, str(SCRIPT), "commit", str(self.book), "--chapter", "1", "--expected-head", "genesis"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            runs = list(pool.map(lambda _: subprocess.run(command, capture_output=True), range(2)))
        self.assertEqual(sorted(r.returncode for r in runs), [0, 2])
        self.assertEqual(self.status()["committed_chapters"], 1)
        self.assertEqual(self.status()["issues"], [])

    def test_dashboard_escapes_story_and_title(self):
        self.stage(body=BODY + "<script>alert(1)</script>", title='<img src=x onerror="alert(1)">')
        self.approve()
        self.commit()
        out = self.cli("dashboard", self.book)
        page = Path(out["dashboard"]).read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn('<img src=x onerror=', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertEqual(out["network_requests"], 0)

    def test_existing_project_is_not_overwritten(self):
        original = self.status()["title"]
        self.cli("init", self.book, "--title", "不应覆盖", ok=False)
        self.assertEqual(self.status()["title"], original)


if __name__ == "__main__":
    unittest.main()
