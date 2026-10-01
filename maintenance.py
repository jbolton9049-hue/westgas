# -*- coding: utf-8 -*-
"""知识库周期治理：增量扫描、周期报告、待审核草稿和发布。"""

import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
from collections import Counter, defaultdict

import process
from ai_tools import process_by_tool
import knowledge_db


RAW_EXTENSIONS = set(process.SUPPORTED)
INDEX_FILE = os.path.join("00_系统说明", "知识治理索引.json")
UPDATE_LOG = os.path.join("09_周期汇总", "更新日志.md")


def _safe_file_name(value):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", str(value))[:100] or "未命名"


def _atomic_write_text(path, content):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def ensure_dirs():
    root = process.ensure_knowledge_base()
    for folder in [
        "09_周期汇总", "09_周期汇总\\日报", "09_周期汇总\\周报", "09_周期汇总\\月报",
        "10_待审核更新", "11_重复与冲突", "12_失效资料",
    ]:
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    return root


def _index_path():
    return os.path.join(ensure_dirs(), INDEX_FILE)


def _load_index():
    try:
        with open(_index_path(), "r", encoding="utf-8") as f:
            value = json.load(f)
            return value if isinstance(value, dict) else {"files": {}}
    except (FileNotFoundError, json.JSONDecodeError):
        return {"files": {}}


