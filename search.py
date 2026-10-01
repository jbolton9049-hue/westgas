# -*- coding: utf-8 -*-
"""知识库本地全文检索。"""

import os
import re
import datetime
import sqlite3
from collections import Counter

from ai_tools import (
    get_kb_path,
    DOMAIN_LABELS,
    ATTR_LABELS,
    classification_label,
)
import knowledge_db


SEARCH_FOLDERS = ["02_分类台账", "03_管理要点", "04_记忆卡片", "07_管理依据库", "08_事件分析"]


def _terms(query):
    values = [item for item in re.split(r"[\s,，;；]+", query.strip()) if item]
    return values or [query.strip()]


def _evidence(path, terms, width=320):
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            text = stream.read()
    except OSError:
        return ""
    positions = [text.lower().find(term.lower()) for term in terms if term and term.lower() in text.lower()]
    first = min(positions) if positions else 0
    return re.sub(r"\s+", " ", text[max(0, first - 100):first + width]).strip()


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            return stream.read()
    except OSError:
        return ""


def search_knowledge(query, limit=30, domain=None, attr=None, synonyms=None):
    """返回按关键词命中次数排序的 Markdown 文档。"""
    if not query or not query.strip():
        return []
    expanded = []
    synonym_map = synonyms or {}
    for term in _terms(query):
        expanded.append(term)
        expanded.extend(synonym_map.get(term, []))
    expanded = list(dict.fromkeys(expanded))
    try:
        rows = []
        seen_paths = set()
        for term in expanded:
            for item in knowledge_db.search(get_kb_path(), SEARCH_FOLDERS, term, limit * 2):
                if item["path"] not in seen_paths:
                    rows.append(item)
                    seen_paths.add(item["path"])
        filtered = []
        for row in rows:
            source_text = _read_text(row["path"])
            if domain and str(domain).upper() not in _category_code(_field(source_text, "领域", ""), "[A-Z]"):
                continue
            if attr and str(attr).upper() not in _category_code(_field(source_text, "属性", ""), "P"):
                continue
            row["evidence"] = _evidence(row["path"], expanded)
            filtered.append(row)
        return filtered[:limit]
    except (OSError, sqlite3.Error, ValueError):
        pass
    terms = _terms(query)
    results = []
    root = get_kb_path()
    for folder in SEARCH_FOLDERS:
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for current, _, files in os.walk(base):
            for name in files:
                if not name.lower().endswith(".md"):
                    continue
                path = os.path.join(current, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                score = sum(text.lower().count(term.lower()) * 2 for term in terms)
                score += sum(name.lower().count(term.lower()) * 4 for term in terms)
                if score <= 0:
                    continue
                first = min((text.lower().find(t.lower()) for t in terms if t.lower() in text.lower()), default=0)
                start = max(0, first - 80)
                snippet = re.sub(r"\s+", " ", text[start:first + 220]).strip()
                if domain or attr:
                    fields = text
                    if domain and str(domain).upper() not in _category_code(_field(fields, "领域", ""), "[A-Z]"):
                        continue
                    if attr and str(attr).upper() not in _category_code(_field(fields, "属性", ""), "P"):
                        continue
                results.append({"score": score, "name": name, "path": path, "snippet": snippet,
                                "evidence": _evidence(path, expanded)})
    return sorted(results, key=lambda item: (-item["score"], item["name"]))[:limit]


def semantic_search(query, limit=20):
    """轻量语义检索：字符二元组相似度，后续可替换为向量索引。"""
    query_tokens = _issue_tokens(query)
    candidates = search_knowledge(" ".join(sorted(query_tokens)), limit=limit * 5)
    scored = []
    for row in candidates:
        tokens = _issue_tokens(row.get("snippet", "") + row.get("name", ""))
        score = len(query_tokens & tokens) / max(len(query_tokens | tokens), 1)
        if score:
            scored.append({**row, "semantic_score": round(score, 4)})
    return sorted(scored, key=lambda item: (-item["semantic_score"], -item.get("score", 0)))[:limit]


def find_related_documents(path, limit=10):
    try:
        with open(path, encoding="utf-8", errors="ignore") as stream:
            text = stream.read()
    except OSError:
        return []
    terms = list(_issue_tokens(text))[:20]
    if not terms:
        return []
    return [row for row in search_knowledge(" ".join(terms[:5]), limit=limit + 1)
            if os.path.abspath(row["path"]) != os.path.abspath(path)][:limit]


def issue_trends():
    """按月份、领域和属性统计已审核问题，供治理看板使用。"""
    counter = Counter()
    for row in _ledger_findings():
        try:
            with open(row["path"], encoding="utf-8", errors="ignore") as stream:
                text = stream.read()
            date = _field(text, "日期", datetime.date.today().isoformat())[:7]
        except OSError:
            date = datetime.date.today().isoformat()[:7]
        counter[(date, row.get("domain", "待定"), row.get("attr", "待定"))] += 1
    return [{"month": key[0], "domain": key[1], "attr": key[2], "count": value}
            for key, value in sorted(counter.items())]


def generate_topic_report(topic):
    groups = summarize_similar_issues(topic)
    lines = [f"# 专题报告：{topic}", "", f"生成时间：{datetime.datetime.now().isoformat(timespec='seconds')}", ""]
    lines.append(f"共发现 {sum(item['count'] for item in groups)} 条相关已审核问题，归并为 {len(groups)} 组。\n")
    for index, group in enumerate(groups, 1):
        lines.append(f"## {index}. {group['summary']}（{group['domain']} / {group['attr']}）")
        lines.extend(f"- {item}" for item in group.get("issues", [])[:5])
        lines.append("### 建议措施")
        lines.extend(f"{n}. {item}" for n, item in enumerate(group.get("measures", []), 1))
        lines.append("### 原文依据")
        lines.extend(f"- {item['name']}：{item.get('evidence', '')}" for item in group.get("sources", []))
        lines.append("")
    return "\n".join(lines)


def save_topic_report(topic):
    folder = os.path.join(get_kb_path(), "09_周期汇总")
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(topic).strip())[:60] or "专题"
    path = os.path.join(folder, f"专题报告_{safe}_{datetime.date.today().isoformat()}.md")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(generate_topic_report(topic))
    return path


