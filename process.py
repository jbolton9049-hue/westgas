# -*- coding: utf-8 -*-
"""处理模块：PDF/文本转文字、去噪、自动打标、写入分类台账。"""

import os
import re
import datetime
import hashlib
import csv
import io
import zipfile
import xml.etree.ElementTree as ET
import subprocess
import sys

from ai_tools import (
    get_kb_path, process_by_tool, DOMAIN_LABELS, ATTR_LABELS, classification_label,
    PROMPT_VERSION, RULE_VERSION,
)

# Keep this list as the single source of truth for files that can be both
# collected and converted to text.  The GUI and the daily governance job use
# it too, so a file cannot be accepted and then silently become unprocessable.
SUPPORTED = {".pdf", ".txt", ".md", ".docx", ".csv", ".xlsx"}


def _atomic_write_text(path, content):
    """Write generated knowledge atomically so an interruption cannot leave a half file."""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _ocr_script_path():
    root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "ocr_windows.ps1")


def _extract_pdf_ocr(file_path):
    """Use Windows' built-in Chinese OCR to read scanned PDF pages."""
    script = _ocr_script_path()
    if os.name != "nt" or not os.path.isfile(script):
        return ""
    powershell = os.environ.get("POWERSHELL_EXE") or "powershell.exe"
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    output = completed.stdout.decode("utf-8-sig", errors="replace")
    if completed.returncode != 0:
        return ""
    pages = []
    for chunk in re.split(r"===PAGE\s+\d+===", output):
        chunk = chunk.strip()
        if chunk:
            pages.append(chunk)
    return "\n\n".join(pages).strip()


def _extract_pdf_with_method(file_path):
    from pypdf import PdfReader
    reader = PdfReader(file_path)
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    native = "\n".join(pages)
    if native.strip():
        return native, "PDF文字层"
    ocr = _extract_pdf_ocr(file_path)
    if ocr:
        return ocr, "Windows中文OCR"
    return native, "PDF文字层为空（OCR不可用）"


