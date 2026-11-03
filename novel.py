#!/usr/bin/env python3
"""Local novel ledger: SQLite transactions, version-bound reviews, offline views.

Python standard library only. No model calls or network access.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone

VERSION = "0.1.0"
DOCS = ("bible.md", "outline.md", "style.md")
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE drafts (chapter INTEGER PRIMARY KEY, title TEXT NOT NULL,
 body TEXT NOT NULL, sha TEXT NOT NULL, base_head TEXT NOT NULL, source_sha TEXT NOT NULL,
 summary TEXT NOT NULL, delta TEXT NOT NULL, proposal_sha TEXT NOT NULL);
CREATE TABLE reviews (chapter INTEGER NOT NULL, reviewer TEXT NOT NULL,
 scope TEXT NOT NULL, body_sha TEXT NOT NULL, base_head TEXT NOT NULL,
 source_sha TEXT NOT NULL, proposal_sha TEXT NOT NULL, report TEXT NOT NULL,
 PRIMARY KEY(chapter, reviewer, scope));
CREATE TABLE commits (chapter INTEGER PRIMARY KEY, title TEXT NOT NULL,
 body TEXT NOT NULL, sha TEXT NOT NULL, summary TEXT NOT NULL, delta TEXT NOT NULL,
 state TEXT NOT NULL, parent_head TEXT NOT NULL, head TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class GateError(Exception):
    pass


def require(ok, message):
    if not ok:
        raise GateError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def counts(text):
    han = sum(0x3400 <= ord(c) <= 0x4DBF or 0x4E00 <= ord(c) <= 0x9FFF
              or 0xF900 <= ord(c) <= 0xFAFF or 0x20000 <= ord(c) <= 0x323AF for c in text)
    return {"han": han, "visible": sum(not c.isspace() for c in text)}


def extract(text, fmt="chapter"):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if fmt == "body":
        body = text.strip()
    else:
        lines = text.splitlines()
        marks = [i for i, line in enumerate(lines) if line.strip() == "## 正文"]
        require(len(marks) == 1, "必须且只能有一个 ‘## 正文’；纯正文请使用 --format body")
        start = marks[0] + 1
        end = next((i for i in range(start, len(lines)) if re.match(r"^#{1,2}\s", lines[i])), len(lines))
        body = "\n".join(lines[start:end]).strip()
    require(bool(body), "正文为空")
    return body


def connect(root):
    root = Path(root).resolve()
    db = root / ".novel" / "ledger.sqlite3"
    require(db.is_file() and not db.is_symlink(), "没有账本；请先 init 新项目")
    con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    require(row is not None and row[0] == "1", "不支持的账本版本")
    return root, con


def config(con):
    return json.loads(con.execute("SELECT value FROM meta WHERE key='config'").fetchone()[0])


def latest(con):
    return con.execute("SELECT * FROM commits ORDER BY chapter DESC LIMIT 1").fetchone()


def head(con):
    row = latest(con)
    return row["head"] if row else "genesis"


def state(con):
    row = latest(con)
    return json.loads(row["state"]) if row else {}


def sources(root, con):
    return digest(encoded({"config": config(con), "docs": {
        name: (root / name).read_text(encoding="utf-8") for name in DOCS}}))


def chapter_path(root, number):
    return root / "chapters" / f"{number:04d}.md"


def rendered(row):
    return f"# 第{row['chapter']}章 {row['title']}\n\n## 正文\n\n{row['body']}\n"


def apply_delta(previous, delta, chapter):
    result = dict(previous)
    for change in delta:
        result[change["key"]] = {"value": change["value"], "evidence": change["evidence"], "chapter": chapter}
    return result


def payload(number, title, sha, summary, delta, parent):
    return {"chapter": number, "title": title, "body_sha": sha, "summary": summary,
            "delta": delta, "parent_head": parent}


def issues(root, con):
    result, parent, number, current = [], "genesis", 1, {}
    for row in con.execute("SELECT * FROM commits ORDER BY chapter"):
        n = row["chapter"]
        if n != number:
            result.append({"chapter": n, "type": "chapter_gap"})
        if row["parent_head"] != parent or digest(row["body"]) != row["sha"]:
            result.append({"chapter": n, "type": "broken_chain"})
        delta = json.loads(row["delta"])
        if digest(encoded(payload(n, row["title"], row["sha"], row["summary"], delta, parent))) != row["head"]:
            result.append({"chapter": n, "type": "commit_hash_mismatch"})
        current = apply_delta(current, delta, n)
        if current != json.loads(row["state"]):
            result.append({"chapter": n, "type": "state_mismatch"})
        path = chapter_path(root, n)
        if path.is_symlink():
            result.append({"chapter": n, "type": "export_symlink"})
        elif not path.is_file():
            result.append({"chapter": n, "type": "missing_export"})
        elif path.read_text(encoding="utf-8") != rendered(row):
            result.append({"chapter": n, "type": "edited_export"})
        parent, number = row["head"], n + 1
    return result


def healthy(root, con):
    found = issues(root, con)
    require(not found, "账本或导出视图需要处理：" + encoded(found))


def init_project(root, cfg):
    root = Path(root).resolve()
    require(not root.exists(), "目录已存在；请使用新项目目录")
    require(cfg["minimum"] > 0, "最小字数必须为正整数")
    require(cfg["maximum"] is None or cfg["maximum"] >= cfg["minimum"], "最大字数不得小于最小字数")
    require(cfg["planned_chapters"] is None or cfg["planned_chapters"] > 0, "计划章数必须为正整数")
    root.mkdir(parents=True)
    for directory in (".novel", "chapters", "drafts", "reports"):
        (root / directory).mkdir()
    con = sqlite3.connect(root / ".novel" / "ledger.sqlite3")
    try:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO meta VALUES('schema','1')")
        con.execute("INSERT INTO meta VALUES('config',?)", (encoded(cfg),))
        con.commit()
    finally:
        con.close()
    defaults = {
        "bible.md": "# 故事设定\n\n记录人物、世界规则、叙事视角与信息边界。未来计划不能登记为已发生事实。\n",
        "outline.md": "# 故事规划\n\n记录当前卷、章节目标、承接条件和留给后续的变化。\n",
        "style.md": "# 文风约定\n\n记录用户偏好、原创或用户授权的范例，以及希望避免的表达。\n",
    }
    for name, text in defaults.items():
        (root / name).write_text(text, encoding="utf-8")
    return {"project": str(root), "head": "genesis", "config": cfg}


def get_draft(con, number):
    row = con.execute("SELECT * FROM drafts WHERE chapter=?", (number,)).fetchone()
    require(row is not None, "本章尚未 stage")
    return row


def fresh(root, con, row):
    require(row["base_head"] == head(con), "草稿基于旧 head；重新取上下文并复核")
    require(row["source_sha"] == sources(root, con), "设定、大纲或文风已变化；重新 stage 并审稿")


def candidate(root, con, args):
    healthy(root, con)
    row = get_draft(con, args.chapter)
    fresh(root, con, row)
    return {"chapter": row["chapter"], "title": row["title"], "body": row["body"],
            "body_sha": row["sha"], "base_head": row["base_head"], "source_sha": row["source_sha"],
            "proposal_sha": row["proposal_sha"], "summary": row["summary"], "delta": json.loads(row["delta"])}


def stage(root, con, args):
    body = extract(Path(args.file).read_text(encoding="utf-8"), args.format)
    summary = Path(args.summary).read_text(encoding="utf-8").strip()
    delta = load(args.delta)
    require(bool(summary), "摘要不能为空")
    require(isinstance(delta, list), "事实增量必须为数组；没有新事实可使用 []")
    con.execute("BEGIN IMMEDIATE")
    try:
        healthy(root, con)
        require(args.expected_head == head(con), "head 已变化；重新取上下文并复核")
        require(args.expected_source == sources(root, con), "写作期间设定、大纲或文风已变化；重新取上下文并复核")
        last, cfg = latest(con), config(con)
        require(args.chapter == (last["chapter"] + 1 if last else 1), "只能暂存下一章；历史改稿请先 fork")
        require(cfg["planned_chapters"] is None or args.chapter <= cfg["planned_chapters"], "已达到约定章数")
        sha, src = digest(body), sources(root, con)
        proposal_sha = digest(encoded({"title": args.title, "summary": summary, "delta": delta}))
        con.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,?,?,?,?,?,?,?)", (
            args.chapter, args.title, body, sha, head(con), src, summary, encoded(delta), proposal_sha))
        con.commit()
        measured = counts(body)
        value = measured[cfg["metric"]]
        return {"chapter": args.chapter, "body_sha": sha, "base_head": args.expected_head,
                "source_sha": src, "proposal_sha": proposal_sha, "summary": summary, "delta": delta,
                "counts": measured, "length_pass": value >= cfg["minimum"]
                and (cfg["maximum"] is None or value <= cfg["maximum"])}
    except Exception:
        con.rollback()
        raise


def record_review(root, con, args):
    report = load(args.file)
    con.execute("BEGIN IMMEDIATE")
    try:
        healthy(root, con)
        row = get_draft(con, args.chapter)
        fresh(root, con, row)
        require(isinstance(report, dict), "审稿报告必须是 JSON 对象")
        for key, expected in (("body_sha", row["sha"]), ("base_head", row["base_head"]),
                              ("source_sha", row["source_sha"]), ("proposal_sha", row["proposal_sha"])):
            require(report.get(key) == expected, "审稿版本不匹配：" + key)
        require(report.get("scope") in ("continuity", "craft"), "scope 必须为 continuity 或 craft")
        require(isinstance(report.get("reviewer"), str) and report["reviewer"].strip(), "缺少 reviewer")
        require(report.get("verdict") in ("pass", "fail"), "verdict 必须为 pass 或 fail")
        require(isinstance(report.get("findings"), list), "findings 必须是数组")
        for finding in report["findings"]:
            require(isinstance(finding, dict) and finding.get("severity") in ("blocking", "advisory")
                    and isinstance(finding.get("message"), str) and finding["message"].strip(), "finding 需要 severity 与 message")
        require(isinstance(report.get("notes"), str) and report["notes"].strip(), "需要 notes 说明实际检查依据")
        require(not (report["verdict"] == "pass" and any(f["severity"] == "blocking" for f in report["findings"])), "有 blocking 问题不能通过")
        con.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?,?)", (
            args.chapter, report["reviewer"], report["scope"], row["sha"], row["base_head"],
            row["source_sha"], row["proposal_sha"], encoded(report)))
        con.commit()
        return {"recorded": True, "chapter": args.chapter, "scope": report["scope"], "verdict": report["verdict"]}
    except Exception:
        con.rollback()
        raise


def commit_chapter(root, con, args):
    con.execute("BEGIN IMMEDIATE")
    try:
        healthy(root, con)
        row = get_draft(con, args.chapter)
        fresh(root, con, row)
        summary, delta = row["summary"], json.loads(row["delta"])
        require(args.expected_head == head(con), "head 已变化")
        last = latest(con)
        require(args.chapter == (last["chapter"] + 1 if last else 1), "只能按章顺序提交")
        cfg = config(con)
        value = counts(row["body"])[cfg["metric"]]
        require(value >= cfg["minimum"], "正文低于约定下限")
        require(cfg["maximum"] is None or value <= cfg["maximum"], "正文超过约定上限")
        reports = [json.loads(r[0]) for r in con.execute(
            "SELECT report FROM reviews WHERE chapter=? AND body_sha=? AND base_head=? AND source_sha=? AND proposal_sha=?",
            (args.chapter, row["sha"], row["base_head"], row["source_sha"], row["proposal_sha"]))]
        require(not any(r["verdict"] == "fail" for r in reports), "当前版本仍有失败审稿")
        require({r["scope"] for r in reports if r["verdict"] == "pass"} == {"continuity", "craft"}, "缺少当前版本的连续性或表达审稿")
        keys = set()
        for change in delta:
            require(isinstance(change, dict) and set(change) == {"key", "value", "evidence"}, "事实需要 key/value/evidence")
            require(isinstance(change["key"], str) and change["key"].strip() and change["key"] not in keys, "事实 key 为空或重复")
            keys.add(change["key"])
            quote = change["evidence"]
            require(isinstance(quote, str) and len(quote.strip()) >= 4 and quote in row["body"], "证据必须是本章正文中至少4字符的原文片段")
            if change["key"].startswith("thread:"):
                val = change["value"]
                require(isinstance(val, dict) and val.get("status") in ("open", "resolved", "dropped"), "伏笔 value 需要合法 status")
                due = val.get("due_chapter")
                require(due is None or type(due) is int and due > 0, "due_chapter 必须为正整数或 null")
        path = chapter_path(root, args.chapter)
        require(not path.exists() and not path.is_symlink(), "章节导出路径已存在；先保留并处理原文件")
        new_state = apply_delta(state(con), delta, args.chapter)
        new_head = digest(encoded(payload(args.chapter, row["title"], row["sha"], summary, delta, head(con))))
        con.execute("INSERT INTO commits VALUES(?,?,?,?,?,?,?,?,?,?)", (
            args.chapter, row["title"], row["body"], row["sha"], summary, encoded(delta), encoded(new_state),
            head(con), new_head, datetime.now(timezone.utc).isoformat()))
        con.execute("DELETE FROM drafts WHERE chapter=?", (args.chapter,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    # DB is canonical. A crash here leaves a detectable, regenerable export.
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(rendered(row))
    except OSError as exc:
        return {"committed": True, "chapter": args.chapter, "head": new_head,
                "export_pending": str(exc), "action": "运行 export 重建缺失视图，不要重复提交"}
    return {"committed": True, "chapter": args.chapter, "head": new_head, "counts": counts(row["body"])}


def due_threads(facts, number):
    return {k: v for k, v in facts.items() if k.startswith("thread:") and isinstance(v["value"], dict)
            and v["value"].get("status") == "open" and type(v["value"].get("due_chapter")) is int
            and v["value"]["due_chapter"] <= number}


def context(root, con, args):
    healthy(root, con)
    last = latest(con)
    number = last["chapter"] + 1 if last else 1
    require(args.chapter is None or args.chapter == number, "上下文只用于下一章")
    facts = state(con)
    focus = [s.strip() for s in (args.focus or "").split(",") if s.strip()]
    selected = {k: v for k, v in facts.items() if not focus or any(t in k or t in encoded(v) for t in focus)}
    selected.update(due_threads(facts, number))
    summaries = [{"chapter": r["chapter"], "summary": r["summary"]} for r in con.execute(
        "SELECT chapter, summary FROM commits ORDER BY chapter DESC LIMIT 20")][::-1]
    recent = [{"chapter": r["chapter"], "body": r["body"]} for r in con.execute(
        "SELECT chapter, body FROM commits ORDER BY chapter DESC LIMIT 3")][::-1]
    retrieved = []
    if focus:
        for row in con.execute("SELECT chapter, body FROM commits ORDER BY chapter"):
            score = sum(row["body"].count(t) for t in focus)
            if score:
                pos = min(row["body"].find(t) for t in focus if t in row["body"])
                retrieved.append({"chapter": row["chapter"], "score": score,
                                  "excerpt": row["body"][max(0, pos-120):pos+600]})
        retrieved.sort(key=lambda item: (-item["score"], -item["chapter"]))
    return {"chapter": number, "head": head(con), "source_sha": sources(root, con), "config": config(con),
            "bible": (root / "bible.md").read_text(encoding="utf-8"),
            "outline": (root / "outline.md").read_text(encoding="utf-8"), "facts": selected,
            "omitted_fact_keys": sorted(set(facts)-set(selected)), "due_threads": due_threads(facts, number),
            "recent_summaries": summaries, "retrieved_passages": retrieved[:5],
            "retrieval_method": "literal-keyword; not semantic search", "recent_prose": recent,
            "style": (root / "style.md").read_text(encoding="utf-8")}


def status(root, con):
    cfg = config(con)
    rows = list(con.execute("SELECT chapter,title,body,sha FROM commits ORDER BY chapter"))
    pending = [{"chapter": r["chapter"], "title": r["title"], "body_sha": r["sha"], "counts": counts(r["body"]),
                "stale": r["base_head"] != head(con) or r["source_sha"] != sources(root, con)}
               for r in con.execute("SELECT * FROM drafts ORDER BY chapter")]
    found = issues(root, con)
    complete = cfg["planned_chapters"] is not None and len(rows) == cfg["planned_chapters"] and not pending and not found
    return {"version": VERSION, "title": cfg["title"], "config": cfg,
            "status": "completed" if complete else "needs_attention" if found else "in_progress",
            "head": head(con), "committed_chapters": len(rows), "drafts": pending,
            "chapters": [{"chapter": r["chapter"], "title": r["title"], "body_sha": r["sha"], "counts": counts(r["body"])} for r in rows],
            "facts": state(con), "due_threads": due_threads(state(con), len(rows)+1), "issues": found}


def export(root, con, args):
    bad = [i for i in issues(root, con) if i["type"] not in ("missing_export", "edited_export")]
    require(not bad, "账本校验失败：" + encoded(bad))
    restored = []
    for row in con.execute("SELECT * FROM commits ORDER BY chapter"):
        path, target = chapter_path(root, row["chapter"]), rendered(row)
        require(not path.is_symlink(), "不写入符号链接")
        if path.exists() and path.read_text(encoding="utf-8") != target:
            require(args.restore, "发现手工改稿；先 fork 保留改动，恢复账本版本请显式传 --restore")
            backup = path.with_name(path.name + f".backup-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}")
            backup.write_bytes(path.read_bytes())
        if not path.exists() or path.read_text(encoding="utf-8") != target:
            path.write_text(target, encoding="utf-8")
            restored.append(row["chapter"])
    output = root / "reports" / "manuscript.md"
    require(not output.is_symlink(), "不写入符号链接")
    output.write_text("\n\n".join(rendered(r) for r in con.execute("SELECT * FROM commits ORDER BY chapter")), encoding="utf-8")
    return {"export": str(output), "restored_chapters": restored}


def fork_project(root, con, args):
    last = latest(con)
    require(last is not None and 1 <= args.from_chapter <= last["chapter"], "fork 起点必须是已提交章节")
    bad = [i for i in issues(root, con) if i["type"] not in ("missing_export", "edited_export")]
    require(not bad, "源账本校验失败")
    destination = Path(args.to).resolve()
    result = init_project(destination, config(con))
    target = sqlite3.connect(destination / ".novel" / "ledger.sqlite3")
    try:
        con.backup(target)
        target.execute("DELETE FROM commits WHERE chapter>=?", (args.from_chapter,))
        target.execute("DELETE FROM drafts")
        target.execute("DELETE FROM reviews WHERE chapter>=?", (args.from_chapter,))
        target.commit()
    finally:
        target.close()
    for name in DOCS:
        (destination / name).write_text((root / name).read_text(encoding="utf-8"), encoding="utf-8")
    (destination / "reports" / "fork-origin.json").write_text(encoded({
        "source_project": str(root), "source_head": head(con), "rewrite_from": args.from_chapter,
        "note": "仅保留起点前的已验收章节；设定沿用分支时版本，须核对。原项目不变。"}), encoding="utf-8")
    new_root, new_con = connect(destination)
    try:
        export(new_root, new_con, argparse.Namespace(restore=False))
        result["head"] = head(new_con)
    finally:
        new_con.close()
    result["rewrite_from"] = args.from_chapter
    return result


def dashboard(root, con):
    data, esc = status(root, con), lambda s: html.escape(str(s), quote=True)
    chapters = []
    for row in con.execute("SELECT * FROM commits ORDER BY chapter"):
        chapters.append(f'<details class="chapter"><summary>第{row["chapter"]}章 · {esc(row["title"])} · {counts(row["body"])[data["config"]["metric"]]}字</summary><p>{esc(row["summary"])}</p><pre>{esc(row["body"])}</pre></details>')
    facts = "".join(f'<tr><td>{esc(k)}</td><td>{esc(encoded(v["value"]))}</td><td>{v["chapter"]}</td><td>{esc(v["evidence"])}</td></tr>' for k,v in data["facts"].items())
    notices = "".join(f'<li>{esc(encoded(i))}</li>' for i in data["issues"])
    pending = "".join(f'<li>第{d["chapter"]}章 {esc(d["title"])}：待验收{ "，来源已变化" if d["stale"] else ""}</li>' for d in data["drafts"])
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>小说工作台</title>
<style>body{margin:0;background:#f4f2ed;color:#222;font:16px/1.7 system-ui,sans-serif}main{max-width:1080px;margin:auto;padding:32px}header{border-bottom:1px solid #d2d0c9;margin-bottom:24px}h1{margin-bottom:4px}small{color:#62635d}section,details{background:white;border:1px solid #deded5;border-radius:10px;padding:18px;margin:16px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;font:inherit}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #eee}input{font:inherit;padding:10px;width:min(90%,440px)}.scroll{overflow:auto}</style><main>'''
    page += f'<header><small>中文小说工作台 · 本地只读快照</small><h1>{esc(data["title"])}</h1><p>已验收 {data["committed_chapters"]} / {esc(data["config"]["planned_chapters"] or "未设总章数")} 章 · {esc(data["status"])}</p><small>此页面不调用模型或联网。新章节和改稿后需重新生成面板。</small></header>'
    page += f'<section><h2>待处理</h2><ul>{pending + notices or "<li>当前没有待处理记录</li>"}</ul><p>到期未回收伏笔：{len(data["due_threads"])}</p></section>'
    page += '<h2>已验收章节</h2><input id="search" aria-label="搜索章节" placeholder="搜索标题或正文">' + ("".join(chapters) or '<p>尚未提交章节。</p>')
    page += '<section><h2>故事事实账本</h2><div class="scroll"><table><tr><th>事实</th><th>当前值</th><th>来源章</th><th>原文依据</th></tr>' + facts + '</table></div></section>'
    page += '<small>脚本检查与文学质量判断分别负责不同问题。已验收表示约定流程已完成。</small></main><script>document.getElementById("search").addEventListener("input",event=>{const q=event.target.value;document.querySelectorAll(".chapter").forEach(el=>{el.hidden=!el.textContent.includes(q)})});</script></html>'
    output = root / "reports" / "dashboard.html"
    require(not output.is_symlink(), "不写入符号链接")
    output.write_text(page, encoding="utf-8")
    return {"dashboard": str(output), "network_requests": 0}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("project")
    init.add_argument("--title", required=True)
    init.add_argument("--genre", default="未限定")
    init.add_argument("--planned-chapters", type=int)
    init.add_argument("--minimum", type=int, default=3000)
    init.add_argument("--maximum", type=int)
    init.add_argument("--metric", choices=("han", "visible"), default="han")
    for name in ("context", "stage", "candidate", "review", "commit", "status", "export", "fork", "dashboard"):
        cmd = sub.add_parser(name)
        cmd.add_argument("project")
        if name in ("stage", "candidate", "review", "commit"):
            cmd.add_argument("--chapter", type=int, required=True)
        if name in ("stage", "commit"):
            cmd.add_argument("--expected-head", required=True)
        if name in ("stage", "review"):
            cmd.add_argument("--file", required=True)
        if name == "stage":
            cmd.add_argument("--title", required=True)
            cmd.add_argument("--expected-source", required=True)
            cmd.add_argument("--format", choices=("chapter", "body"), default="chapter")
            cmd.add_argument("--summary", required=True)
            cmd.add_argument("--delta", required=True)
        if name == "context":
            cmd.add_argument("--chapter", type=int)
            cmd.add_argument("--focus")
        if name == "export":
            cmd.add_argument("--restore", action="store_true")
        if name == "fork":
            cmd.add_argument("--from-chapter", type=int, required=True)
            cmd.add_argument("--to", required=True)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args, con = parser().parse_args(argv), None
    try:
        if args.command == "init":
            result = init_project(args.project, {"title": args.title, "genre": args.genre,
                "planned_chapters": args.planned_chapters, "minimum": args.minimum,
                "maximum": args.maximum, "metric": args.metric})
        else:
            root, con = connect(args.project)
            handlers = {"context": lambda: context(root, con, args), "stage": lambda: stage(root, con, args),
                "candidate": lambda: candidate(root, con, args),
                "review": lambda: record_review(root, con, args), "commit": lambda: commit_chapter(root, con, args),
                "status": lambda: status(root, con), "export": lambda: export(root, con, args),
                "fork": lambda: fork_project(root, con, args), "dashboard": lambda: dashboard(root, con)}
            result = handlers[args.command]()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (GateError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if con is not None:
            con.close()


if __name__ == "__main__":
    sys.exit(main())