def _issue_tokens(text):
    """Tokenize Chinese issue text without requiring an external NLP package."""
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", str(text or "")).lower()
    if len(text) <= 2:
        return {text} if text else set()
    # Character bigrams work well for short Chinese management descriptions.
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _field(text, label, default=""):
    match = re.search(rf"- \*\*{re.escape(label)}\*\*：?\s*(.+)", text)
    return match.group(1).strip() if match else default


def _classification(value, labels, prefix):
    """Normalize a stored category while retaining a readable label.

    Older ledgers sometimes contain only ``B3``/``P1`` while newer ledgers
    store ``B3 安全管理``/``P1 管理问题或缺陷``.  Grouping uses the code so
    those records can be merged, and the returned value always shows the
    category name to a reviewer.
    """
    raw = str(value or "").strip()
    match = re.search(rf"\b({prefix}\d+)\b", raw, re.I)
    if match:
        code = match.group(1).upper()
        return classification_label(code, labels)
    return raw or "待定"


def _category_code(value, prefix):
    match = re.search(rf"\b({prefix}\d+)\b", str(value or ""), re.I)
    return match.group(1).upper() if match else str(value or "待定").strip() or "待定"


def _section_items(text, heading, next_heading=None):
    """Read markdown list items under a heading."""
    if heading not in text:
        return []
    part = text.split(heading, 1)[1]
    if next_heading and next_heading in part:
        part = part.split(next_heading, 1)[0]
    return [item.strip() for item in re.findall(r"^\s*(?:[-*]|\d+[.)])\s+(.+)$", part, re.M)
            if item.strip() and not item.strip().startswith("（待")]