def extract_text_with_method(file_path):
    """Extract text and report whether native text or OCR was used."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf_with_method(file_path)
    if ext == ".docx":
        return _extract_docx(file_path), "DOCX文字"
    if ext == ".csv":
        return _extract_csv(file_path), "CSV文字"
    if ext == ".xlsx":
        return _extract_xlsx(file_path), "XLSX文字"
    # txt / md
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(), "文本文件"


def extract_text(file_path):
    """按类型提取文字，保持旧接口兼容。"""
    return extract_text_with_method(file_path)[0]


def _extract_csv(file_path):
    """将 CSV 转为可检索的制表文本，兼容常见中文编码。"""
    with open(file_path, "rb") as stream:
        raw = stream.read()
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - the final fallback always decodes
        text = raw.decode("utf-8", errors="replace")
    rows = csv.reader(io.StringIO(text))
    return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)


def _xml_local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _extract_xlsx(file_path):
    """Extract visible cell values from an XLSX without requiring Excel.

    The standard library is used deliberately so the packaged application can
    process spreadsheets on a clean machine.  Sheet names are not needed for
    search, while row boundaries and cell order are retained.
    """
    with zipfile.ZipFile(file_path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter():
                if _xml_local_name(item.tag) == "si":
                    shared.append("".join(
                        node.text or "" for node in item.iter()
                        if _xml_local_name(node.tag) == "t"
                    ))
        sheet_names = sorted(
            name for name in archive.namelist()
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
        )
        output = []
        for sheet_name in sheet_names:
            root = ET.fromstring(archive.read(sheet_name))
            for row in root.iter():
                if _xml_local_name(row.tag) != "row":
                    continue
                values = []
                for cell in row:
                    if _xml_local_name(cell.tag) != "c":
                        continue
                    kind = cell.attrib.get("t")
                    value_node = next(
                        (node for node in cell if _xml_local_name(node.tag) == "v"),
                        None,
                    )
                    inline = next(
                        (node for node in cell if _xml_local_name(node.tag) == "is"),
                        None,
                    )
                    if inline is not None:
                        value = "".join(
                            node.text or "" for node in inline.iter()
                            if _xml_local_name(node.tag) == "t"
                        )
                    else:
                        value = value_node.text if value_node is not None else ""
                    if kind == "s" and value:
                        try:
                            value = shared[int(value)]
                        except (ValueError, IndexError):
                            pass
                    values.append(value or "")
                if values and any(value.strip() for value in values):
                    output.append(" | ".join(value.strip() for value in values))
        return "\n".join(output)


def _extract_pdf(file_path):
    return _extract_pdf_with_method(file_path)[0]


def _extract_docx(file_path):
    from docx import Document
    doc = Document(file_path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(parts)


def clean_text(text):
    """去噪：去页眉页脚/空行/重复段落，压缩空白。"""
    lines = [ln.strip() for ln in text.splitlines()]
    out = []
    previous = None
    for ln in lines:
        if not ln:
            continue
        # 简单页眉页脚过滤：极短、像页码
        if re.fullmatch(r"[-—_.· ]*\d+[-—_.· ]*", ln):
            continue
        # 只去除连续重复行，避免误删制度中有意重复的条款。
        if ln == previous:
            continue
        out.append(ln)
        previous = ln
    return "\n".join(out)


def assess_ocr_quality(text, extraction_method):
    """给审核人员一个可解释的提取质量提示，不把提示当成审核结论。"""
    value = str(text or "")
    replacement = value.count("�")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    warnings = []
    page_values = [part.strip() for part in re.split(r"(?:===PAGE\s+\d+===|\n\s*\n)", value) if part.strip()]
    pages = []
    for index, page in enumerate(page_values or [value], 1):
        page_replacement = page.count("�")
        page_chinese = len(re.findall(r"[\u4e00-\u9fff]", page))
        page_warning = []
        if "OCR" in str(extraction_method) and len(page) < 40:
            page_warning.append("文字偏少")
        if page_replacement:
            page_warning.append("含异常字符")
        pages.append({"page": index, "characters": len(page),
                      "chinese_characters": page_chinese,
                      "warning": "；".join(page_warning),
                      "needs_manual_review": bool(page_warning)})
    if not value.strip():
        warnings.append("未提取到文字")
    if "OCR" in str(extraction_method) and len(value.strip()) < 80:
        warnings.append("OCR文字较少，建议对照原图复核")
    if replacement:
        warnings.append(f"发现 {replacement} 个无法识别字符")
    if len(value.strip()) and chinese / max(len(value.strip()), 1) < 0.08 and "OCR" in str(extraction_method):
        warnings.append("中文识别比例偏低，建议复核")
    return {
        "method": extraction_method,
        "characters": len(value),
        "chinese_characters": chinese,
        "warning": "；".join(warnings) if warnings else "",
        "level": "需复核" if warnings else "正常",
        "page_count": len(pages),
        "pages": pages,
        "needs_manual_review": bool(warnings or any(page["needs_manual_review"] for page in pages)),
    }


def _record_id(src_file, text):
    digest = hashlib.sha256((os.path.abspath(src_file) + text[:2000]).encode("utf-8")).hexdigest()[:8]
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"JT-{stamp}-{digest}"


def _safe_name(value):
    return re.sub(r'[\\/:*?"<>|]', "_", str(value)).strip()[:80] or "未命名"


def ensure_knowledge_base():
    """建立自动闭环需要的标准目录。"""
    root = get_kb_path()
    for folder in [
        "00_系统说明", "01_原始资料库", "02_分类台账", "03_管理要点",
        "04_记忆卡片", "05_训练记录", "06_技术规划", "07_管理依据库", "08_事件分析",
    ]:
        os.makedirs(os.path.join(root, folder), exist_ok=True)
    return root


def write_raw_processed(file_path, clean_text_content, tag_result):
    """把预处理结果写入 01_原始资料库 的 .md 文件。"""
    raw_dir = os.path.join(ensure_knowledge_base(), "01_原始资料库")
    os.makedirs(raw_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(file_path))[0]
    digest = hashlib.sha256(os.path.abspath(file_path).encode("utf-8")).hexdigest()[:8]
    out_file = os.path.join(raw_dir, f"{_safe_name(base_name)}_{digest}_预处理.md")
    domain = tag_result.get("domain", "待定")
    attr = tag_result.get("attr", "待定")
    content = (
        f"# {base_name}（预处理）\n\n"
        f"> 本文件由《天然气管理工具》自动生成。\n\n"
        f"---\n\n- **标题**：{base_name}\n"
        f"- **日期**：{datetime.date.today().isoformat()}\n"
        f"- **来源类型**：制度文件/会议纪要/其他\n"
        f"- **来源人/部门**：\n"
        f"- **原始链接/位置**：{file_path}\n\n---\n\n"
        f"## 建议打标（{tag_result.get('tool','')}）\n\n"
        f"- **领域**：{classification_label(domain, DOMAIN_LABELS)}\n"
        f"- **属性**：{classification_label(attr, ATTR_LABELS)}\n"
        f"- **可信度**：待人工确认\n\n"
        f"---\n\n## 去噪后正文\n\n{clean_text_content}\n"
    )
    _atomic_write_text(out_file, content)
    return out_file


def write_ledger(text, tag_result, src_file, analysis=None, record_id=None):
    """把打标+提炼结果写入 02_分类台账 的 .md 文件。"""
    ledger_dir = os.path.join(ensure_knowledge_base(), "02_分类台账")
    os.makedirs(ledger_dir, exist_ok=True)
    domain = tag_result.get("domain", "")
    attr = tag_result.get("attr", "")
    record_id = record_id or _record_id(src_file, text)
    analysis = analysis or {}
    out_file = os.path.join(ledger_dir, f"台账_{record_id}_{domain}_{attr}.md")
    roots = analysis.get("root_cause") or ["（待人工复核）"]
    if isinstance(roots, str):
        roots = [roots]
    measures = analysis.get("measures") or ["（待人工补充）"]
    if isinstance(measures, str):
        measures = [measures]
    findings = analysis.get("findings") or []
    findings_text = ""
    if findings:
        findings_text = "\n## AI提取的具体问题（逐条人工确认）\n\n"
        for index, item in enumerate(findings, 1):
            if not isinstance(item, dict):
                continue
            findings_text += (
                f"### {index}. {item.get('title', '待命名问题')}\n\n"
                f"- **问题判断**：{item.get('statement', '')}\n"
                f"- **原文依据**：{item.get('evidence', '待人工补充')}\n"
                f"- **领域/属性**：{classification_label(item.get('domain', domain), DOMAIN_LABELS)} / "
                f"{classification_label(item.get('attr', attr), ATTR_LABELS)}\n"
                f"- **可信度**：{item.get('confidence', '待人工确认')}\n"
                f"- **管理内涵候选**：{item.get('management_insight', '待人工提炼')}\n"
                f"- **审核状态**：已通过文件级人工审核\n\n"
            )
    trace = analysis.get("processing_trace") or {}
    trace_lines = []
    if trace:
        trace_lines.append(f"- **请求工具**：{trace.get('requested_tool', '未记录')}")
        trace_lines.append(f"- **实际工具**：{trace.get('actual_tool', '未记录')}")
        trace_lines.append(f"- **是否回退规则版**：{'是' if trace.get('fallback') else '否'}")
        for stage, detail in (trace.get("tasks") or {}).items():
            state = detail.get("status", "未知") if isinstance(detail, dict) else str(detail)
            line = f"- **{stage}**：{state}"
            if isinstance(detail, dict) and detail.get("error"):
                line += f"（{detail['error']}）"
            trace_lines.append(line)
    trace_text = "\n".join(trace_lines) if trace_lines else "- **调用追踪**：未记录（旧记录）"
    content = (
        f"# 台账：{os.path.basename(src_file)}\n\n"
        f"- **记录编号**：{record_id}\n"
        f"- **日期**：{datetime.date.today().isoformat()}\n"
        f"- **领域**：{classification_label(domain, DOMAIN_LABELS)}\n"
        f"- **属性**：{classification_label(attr, ATTR_LABELS)}\n"
        f"- **分类工具**：{tag_result.get('tool', '未知')}\n"
        f"- **分析工具**：{analysis.get('tool', '未知')}\n"
        f"- **处理说明**：{analysis.get('note', tag_result.get('note', '自动生成，待人工确认'))}\n"
        f"- **调用追踪**：\n{trace_text}\n"
        f"- **可信度**：待人工确认\n- **来源**：{src_file}\n\n---\n\n"
        f"## 一、原始内容摘要\n\n{text[:500]}\n\n---\n\n"
        f"## 二、问题定性\n\n{analysis.get('issue', '（待人工复核）')}\n\n"
        f"### 根因\n\n" + "\n".join(f"- {item}" for item in roots) + "\n\n"
        f"### 缺失机制\n\n{analysis.get('mechanism', '（待人工复核）')}\n\n---\n\n"
        f"## 三、管理内涵提炼\n\n{analysis.get('one_sentence', analysis.get('content', '（待人工复核）'))}\n\n---\n\n"
        f"## 四、后果归纳\n\n"
        f"- **直接后果**：{analysis.get('direct_consequence', '待复核')}\n"
        f"- **扩散后果**：{analysis.get('spread_consequence', '待复核')}\n"
        f"- **组织现象**：{analysis.get('organizational_phenomenon', '待复核')}\n"
        f"- **严重度**：{analysis.get('severity', '待复核')}\n\n---\n\n"
        f"## 五、建议措施\n\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(measures, 1)) + "\n\n---\n\n"
        f"## 六、处理状态\n\n- [x] 已自动分析\n- [x] 已人工复核\n- [ ] 已归档\n"
        + findings_text
    )
    _atomic_write_text(out_file, content)
    return out_file


def _run_with_fallback(text, task, requested_tool, trace):
    """Run one stage and record the actual tool and any transparent fallback."""
    requested_tool = requested_tool or "rule"
    stage_names = {"tag": "打标", "findings": "问题提取", "analyze": "管理分析", "extract": "管理内涵"}
    stage = stage_names.get(task, task)
    try:
        result = process_by_tool(text, task=task, tool_key=requested_tool)
        if requested_tool != "rule":
            if task == "tag" and (result.get("domain") not in DOMAIN_LABELS or result.get("attr") not in ATTR_LABELS):
                raise ValueError("AI打标返回无效领域或属性")
            if task == "analyze" and result.get("parse_error"):
                raise ValueError(result["parse_error"])
            if task == "findings" and result.get("parse_error"):
                raise ValueError(result["parse_error"])
        actual = result.get("tool") or ("规则版" if requested_tool == "rule" else requested_tool)
        trace.setdefault("tasks", {})[stage] = {"status": "成功", "tool": actual}
        if trace.get("fallback"):
            trace["actual_tool"] = "AI + 规则版（部分回退）"
        else:
            trace["actual_tool"] = actual
        return result
    except Exception as exc:
        if requested_tool == "rule":
            trace.setdefault("tasks", {})[stage] = {"status": "失败", "tool": "规则版", "error": str(exc)}
            raise
        fallback = process_by_tool(text, task=task, tool_key="rule")
        trace["fallback"] = True
        trace["actual_tool"] = ("AI + 规则版（部分回退）" if any(
            detail.get("status") == "成功" and detail.get("tool") != "rule"
            for detail in trace.get("tasks", {}).values()) else "规则版（回退）")
        trace.setdefault("tasks", {})[stage] = {
            "status": "失败，已回退规则版", "tool": "规则版", "error": str(exc),
        }
        fallback["note"] = f"{stage}：{requested_tool}调用失败，已回退规则版：{exc}"
        return fallback


def write_solution(src_file, tag_result, analysis, record_id):
    out_dir = os.path.join(ensure_knowledge_base(), "03_管理要点")
    title = _safe_name(os.path.splitext(os.path.basename(src_file))[0])
    out_file = os.path.join(out_dir, f"{record_id}_{title}_一页纸.md")
    measures = analysis.get("measures") or []
    if isinstance(measures, str):
        measures = [measures]
    content = (
        f"# 一页纸解决思路：{title}\n\n"
        f"- **关联记录**：[[台账_{record_id}_{tag_result.get('domain')}_{tag_result.get('attr')}|{record_id}]]\n"
        f"- **领域/属性**：{classification_label(tag_result.get('domain'), DOMAIN_LABELS)} / "
        f"{classification_label(tag_result.get('attr'), ATTR_LABELS)}\n"
        f"- **复核状态**：待人工复核\n\n"
        f"## 管理判断\n\n{analysis.get('issue', '待复核')}\n\n"
        f"## 总经理一句话\n\n{analysis.get('one_sentence', analysis.get('content', '待复核'))}\n\n"
        f"## 五维措施\n\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(measures, 1)) + "\n\n"
        "## 验收标准\n\n- 责任到人\n- 节点可查\n- 结果可验\n- 问题可追\n"
    )
    _atomic_write_text(out_file, content)
    return out_file


def write_event_analysis(src_file, analysis, record_id, source_text=""):
    out_dir = os.path.join(ensure_knowledge_base(), "08_事件分析")
    event_type = analysis.get("event_type", "negative")
    label = "积极事件" if event_type == "positive" else "消极事件"
    out_file = os.path.join(out_dir, f"{record_id}_{label}.md")
    action_title = "经验固化" if event_type == "positive" else "改进措施"
    measures = analysis.get("measures") or []
    if isinstance(measures, str):
        measures = [measures]
    from search import match_management_basis
    basis = match_management_basis(source_text)
    if basis:
        basis_text = "\n".join(
            f"- [[{os.path.splitext(item['name'])[0]}]]（命中：{'、'.join(item['terms'])}）\n  > {item['snippet']}"
            for item in basis
        )
    else:
        basis_text = "- 暂无候选原文；请人工从07_管理依据库确认。"
    content = (
        f"# {label}分析：{os.path.basename(src_file)}\n\n"
        f"- **关联记录**：{record_id}\n- **管理依据候选**：\n{basis_text}\n"
        f"- **禁止推断条款**：未匹配到原文时不得编造依据\n\n"
        f"## 事件判断\n\n{analysis.get('issue', '待复核')}\n\n"
        f"## {action_title}\n\n" + "\n".join(f"- {item}" for item in measures) + "\n"
    )
    _atomic_write_text(out_file, content)
    return out_file


def finalize_processed(file_path, processed, tool_key=None, analysis_override=None):
    """在人工确认后，从已预处理结果生成管理内涵和完整闭环产物。"""
    cleaned = processed.get("text", "")
    trace = processed.setdefault("processing_trace", {
        "requested_tool": tool_key or "rule", "actual_tool": "", "fallback": False, "tasks": {},
    })
    tag = processed.get("tag") or _run_with_fallback(cleaned, "tag", tool_key or "rule", trace)
    if analysis_override is not None:
        analysis = analysis_override
    elif processed.get("analysis") is not None:
        analysis = processed["analysis"]
    else:
        try:
            analysis = _run_with_fallback(cleaned, "analyze", tool_key or "rule", trace)
        except Exception as exc:
            analysis = process_by_tool(cleaned, task="analyze", tool_key="rule")
            analysis["note"] = f"AI分析失败，已回退规则版：{exc}"
    analysis.setdefault("processing_trace", trace)
    record_id = _record_id(file_path, cleaned)
    raw_file = processed.get("raw_file") or write_raw_processed(file_path, cleaned, tag)
    ledger_file = write_ledger(cleaned, tag, file_path, analysis, record_id)
    solution_file = write_solution(file_path, tag, analysis, record_id)
    event_file = write_event_analysis(file_path, analysis, record_id, cleaned)

    from train import generate_card
    title = os.path.splitext(os.path.basename(file_path))[0]
    roots = analysis.get("root_cause") or []
    measures = analysis.get("measures") or []
    root_text = "；".join(roots) if isinstance(roots, list) else str(roots)
    action_text = "；".join(measures) if isinstance(measures, list) else str(measures)
    card_file = generate_card(
        title, analysis.get("issue", "待复核"), root_text,
        action_text, analysis.get("one_sentence", analysis.get("content", "待复核")),
        source_id=record_id, source_path=file_path,
        topic=f"{tag.get('domain', '')}/{tag.get('attr', '')}",
    )
    return {
        "ok": True, "record_id": record_id, "chars": len(cleaned), "tag": tag,
        "analysis": analysis, "raw_file": raw_file, "ledger_file": ledger_file,
        "solution_file": solution_file, "event_file": event_file, "card_file": card_file,
        "text": cleaned,
    }


def prepare_file(file_path, tool_key=None):
    """审核前预处理：提取、去噪、打标，只写预处理正文，不创建正式台账。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED:
        raise ValueError(f"处理模块暂不支持 {ext}（支持 {sorted(SUPPORTED)}）")
    raw, extraction_method = extract_text_with_method(file_path)
    ocr_quality = assess_ocr_quality(raw, extraction_method)
    if not raw or not raw.strip():
        return {
            "ok": False,
            "msg": (
                "未能从文件提取到文字；Windows中文OCR不可用或未安装，"
                "请在系统语言设置中安装中文（简体）OCR后重试。"
            ),
            "extraction_method": extraction_method,
            "ocr_quality": ocr_quality,
        }
    cleaned = clean_text(raw)
    trace = {
        "requested_tool": tool_key or "rule", "actual_tool": "", "fallback": False, "tasks": {},
        "prompt_version": PROMPT_VERSION, "rule_version": RULE_VERSION,
        "extraction_method": extraction_method, "ocr_quality": ocr_quality,
    }
    tag = _run_with_fallback(cleaned, "tag", tool_key or "rule", trace)
    raw_out = write_raw_processed(file_path, cleaned, tag)
    analysis = _run_with_fallback(cleaned, "analyze", tool_key or "rule", trace)
    finding_result = _run_with_fallback(cleaned, "findings", tool_key or "rule", trace)
    findings = finding_result.get("findings") or []
    if not findings:
        findings = [{
            "title": "待人工确认的问题",
            "statement": analysis.get("issue", "资料未提取出可直接确认的问题"),
            "evidence": cleaned[:300],
            "domain": tag.get("domain", "B3"),
            "attr": tag.get("attr", "P1"),
            "confidence": "待人工确认",
            "management_insight": analysis.get("one_sentence", "待人工提炼"),
            "reason": "模型未返回结构化问题，已保留为人工确认项。",
        }]
    # Carry the reviewed, concrete findings into the document-level draft so
    # the ledger's headline is not a generic placeholder.
    statements = [str(item.get("statement", "")).strip() for item in findings
                  if isinstance(item, dict) and item.get("statement")]
    if statements:
        analysis["issue"] = "；".join(statements)
    first_insight = next(
        (str(item.get("management_insight", "")).strip() for item in findings
         if isinstance(item, dict) and item.get("management_insight")),
        "",
    )
    if first_insight:
        analysis["one_sentence"] = first_insight
    analysis["findings"] = findings
    analysis["processing_trace"] = trace
    return {
        "ok": True,
        "chars": len(cleaned),
        "tag": tag,
        "raw_file": raw_out,
        "extraction_method": extraction_method,
        "ocr_quality": ocr_quality,
        "analysis": analysis,
        "findings": findings,
        # Keep both versions available to the reviewer.  The raw extraction
        # shows what the converter actually read; the cleaned text is what
        # the classifier used.
        "raw_text": raw,
        "text": cleaned,
        "processing_trace": trace,
    }