def _save_index(index):
    index["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    target = _index_path()
    temporary = target + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, target)
    try:
        knowledge_db.sync_governance_index(process.get_kb_path(), index)
    except Exception as exc:
        _append_log(f"SQLite索引同步失败：{exc}")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_sha256(path):
    """Hash normalized text for a conservative near-duplicate signal."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".txt", ".md", ".csv"}:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
            text = stream.read()
    except OSError:
        return ""
    normalized = re.sub(r"\s+", "", text).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _snapshot_dir():
    path = os.path.join(ensure_dirs(), "12_失效资料", "版本快照")
    os.makedirs(path, exist_ok=True)
    return path


def _save_version_snapshot(path, digest):
    """Keep a copy of each observed source version for later human diffing."""
    target = os.path.join(_snapshot_dir(), f"{os.path.basename(path)}.{digest[:12]}")
    if not os.path.exists(target):
        shutil.copy2(path, target)
    return target


def version_diff(path, old_hash, new_hash=None):
    """Return a unified text diff between stored and current source versions."""
    new_hash = new_hash or _sha256(path)
    old_candidates = [
        os.path.join(_snapshot_dir(), name)
        for name in os.listdir(_snapshot_dir())
        if name.startswith(os.path.basename(path) + ".") and old_hash[:12] in name
    ]
    if not old_candidates:
        return "未找到旧版本快照。"
    old_path = old_candidates[0]
    try:
        with open(old_path, "r", encoding="utf-8", errors="ignore") as stream:
            old_lines = stream.readlines()
        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
            new_lines = stream.readlines()
    except (OSError, UnicodeDecodeError):
        return "该文件不是可直接比较的文本格式，请人工核对版本。"
    return "".join(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"旧版本 {old_hash[:12]}",
        tofile=f"当前版本 {new_hash[:12]}",
    )) or "文本内容无变化（可能是格式或元数据变化）。"


def _source_files():
    base = os.path.join(ensure_dirs(), "01_原始资料库")
    for current, _, files in os.walk(base):
        for name in sorted(files):
            path = os.path.join(current, name)
            ext = os.path.splitext(name)[1].lower()
            if ext not in RAW_EXTENSIONS or name.endswith("_预处理.md"):
                continue
            yield os.path.abspath(path)


def scan_incremental():
    """扫描原始资料并返回新增、修改、未变化、删除、重复结果。"""
    old = _load_index().get("files", {})
    current = {}
    new, changed, unchanged = [], [], []
    for path in _source_files():
        stat = os.stat(path)
        key = path
        digest = _sha256(path)
        observed_at = datetime.datetime.now().isoformat(timespec="seconds")
        previous = old.get(key)
        item = {
            "path": path,
            "name": os.path.basename(path),
            "source_id": "SRC-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:12],
            "hash": digest,
            "normalized_hash": _normalized_sha256(path),
            "size": stat.st_size,
            "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "file_date": datetime.date.fromtimestamp(stat.st_mtime).isoformat(),
            "imported_at": (previous or {}).get("imported_at", observed_at),
            "last_updated_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "last_seen_at": observed_at,
        }
        if not previous or previous.get("status") == "removed":
            item["status"] = "new"
            item["review_status"] = "pending"
            new.append(item)
        elif previous.get("hash") != digest:
            item["status"] = "changed"
            item["review_status"] = "pending"
            item["previous_hash"] = previous.get("hash")
            item["versions"] = list(previous.get("versions", [])) + [{
                "hash": previous.get("hash"),
                "mtime": previous.get("mtime"),
                "detected_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }]
            changed.append(item)
        else:
            item.update({k: v for k, v in previous.items() if k not in item})
            item["status"] = previous.get("status", "unchanged")
            item["review_status"] = previous.get("review_status", "pending")
            unchanged.append(item)
        current[key] = item
        _save_version_snapshot(path, digest)

    removed = []
    for path, item in old.items():
        if path not in current:
            item = dict(item)
            if item.get("status") != "removed":
                item["status"] = "removed"
                item["removed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                removed.append(item)
            current[path] = item

    by_hash = defaultdict(list)
    by_normalized_hash = defaultdict(list)
    for item in current.values():
        if item.get("status") != "removed":
            by_hash[item.get("hash")].append(item["path"])
            if item.get("normalized_hash"):
                by_normalized_hash[item["normalized_hash"]].append(item["path"])
    duplicates = [paths for paths in by_hash.values() if len(paths) > 1]
    similar_duplicates = [paths for paths in by_normalized_hash.values() if len(paths) > 1 and paths not in duplicates]
    index = {"files": current}
    _save_index(index)
    return {
        "new": new, "changed": changed, "unchanged": unchanged,
        "removed": removed, "duplicates": duplicates, "similar_duplicates": similar_duplicates,
    }


def _analysis(text, tool_key=None):
    try:
        return process_by_tool(text, task="analyze", tool_key=tool_key)
    except Exception as exc:
        result = process_by_tool(text, task="analyze", tool_key="rule")
        result["note"] = f"AI分析失败，已回退规则版：{exc}"
        return result


def _draft_path(item):
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", os.path.splitext(item["name"])[0])[:80]
    return os.path.join(ensure_dirs(), "10_待审核更新", f"{datetime.date.today().isoformat()}_{stem}_{item['hash'][:8]}.md")


def _write_draft(item, prepared, analysis):
    path = _draft_path(item)
    tag = prepared.get("tag", {})
    roots = analysis.get("root_cause") or []
    measures = analysis.get("measures") or []
    if isinstance(roots, str):
        roots = [roots]
    if isinstance(measures, str):
        measures = [measures]
    content = (
        "---\n"
        "review_status: pending\n"
        f"source_path: {json.dumps(item['path'], ensure_ascii=False)}\n"
        f"content_hash: {item['hash']}\n"
        f"domain: {tag.get('domain', '待定')}\n"
        f"attr: {tag.get('attr', '待定')}\n"
        f"generated_at: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
        "---\n\n"
        f"# 待审核更新：{item['name']}\n\n"
        f"- **资料状态**：{item['status']}\n- **分类**：{tag.get('domain')} / {tag.get('attr')}\n"
        f"- **来源**：{item['path']}\n- **审核动作**：将 `review_status: pending` 改为 `approved` 后，可在系统设置中发布。\n\n"
        "## 问题定性\n\n"
        f"{analysis.get('issue', '待复核')}\n\n"
        "## 管理内涵草稿\n\n"
        f"{analysis.get('one_sentence', analysis.get('content', '待复核'))}\n\n"
        "## 后果归纳\n\n"
        f"- 直接后果：{analysis.get('direct_consequence', '待复核')}\n"
        f"- 扩散后果：{analysis.get('spread_consequence', '待复核')}\n"
        f"- 严重度：{analysis.get('severity', '待复核')}\n\n"
        "## 根因\n\n" + "\n".join(f"- {value}" for value in roots) + "\n\n"
        "## 建议措施\n\n" + "\n".join(f"{i}. {value}" for i, value in enumerate(measures, 1)) + "\n\n"
        "<!-- ANALYSIS_JSON\n" + json.dumps(analysis, ensure_ascii=False) + "\nANALYSIS_JSON -->\n"
    )
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)
    return path


def _audit_path():
    return os.path.join(ensure_dirs(), "09_周期汇总", "任务运行日志.jsonl")


def _append_audit(kind, status, started_at, finished_at, details=None):
    item = {
        "kind": kind, "status": status, "started_at": started_at,
        "finished_at": finished_at, "details": details or {},
    }
    with open(_audit_path(), "a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def _append_log(message):
    path = os.path.join(ensure_dirs(), UPDATE_LOG)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 知识库更新日志\n\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.datetime.now().isoformat(timespec='seconds')}：{message}\n")


def run_daily(tool_key=None):
    """执行增量扫描并生成待审核更新草稿。"""
    report = scan_incremental()
    targets = report["new"] + report["changed"]
    targets.extend(item for item in report["unchanged"] if item.get("status") == "failed")
    drafts = []
    index = _load_index()
    selected_tool = tool_key or "rule"
    for item in targets:
        try:
            prepared = process.prepare_file(item["path"], selected_tool)
            if not prepared.get("ok"):
                item["status"] = "failed"
                item["error"] = prepared.get("msg")
                index["files"][item["path"]] = item
                continue
            analysis = _analysis(prepared["text"], selected_tool)
            draft = _write_draft(item, prepared, analysis)
            item["status"] = "draft_pending"
            item["review_status"] = "pending"
            item["draft_file"] = draft
            item["last_summarized_at"] = datetime.datetime.now().isoformat(timespec="seconds")
            index["files"][item["path"]] = item
            drafts.append(draft)
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            index["files"][item["path"]] = item
    _save_index(index)
    if report["duplicates"] or report.get("similar_duplicates"):
        dup_path = os.path.join(ensure_dirs(), "11_重复与冲突", f"重复资料_{datetime.date.today().isoformat()}.md")
        with open(dup_path, "w", encoding="utf-8") as f:
            f.write("# 重复资料清单\n\n")
            for paths in report["duplicates"]:
                f.write("## 同内容文件\n\n" + "\n".join(f"- {path}" for path in paths) + "\n\n")
            for paths in report.get("similar_duplicates", []):
                f.write("## 规范化文本相同（需人工确认）\n\n" + "\n".join(f"- {path}" for path in paths) + "\n\n")
    if report["removed"]:
        removed_path = os.path.join(ensure_dirs(), "12_失效资料", f"失效资料_{datetime.date.today().isoformat()}.md")
        with open(removed_path, "w", encoding="utf-8") as f:
            f.write("# 失效资料清单\n\n" + "\n".join(f"- {item['path']}" for item in report["removed"]) + "\n")
    if report["changed"]:
        conflict_path = os.path.join(ensure_dirs(), "11_重复与冲突", f"版本变更_{datetime.date.today().isoformat()}.md")
        with open(conflict_path, "w", encoding="utf-8") as f:
            f.write("# 来源资料版本变更（需人工复核）\n\n")
            for item in report["changed"]:
                diff_file = os.path.join(
                    ensure_dirs(), "11_重复与冲突",
                    f"差异_{_safe_file_name(item['name'])}_{item['hash'][:8]}.diff",
                )
                _atomic_write_text(diff_file, version_diff(item["path"], item.get("previous_hash", ""), item["hash"]))
                f.write(f"- {item['path']}\n  - 旧哈希：{item.get('previous_hash')}\n  - 新哈希：{item['hash']}\n  - 文字差异：{diff_file}\n")
    _append_log(f"每日增量：新增{len(report['new'])}，修改{len(report['changed'])}，草稿{len(drafts)}，删除{len(report['removed'])}，重复组{len(report['duplicates'])}，相似组{len(report.get('similar_duplicates', []))}")
    return {**report, "drafts": drafts}


def list_pending_drafts():
    """Return pending update drafts for the GUI reviewer."""
    root = os.path.join(ensure_dirs(), "10_待审核更新")
    drafts = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(root, name)
        try:
            meta, text = _read_front_matter(path)
        except OSError:
            continue
        if meta.get("review_status") == "pending":
            drafts.append({"path": path, "name": name, "meta": meta, "text": text})
    return drafts


def update_draft_status(path, status):
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("草稿状态必须是 pending、approved 或 rejected")
    meta, text = _read_front_matter(path)
    if not meta:
        raise ValueError("草稿缺少有效 front matter")
    _set_review_status(text, path, status, meta.get("source_path"))
    _append_log(f"人工审核草稿：{os.path.basename(path)} -> {status}")
    return path


def _markdown_files(folders):
    root = ensure_dirs()
    for folder in folders:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for current, _, files in os.walk(base):
            for name in files:
                if name.lower().endswith(".md"):
                    yield os.path.join(current, name)


def generate_period_report(period="weekly"):
    """生成周报或月报草稿，不修改正式知识条目。"""
    if period not in {"weekly", "monthly"}:
        raise ValueError("周期报告只支持 weekly 或 monthly")
    now = datetime.date.today()
    label = "周报" if period == "weekly" else "月报"
    folder = "周报" if period == "weekly" else "月报"
    all_files = list(_markdown_files(["02_分类台账", "03_管理要点", "08_事件分析", "10_待审核更新"]))
    if period == "weekly":
        current_start = now - datetime.timedelta(days=now.weekday())
        previous_start = current_start - datetime.timedelta(days=7)
    else:
        current_start = now.replace(day=1)
        previous_end = current_start - datetime.timedelta(days=1)
        previous_start = previous_end.replace(day=1)
    current_end = now + datetime.timedelta(days=1)
    previous_end = current_start
    files = []
    previous_files = []
    for path in all_files:
        modified = datetime.date.fromtimestamp(os.path.getmtime(path))
        if current_start <= modified < current_end:
            files.append(path)
        elif previous_start <= modified < previous_end:
            previous_files.append(path)
    domains = Counter()
    attrs = Counter()
    signals = Counter()
    signal_terms = ["安全", "隐患", "整改", "责任", "流程", "考核", "制度", "岗位", "应急", "合规", "客户", "成本"]
    pending = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        domain_matches = re.findall(
            r"(?:\*\*)?(?:领域代码|领域)(?:\*\*)?[：:]\s*(?:\*\*)?([AB]\d)",
            text,
        )
        domain_matches += re.findall(r"^domain:\s*[\"']?([AB]\d)", text, re.M)
        for domain in domain_matches:
            domains[domain] += 1
        attr_matches = re.findall(
            r"(?:\*\*)?(?:属性代码|属性)(?:\*\*)?[：:]\s*(?:\*\*)?(P\d)",
            text,
        )
        attr_matches += re.findall(r"^attr:\s*[\"']?(P\d)", text, re.M)
        for attr in attr_matches:
            attrs[attr] += 1
        for term in signal_terms:
            if term in text:
                signals[term] += 1
        if "review_status: pending" in text:
            pending.append(os.path.basename(path))
    pending_backlog = 0
    for path in _markdown_files(["10_待审核更新"]):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                if "review_status: pending" in f.read():
                    pending_backlog += 1
        except OSError:
            continue
    out_dir = os.path.join(ensure_dirs(), "09_周期汇总", folder)
    out_file = os.path.join(out_dir, f"{label}_{now.isoformat()}.md")
    content = (
        f"# 知识库{label}（{now.isoformat()}）\n\n"
        f"- **本期新增/更新文档**：{len(files)}\n"
        f"- **上期文档数**：{len(previous_files)}\n"
        f"- **环比变化**：{len(files) - len(previous_files):+d}\n"
        f"- **待审核更新（当前积压）**：{pending_backlog}\n\n"
        "## 领域分布\n\n" + "\n".join(f"- {key}：{value}" for key, value in domains.most_common()) + "\n\n"
        "## 属性分布\n\n" + "\n".join(f"- {key}：{value}" for key, value in attrs.most_common()) + "\n\n"
        "## 问题热点（本期提及文档数）\n\n" + "\n".join(f"- {key}：{value}" for key, value in signals.most_common(8)) + "\n\n"
        "## 待审核更新\n\n" + "\n".join(f"- {name}" for name in pending) + "\n\n"
        "## 本期更新文档\n\n" + "\n".join(f"- {os.path.basename(path)}" for path in files) + "\n\n"
        "## 管理提示\n\n- 本报告为自动归纳草稿，发布前请人工核对原始资料和依据条款。\n"
    )
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    _append_log(f"生成{label}：{out_file}")
    return out_file


def _read_front_matter(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    values = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = value.strip('"')
            values[key.strip()] = value
    return values, text


def _section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.M)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^## ", text[start:], re.M)
    if next_heading:
        end = start + next_heading.start()
    else:
        end = text.find("<!-- ANALYSIS_JSON", start)
        if end < 0:
            end = len(text)
    return text[start:end].strip()


def _section_list(text, heading):
    value = _section(text, heading)
    return [
        re.sub(r"^(?:[-*]|\d+[.)])\s*", "", line).strip()
        for line in value.splitlines()
        if line.strip()
    ]


def publish_approved(tool_key=None):
    """发布明确标记为approved的待审核草稿，并保留原草稿。"""
    published = []
    root = os.path.join(ensure_dirs(), "10_待审核更新")
    for name in sorted(os.listdir(root)):
        if not name.endswith(".md"):
            continue
        path = os.path.join(root, name)
        try:
            meta, text = _read_front_matter(path)
            if meta.get("review_status") != "approved":
                continue
            source = meta.get("source_path")
            if not source or not os.path.isfile(source):
                _append_log(f"发布跳过 {name}：来源不存在")
                _set_review_status(text, path, "stale", source)
                continue
            if _sha256(source) != meta.get("content_hash"):
                _append_log(f"发布跳过 {name}：来源在审核后已变化，请重新生成草稿")
                _set_review_status(text, path, "stale", source)
                continue
            prepared = process.prepare_file(source, "rule")
            prepared["tag"]["domain"] = meta.get("domain", prepared["tag"].get("domain"))
            prepared["tag"]["attr"] = meta.get("attr", prepared["tag"].get("attr"))
            analysis = _analysis(prepared["text"], tool_key or "rule")
            issue = _section(text, "问题定性")
            essence = _section(text, "管理内涵草稿")
            consequences = _section(text, "后果归纳")
            roots = _section_list(text, "根因")
            measures = _section_list(text, "建议措施")
            if issue:
                analysis["issue"] = issue
            if essence:
                analysis["one_sentence"] = essence
            if roots:
                analysis["root_cause"] = roots
            if measures:
                analysis["measures"] = measures
            for line in consequences.splitlines():
                if "直接后果：" in line:
                    analysis["direct_consequence"] = line.split("直接后果：", 1)[1].strip()
                elif "扩散后果：" in line:
                    analysis["spread_consequence"] = line.split("扩散后果：", 1)[1].strip()
                elif "严重度：" in line:
                    analysis["severity"] = line.split("严重度：", 1)[1].strip()
            final = process.finalize_processed(
                source, prepared, tool_key or "rule", analysis_override=analysis,
            )
            text = text.replace("review_status: approved", "review_status: published")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            index = _load_index()
            file_entry = index.get("files", {}).get(os.path.abspath(source), {})
            file_entry.update({
                "status": "published",
                "review_status": "published",
                "reviewed_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "published_record_id": final["record_id"],
            })
            index.setdefault("files", {})[os.path.abspath(source)] = file_entry
            _save_index(index)
            published.append(final["record_id"])
        except Exception as exc:
            _append_log(f"发布失败 {name}：{exc}")
    _append_log(f"发布已审核更新：{len(published)}条")
    return published


def _set_review_status(text, path, status, source=None):
    updated = re.sub(r"(?m)^review_status:\s*[^\r\n]+", f"review_status: {status}", text, count=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)
    if source:
        index = _load_index()
        entry = index.get("files", {}).get(os.path.abspath(source))
        if entry is not None:
            entry["review_status"] = status
            index["files"][os.path.abspath(source)] = entry
            _save_index(index)


def run_job(kind):
    started = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        if kind == "daily":
            result = run_daily()
        elif kind in {"weekly", "monthly"}:
            result = generate_period_report(kind)
        elif kind == "publish":
            result = publish_approved()
        else:
            raise ValueError(f"未知治理任务: {kind}")
        finished = datetime.datetime.now().isoformat(timespec="seconds")
        summary = {key: len(value) for key, value in result.items() if isinstance(value, list)} if isinstance(result, dict) else {"result": str(result)}
        _append_audit(kind, "success", started, finished, summary)
        return result
    except Exception as exc:
        finished = datetime.datetime.now().isoformat(timespec="seconds")
        _append_audit(kind, "failed", started, finished, {"error": str(exc)})
        raise