def _parse_ledger(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
            text = stream.read()
    except OSError:
        return []
    # 分类台账通常在人工审核通过后生成；若文件明确仍待审核，不参与
    # 汇总，避免把审核草稿当成已确认问题。
    if re.search(r"- \[ \]\s*已人工复核|审核状态\s*[：:]\s*待人工", text):
        return []
    domain_raw = _field(text, "领域", _field(text, "领域代码", "待定"))
    attr_raw = _field(text, "属性", _field(text, "属性代码", "待定"))
    base = {
        "name": os.path.basename(path),
        "path": path,
        "domain": _classification(domain_raw, DOMAIN_LABELS, "[A-Z]"),
        "attr": _classification(attr_raw, ATTR_LABELS, "P"),
        "domain_code": _category_code(domain_raw, "[A-Z]"),
        "attr_code": _category_code(attr_raw, "P"),
        "source": _field(text, "来源", ""),
    }
    # Keep document level roots/measures as a fallback for item-level findings.
    document_roots = _section_items(text, "### 根因", "### 缺失机制")
    document_measures = _section_items(text, "## 五、建议措施", "## 六、处理状态")
    findings = []
    section = text.split("## AI提取的具体问题", 1)[-1] if "## AI提取的具体问题" in text else ""
    blocks = re.split(r"(?=###\s+\d+\.)", section)
    for block in blocks:
        statement = _field(block, "问题判断")
        if not statement:
            continue
        if re.search(r"审核状态\s*[：:]\s*(?:待人工|未通过|不同意)", block):
            continue
        findings.append({
            **base,
            "title": re.search(r"###\s+\d+\.\s*(.+)", block).group(1).strip()
            if re.search(r"###\s+\d+\.\s*(.+)", block) else statement[:40],
            "issue": statement,
            "evidence": _field(block, "原文依据"),
            "domain": _classification(_field(block, "领域/属性", base["domain"])
                                      .split("/")[0].strip(), DOMAIN_LABELS, "[A-Z]"),
            "attr": _classification(_field(block, "领域/属性", base["attr"])
                                    .split("/")[-1].strip(), ATTR_LABELS, "P"),
            "domain_code": _category_code(_field(block, "领域/属性", base["domain"])
                                           .split("/")[0].strip(), "[A-Z]"),
            "attr_code": _category_code(_field(block, "领域/属性", base["attr"])
                                         .split("/")[-1].strip(), "P"),
            "insight": _field(block, "管理内涵候选"),
            "confidence": _field(block, "可信度", "待人工确认"),
            "root": document_roots,
            "measures": document_measures,
        })
    if findings:
        return findings
    issue_match = re.search(r"## 二、问题定性\s*\n\s*(.+)", text)
    issue = issue_match.group(1).strip() if issue_match else ""
    if issue:
        roots = re.findall(r"^[-*]\s+(.+)$", text.split("### 根因", 1)[-1].split("### 缺失机制", 1)[0], re.M)
        measures_part = text.split("## 五、建议措施", 1)[-1].split("## 六、处理状态", 1)[0]
        measures = re.findall(r"^\d+\.\s+(.+)$", measures_part, re.M)
        return [{**base, "title": os.path.splitext(base["name"])[0], "issue": issue,
                 "evidence": _field(text, "来源"), "insight": _field(text, "管理内涵"),
                 "confidence": _field(text, "可信度", "待人工确认"), "root": roots,
                 "measures": measures}]
    return []


def _ledger_findings(query=""):
    base = os.path.join(get_kb_path(), "02_分类台账")
    if not os.path.isdir(base):
        return []
    terms = _terms(query) if query and query.strip() else []
    rows = []
    for current, _, files in os.walk(base):
        for name in files:
            if name.lower().endswith(".md"):
                rows.extend(_parse_ledger(os.path.join(current, name)))
    if not terms:
        return rows
    return [row for row in rows if any(
        term.lower() in " ".join(str(row.get(key, "")) for key in ("issue", "title", "domain", "attr")).lower()
        for term in terms
    )]


def summarize_similar_issues(query="", limit=20):
    """Group repeated ledger issues and produce evidence-linked recommendations."""
    rows = _ledger_findings(query)
    groups = []
    for row in rows:
        tokens = _issue_tokens(row.get("issue"))
        best = None
        best_score = 0.0
        for group in groups:
            if (row.get("domain"), row.get("attr")) != group["classification"]:
                continue
            overlap = len(tokens & group["tokens"])
            union = len(tokens | group["tokens"]) or 1
            score = overlap / union
            if score > best_score:
                best, best_score = group, score
        if best is None or best_score < 0.16:
            best = {"classification": (row.get("domain"), row.get("attr")),
                    "tokens": set(tokens), "rows": []}
            groups.append(best)
        best["rows"].append(row)
        best["tokens"] |= tokens

    output = []
    defaults = [
        "明确牵头部门、责任人和可验收结果，形成责任清单。",
        "把问题转成流程节点、办理时限、证据要求和升级条件。",
        "建立月度检查、异常升级和闭环销项机制，重复问题必须复盘。",
        "将关键结果纳入考核，区分过程行为与最终结果，及时反馈。",
        "验证有效后沉淀为制度条款、模板和培训案例。",
    ]
    for group in groups:
        items = group["rows"]
        issue_counts = Counter(item.get("issue", "") for item in items)
        root_counts = Counter(root for item in items for root in item.get("root", []))
        measure_counts = Counter(measure for item in items for measure in item.get("measures", []))
        def unique(values):
            return list(dict.fromkeys(value for value in values if value))
        sources = []
        seen_sources = set()
        for item in items:
            if item["path"] in seen_sources:
                continue
            seen_sources.add(item["path"])
            sources.append({
                "name": item["name"],
                "path": item["path"],
                "evidence": item.get("evidence", ""),
            })
        output.append({
            "count": len(items),
            "domain": group["classification"][0],
            "attr": group["classification"][1],
            "summary": issue_counts.most_common(1)[0][0] if issue_counts else "同类管理问题",
            "issues": unique(item.get("issue") for item in items),
            "root_causes": [item for item, _ in root_counts.most_common(5)] or ["需结合多份原始资料进一步核实共同根因。"],
            "measures": [item for item, _ in measure_counts.most_common(5)] or defaults,
            "insights": unique(item.get("insight") for item in items),
            "sources": sources,
        })
    return sorted(output, key=lambda item: (-item["count"], item["domain"], item["attr"]))[:limit]


def format_issue_summary(groups):
    """Render a reviewable summary for the GUI or a saved Markdown report."""
    if not groups:
        return "没有找到可归并的已审核问题。\n"
    lines = [f"同类问题汇总（共 {len(groups)} 组）", "=" * 60, ""]
    for index, group in enumerate(groups, 1):
        lines += [
            f"[{index}] {group['count']} 条｜{group['domain']} / {group['attr']}",
            f"共性问题：{group['summary']}",
            "问题记录：",
        ]
        lines.extend(f"  - {issue}" for issue in group["issues"][:8])
        lines.append("共同根因候选：")
        lines.extend(f"  - {cause}" for cause in group["root_causes"])
        if group.get("insights"):
            lines.append("管理内涵候选：")
            lines.extend(f"  - {insight}" for insight in group["insights"][:5])
        lines.append("建议措施：")
        lines.extend(f"  {n}. {measure}" for n, measure in enumerate(group["measures"], 1))
        lines.append("来源依据：")
        lines.extend(f"  - {source['name']}：{source['evidence'][:180]}" for source in group["sources"])
        lines.append("")
    return "\n".join(lines)


def save_issue_summary(groups):
    """Save a dated summary under the knowledge base for later review."""
    folder = os.path.join(get_kb_path(), "09_周期汇总")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"同类问题汇总_{datetime.date.today().isoformat()}.md")
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("# 同类问题汇总\n\n" + format_issue_summary(groups))
    return path


def match_management_basis(source_text, limit=5):
    """从07_管理依据库中返回关键词命中的候选原文，不替代人工定条。"""
    if not source_text:
        return []
    terms = []
    for value in re.findall(r"[\u4e00-\u9fff]{2,8}", source_text):
        if value not in terms and value not in {"管理", "工作", "问题", "要求", "进行"}:
            terms.append(value)
    terms = terms[:80]
    results = []
    base = os.path.join(get_kb_path(), "07_管理依据库")
    if not os.path.isdir(base):
        return results
    for current, _, files in os.walk(base):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(current, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            matched = [term for term in terms if term in text]
            if not matched:
                continue
            first = min(text.find(term) for term in matched)
            results.append({
                "name": name,
                "path": path,
                "score": len(matched),
                "terms": matched[:12],
                "snippet": re.sub(r"\s+", " ", text[max(0, first - 80):first + 260]).strip(),
            })
    return sorted(results, key=lambda item: (-item["score"], item["name"]))[:limit]