def process_file(file_path, tool_key=None):
    """兼容旧入口：预处理后生成待人工复核台账。"""
    result = prepare_file(file_path, tool_key)
    if not result.get("ok"):
        return result
    result["ledger_file"] = write_ledger(
        result["text"], result["tag"], file_path, result.get("analysis")
    )
    return result


def automate_file(file_path, tool_key=None):
    """资料到台账、方案、事件分析和记忆卡片的一键闭环。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED:
        raise ValueError(f"自动闭环暂不支持 {ext}（支持 {sorted(SUPPORTED)}）")
    raw = extract_text(file_path)
    if not raw or not raw.strip():
        return {"ok": False, "msg": "未能提取文字；扫描件需要OCR后再处理"}
    cleaned = clean_text(raw)
    trace = {
        "requested_tool": tool_key or "rule", "actual_tool": "", "fallback": False, "tasks": {},
    }
    tag = _run_with_fallback(cleaned, "tag", tool_key or "rule", trace)
    if not tag.get("domain") or not tag.get("attr"):
        tag = process_by_tool(cleaned, task="tag", tool_key="rule")
        tag["note"] = "AI打标无效，已自动回退规则版"
        trace["fallback"] = True
    return finalize_processed(file_path, {"text": cleaned, "tag": tag, "processing_trace": trace}, tool_key)


def automate_folder(folder, tool_key=None):
    """批量执行自动闭环；单个文件失败不影响其余文件。"""
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"文件夹不存在: {folder}")
    results = []
    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in SUPPORTED:
            try:
                results.append((fname, automate_file(fpath, tool_key)))
            except Exception as exc:
                results.append((fname, {"ok": False, "msg": str(exc)}))
    return results


def process_folder(folder, tool_key=None):
    """批量处理文件夹内所有支持的文本文件。"""
    results = []
    for fname in sorted(os.listdir(folder)):
        fpath = os.path.join(folder, fname)
        if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in SUPPORTED:
            try:
                r = process_file(fpath, tool_key)
                results.append((fname, r))
            except Exception as e:
                results.append((fname, {"ok": False, "msg": str(e)}))
    return results


def process_files(paths, tool_key=None):
    """Process an explicit user-selected list, preserving order and filenames."""
    results = []
    for fpath in paths:
        fname = os.path.basename(fpath)
        try:
            results.append((fname, process_file(fpath, tool_key)))
        except Exception as exc:
            results.append((fname, {"ok": False, "msg": str(exc)}))
    return results


def process_files_managed(paths, tool_key=None, manager=None, progress=None, task=None):
    """带统一任务编号、进度、暂停/取消和恢复能力的批量处理入口。"""
    import task_manager as task_mod
    manager = manager or task_mod.TaskManager(get_kb_path())
    names = [os.path.basename(path) for path in paths]
    task = task or manager.create("资料批量处理", names, {"tool": tool_key or "rule"})
    raw_results = task_mod.process_with_manager(
        manager, task["task_id"], list(paths),
        lambda path: process_file(path, tool_key),
        progress=progress,
    )
    results = []
    for index, (name, value) in enumerate(zip(names, raw_results)):
        if isinstance(value, Exception):
            results.append((name, {"ok": False, "msg": str(value)}))
        else:
            results.append((name, value))
    return task["task_id"], results, manager.get(task["task_id"])


def resume_managed_task(task_id, paths, tool_key=None, manager=None, progress=None):
    """从任务状态文件恢复未完成的批量任务。"""
    import task_manager as task_mod
    manager = manager or task_mod.TaskManager(get_kb_path())
    task = manager.get(task_id)
    if not task:
        raise KeyError(task_id)
    if task.get("status") == "cancelled":
        raise ValueError("已取消的任务不能直接恢复，请先创建重试任务")
    manager.resume(task_id)
    return process_files_managed(paths, tool_key, manager, progress, task)


def automate_files(paths, tool_key=None):
    """Run the automatic workflow for an explicit user-selected list."""
    results = []
    for fpath in paths:
        fname = os.path.basename(fpath)
        try:
            results.append((fname, automate_file(fpath, tool_key)))
        except Exception as exc:
            results.append((fname, {"ok": False, "msg": str(exc)}))
    return results


# ---------- 提炼管理内涵（下一步） ----------
def extract_insight(text, tool_key=None, ledger_file=None):
    """提炼管理内涵（问题定性/根因/缺失机制/一句话内涵）。
    若提供 ledger_file，则把提炼结果追加写入台账文件。
    返回提炼结果 dict。"""
    from ai_tools import process_by_tool
    result = process_by_tool(text, task="extract", tool_key=tool_key)
    content = result.get("content") or result.get("one_sentence", "")
    if content and not result.get("content"):
        result["content"] = content
    if ledger_file and content and os.path.isfile(ledger_file):
        try:
            with open(ledger_file, "a", encoding="utf-8") as f:
                f.write("\n\n## 管理内涵提炼（AI工具生成，待人工复核）\n\n" + content + "\n")
        except Exception as e:
            result["append_error"] = str(e)
    return result
