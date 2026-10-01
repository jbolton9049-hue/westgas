# -*- coding: utf-8 -*-
"""训练模块：从管理要点生成记忆卡片、生成每日打卡、遗忘曲线复习提醒。"""

import os
import re
import datetime
import hashlib
import json

from ai_tools import get_kb_path

REVIEW_DAYS = [0, 1, 2, 4, 7, 15]
REVIEW_INTERVALS = {0: 0, 1: 1, 2: 2, 3: 4, 4: 7, 5: 15}
DAILY_TRAINING_LIMIT = 10
MASTERY_THRESHOLD = 80
WEAK_THRESHOLD = 60


def _cards_dir():
    return os.path.join(get_kb_path(), "04_记忆卡片")


def _pending_dir():
    return os.path.join(_cards_dir(), "待审核")


def _rejected_dir():
    return os.path.join(_cards_dir(), "已拒绝")


def _training_dir():
    return os.path.join(get_kb_path(), "05_训练记录")


def _state_path():
    return os.path.join(_training_dir(), "训练状态.json")


def _load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state = {}
    state.setdefault("cards", {})
    state.setdefault("settings", {"daily_limit": DAILY_TRAINING_LIMIT,
                                   "mastery_threshold": MASTERY_THRESHOLD})
    return state


def _save_state(state):
    os.makedirs(_training_dir(), exist_ok=True)
    temp = _state_path() + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(temp, _state_path())


def _card_path(card_file):
    return card_file if os.path.isabs(str(card_file)) else os.path.join(_cards_dir(), str(card_file))


def _card_name(card_file):
    return os.path.basename(_card_path(card_file))


def _safe_name(value):
    return re.sub(r'[\\/:*?"<>|]', "_", str(value or ""))


def _section(text, labels):
    """Return the body of the first Markdown heading containing one of labels."""
    labels = tuple(labels) if isinstance(labels, (tuple, list)) else (labels,)
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("#") and any(label in line for label in labels):
            start = index + 1
            break
    if start is None:
        return ""
    body = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        body.append(line)
    return "\n".join(body).strip(" \n")


def _first_paragraph(text):
    chunks = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n\s*\n", text)]
    return next((item for item in chunks if item and not item.startswith(("#", ">", "- **"))), "")


def list_cards():
    """列出已有记忆卡片。"""
    cards_dir = os.path.join(get_kb_path(), "04_记忆卡片")
    if not os.path.isdir(cards_dir):
        return []
    return sorted([f for f in os.listdir(cards_dir) if f.endswith(".md")])


def generate_card(title, problem, root_cause, action, expression, source_id="",
                  card_type="问答卡", source_path="", topic="", knowledge_hash="",
                  pending=False):
    """生成记忆卡片文件，保留人工创建调用的兼容性。"""
    cards_dir = _pending_dir() if pending else _cards_dir()
    os.makedirs(cards_dir, exist_ok=True)
    safe = _safe_name(title)
    today = datetime.date.today().isoformat()
    suffix = f"_{source_id}" if source_id else ""
    out_file = os.path.join(cards_dir, f"{today}_{safe}{suffix}.md")
    content = (
        f"# 记忆卡片：{title}\n\n"
        f"- **创建日期**：{today}\n"
        f"- **来源记录**：{source_id or '手工创建'}\n\n"
        f"- **卡片类型**：{card_type}\n"
        f"- **来源文件**：{source_path or '手工创建'}\n"
        f"- **知识主题**：{topic or '未分类'}\n"
        f"- **知识哈希**：{knowledge_hash}\n\n"
        f"- **审核状态**：{'待审核' if pending else '已通过'}\n\n"
        f"**问题情景**：{problem}\n\n"
        f"**根因**：{root_cause}\n\n"
        f"**解决动作**：{action}\n\n"
        f"**一句话表达**：{expression}\n\n"
        f"---\n\n## 我的复述\n\n"
        f"- [ ] 第一次回忆（今天）\n"
        f"- [ ] 第二次复习（第1天）\n"
        f"- [ ] 第三次复习（第2天）\n"
        f"- [ ] 第四次复习（第4天）\n"
        f"- [ ] 第五次复习（第7天）\n"
        f"- [ ] 第六次复习（第15天）\n\n"
        f"**掌握程度**：生疏 / 熟悉 / 掌握\n"
        f"**掌握度**：0%\n"
        f"**下次复习**：{today}\n"
    )
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    return out_file


def _knowledge_card_candidate(path, text, topic=""):
    if topic and topic.casefold() not in (text + " " + os.path.basename(path)).casefold():
        return None
    title_match = re.search(r"^#\s+(.+)$", text, re.M)
    title = title_match.group(1).strip() if title_match else os.path.splitext(os.path.basename(path))[0]
    title = re.sub(r"^(一页纸解决思路：|台账：|记忆卡片：)", "", title).strip()
    judgement = _section(text, ("管理判断", "问题定性", "问题判断"))
    expression = _section(text, ("总经理一句话", "一句话表达", "管理内涵提炼", "管理内涵"))
    root = _section(text, ("根因", "共同根因"))
    action = _section(text, ("五维措施", "建议措施", "解决动作"))
    answer = judgement or expression or _first_paragraph(text) or text[:500]
    question = f"关于“{title}”，最需要记住的管理判断是什么？"
    if "依据" in os.path.basename(os.path.dirname(path)) or "法规" in text[:200]:
        question = f"“{title}”要求我们遵守的关键要点是什么？"
    if not root:
        root = "请结合原文补充根因或风险背景。"
    if not action:
        action = "请结合原文补充可执行的解决动作。"
    expression_text = "；".join(part for part in (judgement, expression) if part)
    if re.search(r"是否|正确|错误|不得|必须", answer):
        card_type = "判断卡"
    elif re.search(r"(?:^|\n)\s*\d+[.)、]", action):
        card_type = "步骤卡"
    else:
        card_type = "问答卡"
    return {
        "title": title[:80], "problem": question, "root": root[:800],
        "action": action[:1000], "expression": (expression_text or answer)[:500],
        "source": path, "card_type": card_type,
    }


def generate_cards_from_knowledge(topic="", limit=50):
    """从知识库管理文档生成卡片，并按内容哈希增量去重。"""
    candidates = []
    root = get_kb_path()
    for folder in ("03_管理要点", "02_分类台账", "07_管理依据库", "08_事件分析"):
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for current, _, files in os.walk(base):
            for name in sorted(files):
                if not name.lower().endswith(".md"):
                    continue
                path = os.path.join(current, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                item = _knowledge_card_candidate(path, text, topic)
                if item:
                    candidates.append((item, text))
    state = _load_state()
    existing_hashes = set()
    for folder in (_cards_dir(), _pending_dir(), _rejected_dir()):
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.lower().endswith(".md"):
                continue
            try:
                with open(os.path.join(folder, name), "r", encoding="utf-8", errors="ignore") as f:
                    match = re.search(r"\*\*知识哈希\*\*：([^\n]*)", f.read())
                    if match and match.group(1).strip():
                        existing_hashes.add(match.group(1).strip())
            except OSError:
                continue
    generated = []
    for item, text in candidates:
        if len(generated) >= max(0, int(limit)):
            break
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        if digest in existing_hashes:
            continue
        source_id = f"kb_{digest}"
        file_path = generate_card(
            item["title"], item["problem"], item["root"], item["action"],
            item["expression"], source_id=source_id, card_type=item["card_type"],
            source_path=item["source"], topic=topic or item["title"], knowledge_hash=digest,
            pending=True,
        )
        existing_hashes.add(digest)
        generated.append({"file": file_path, "title": item["title"],
                          "source": item["source"], "card_type": item["card_type"]})
    return generated


def list_pending_cards():
    """List automatically generated cards awaiting explicit human review."""
    folder = _pending_dir()
    if not os.path.isdir(folder):
        return []
    return [{"file": os.path.join(folder, name), "name": name, "status": "pending"}
            for name in sorted(os.listdir(folder)) if name.lower().endswith(".md")]


def _validated_draft_path(card_file):
    path = os.path.abspath(str(card_file))
    if os.path.dirname(path) != os.path.abspath(_pending_dir()) or not os.path.isfile(path):
        raise ValueError("只能审核待审核目录中的卡片")
    return path


def _unique_path(folder, name):
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}_{index}{ext}")
        index += 1
    return candidate


def approve_card(card_file, edits=None):
    """Approve a draft, optionally editing its question and answer fields."""
    source = _validated_draft_path(card_file)
    with open(source, "r", encoding="utf-8") as f:
        content = f.read()
    field_labels = {"title": None, "problem": "问题情景", "root_cause": "根因",
                    "action": "解决动作", "expression": "一句话表达"}
    for key, value in (edits or {}).items():
        if key not in field_labels:
            raise ValueError(f"不支持编辑字段：{key}")
        value = str(value).strip().replace("\r", " ").replace("\n", " ")
        if key == "title":
            content = re.sub(r"^# 记忆卡片：.*$", f"# 记忆卡片：{value}", content, count=1, flags=re.M)
        else:
            label = field_labels[key]
            content = re.sub(rf"^\*\*{label}\*\*：.*$", f"**{label}**：{value}", content, count=1, flags=re.M)
    if not re.search(r"\*\*问题情景\*\*：\S", content) or not re.search(r"\*\*一句话表达\*\*：\S", content):
        raise ValueError("问题情景和一句话表达不能为空")
    content = content.replace("- **审核状态**：待审核", "- **审核状态**：已通过")
    destination = _unique_path(_cards_dir(), os.path.basename(source))
    with open(destination, "w", encoding="utf-8") as f:
        f.write(content)
    os.remove(source)
    return destination


def reject_card(card_file):
    """Archive a rejected draft; its source hash prevents repeat proposals."""
    source = _validated_draft_path(card_file)
    destination = _unique_path(_rejected_dir(), os.path.basename(source))
    os.replace(source, destination)
    return destination


def _default_progress(card_name):
    created = _card_created_date(card_name).isoformat()
    return {"level": 0, "mastery": 0, "reviews": [], "due": created,
            "streak": 0, "mastered": False, "last_review": ""}


def card_progress(card_file):
    """Return persisted progress for one card without changing it."""
    state = _load_state()
    name = _card_name(card_file)
    return dict(state["cards"].get(name, _default_progress(name)))


def record_review(card_file, rating):
    """Record remember/fuzzy/forget and update interval, mastery and due date."""
    name = _card_name(card_file)
    path = _card_path(card_file)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    aliases = {"remember": "remember", "记得": "remember", "熟悉": "remember", 2: "remember", 3: "remember", 4: "remember", 5: "remember",
               "fuzzy": "fuzzy", "模糊": "fuzzy", "熟悉但不稳": "fuzzy", 1: "fuzzy",
               "forget": "forget", "忘记": "forget", "生疏": "forget", 0: "forget"}
    key = aliases.get(rating, aliases.get(str(rating).casefold()))
    if not key:
        raise ValueError("rating 必须是 remember、fuzzy 或 forget")
    state = _load_state()
    progress = state["cards"].setdefault(name, _default_progress(name))
    today = datetime.date.today()
    if key == "remember":
        progress["level"] = min(5, int(progress.get("level", 0)) + 1)
        progress["streak"] = int(progress.get("streak", 0)) + 1
        weight = 1.0
    elif key == "fuzzy":
        progress["level"] = max(0, int(progress.get("level", 0)))
        progress["streak"] = 0
        weight = 0.5
    else:
        progress["level"] = max(0, int(progress.get("level", 0)) - 1)
        progress["streak"] = 0
        weight = 0.0
    reviews = list(progress.get("reviews", []))
    reviews.append({"date": today.isoformat(), "rating": key, "weight": weight})
    progress["reviews"] = reviews[-10:]
    recent = progress["reviews"]
    recent_rate = sum(float(item.get("weight", 0)) for item in recent) / max(1, len(recent))
    progress["mastery"] = round(recent_rate * 70 + (int(progress["level"]) / 5) * 30)
    threshold = int(state.get("settings", {}).get("mastery_threshold", MASTERY_THRESHOLD))
    progress["mastered"] = progress["mastery"] >= threshold
    interval = REVIEW_INTERVALS.get(int(progress["level"]), 30)
    if key == "fuzzy":
        interval = min(interval or 1, 2)
    elif key == "forget":
        interval = 0
    if progress["mastered"] and int(progress.get("streak", 0)) >= 6:
        interval = 30
    progress["due"] = (today + datetime.timedelta(days=interval)).isoformat()
    progress["last_review"] = today.isoformat()
    state["cards"][name] = progress
    _save_state(state)
    return dict(progress)


def daily_checkin(card_file=None):
    """生成今日训练打卡文件。"""
    train_dir = os.path.join(get_kb_path(), "05_训练记录")
    os.makedirs(train_dir, exist_ok=True)
    today = datetime.date.today().isoformat()
    card_name = os.path.basename(card_file) if card_file else "（填写今日卡片）"
    safe_card = re.sub(r'[\\/:*?"<>|]', "_", os.path.splitext(card_name)[0])[:80]
    out_file = os.path.join(train_dir, f"{today}_{safe_card}_训练打卡.md")
    content = (
        f"# 训练记录：{today}\n\n"
        f"> 今日卡片：{card_name}\n\n---\n\n"
        f"**① 主动回忆**（先别看卡片）：\n\n"
        f"**② 学习今日卡片**：\n\n"
        f"**③ 费曼复述**（用自己的话讲一遍）：\n\n"
        f"**④ 一句话表达**（写成能对下属/上级说的话）：\n\n---\n\n"
        f"## 掌握情况\n\n| 卡片 | 掌握程度 | 下次复习 |\n|---|---|---|\n"
        f"| {card_name} | 生疏/熟悉/掌握 | 第1天 |\n\n---\n\n## 自评\n\n"
        f"- 今天最有收获的点：\n- 还没想透的点：\n"
    )
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    return out_file


def _card_created_date(card_name):
    try:
        return datetime.date.fromisoformat(card_name[:10])
    except ValueError:
        return datetime.date.today()


def _completed_count(card_name):
    train_dir = os.path.join(get_kb_path(), "05_训练记录")
    if not os.path.isdir(train_dir):
        return 0
    stem = os.path.splitext(card_name)[0]
    count = 0
    for fname in os.listdir(train_dir):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(train_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                if stem in f.read():
                    count += 1
        except OSError:
            continue
    return count


def due_cards(on_date=None, limit=None, weak_only=False):
    """按持久化间隔计算到期卡片，兼容旧版打卡记录。"""
    on_date = on_date or datetime.date.today()
    state = _load_state()
    due = []
    for card in list_cards():
        progress = state["cards"].get(card)
        if progress is not None:
            if weak_only and int(progress.get("mastery", 0)) >= WEAK_THRESHOLD:
                continue
            target = progress.get("due", on_date.isoformat())
            if target <= on_date.isoformat():
                due.append({"file": card, "due": target,
                            "round": len(progress.get("reviews", [])) + 1,
                            "mastery": int(progress.get("mastery", 0)),
                            "level": int(progress.get("level", 0))})
            continue
        completed = _completed_count(card)
        if completed >= len(REVIEW_DAYS):
            continue
        created = _card_created_date(card)
        target = created + datetime.timedelta(days=REVIEW_DAYS[completed])
        if target <= on_date:
            due.append({"file": card, "due": target.isoformat(), "round": completed + 1,
                        "mastery": 0, "level": completed})
    due.sort(key=lambda item: (item["due"], item["file"]))
    return due[:limit] if limit else due


def weak_cards(limit=None):
    """返回掌握度低于 60% 的薄弱卡片。"""
    state = _load_state()
    rows = []
    for card in list_cards():
        progress = state["cards"].get(card, _default_progress(card))
        if int(progress.get("mastery", 0)) < WEAK_THRESHOLD:
            rows.append({"file": card, "mastery": int(progress.get("mastery", 0)),
                         "level": int(progress.get("level", 0))})
    rows.sort(key=lambda item: (item["mastery"], item["file"]))
    return rows[:limit] if limit else rows


def training_dashboard(on_date=None):
    """返回今日训练看板数据。"""
    on_date = on_date or datetime.date.today()
    state = _load_state()
    mastery_threshold = int(state.get("settings", {}).get("mastery_threshold", MASTERY_THRESHOLD))
    cards = list_cards()
    progress = [state["cards"].get(card, _default_progress(card)) for card in cards]
    mastered = sum(1 for item in progress if int(item.get("mastery", 0)) >= mastery_threshold)
    review_dates = {review.get("date") for item in progress for review in item.get("reviews", [])
                    if review.get("date")}
    streak = 0
    cursor = on_date
    while cursor.isoformat() in review_dates:
        streak += 1
        cursor -= datetime.timedelta(days=1)
    return {
        "total": len(cards), "mastered": mastered,
        "mastery_rate": round(mastered / len(cards) * 100, 1) if cards else 0,
        "today_due": len(due_cards(on_date)), "weak": len(weak_cards()),
        "streak_days": streak, "daily_limit": int(state.get("settings", {}).get("daily_limit", DAILY_TRAINING_LIMIT)),
        "mastery_threshold": mastery_threshold,
    }


def update_training_settings(daily_limit=None, mastery_threshold=None,
                             reminder_time=None, reminder_frequency=None):
    """保存训练参数，供界面或后续定时任务使用。"""
    state = _load_state()
    settings = state.setdefault("settings", {})
    if daily_limit is not None:
        settings["daily_limit"] = max(1, min(200, int(daily_limit)))
    if mastery_threshold is not None:
        settings["mastery_threshold"] = max(1, min(100, int(mastery_threshold)))
    if reminder_time is not None:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(reminder_time)):
            raise ValueError("reminder_time 必须是 HH:MM")
        settings["reminder_time"] = str(reminder_time)
    if reminder_frequency is not None:
        if reminder_frequency not in {"daily", "only_due", "off"}:
            raise ValueError("reminder_frequency 必须是 daily、only_due 或 off")
        settings["reminder_frequency"] = reminder_frequency
    if "reminder_time" not in settings:
        settings["reminder_time"] = "08:30"
    if "reminder_frequency" not in settings:
        settings["reminder_frequency"] = "only_due"
    if "last_reminder_date" not in settings:
        settings["last_reminder_date"] = ""
    return_settings = dict(settings)
    _save_state(state)
    return return_settings


def get_training_settings():
    settings = dict(_load_state().get("settings", {}))
    settings.setdefault("daily_limit", DAILY_TRAINING_LIMIT)
    settings.setdefault("mastery_threshold", MASTERY_THRESHOLD)
    settings.setdefault("reminder_time", "08:30")
    settings.setdefault("reminder_frequency", "only_due")
    settings.setdefault("last_reminder_date", "")
    return settings


def should_show_reminder(now=None):
    """判断当前时间是否到达用户设置的提醒时间且今天尚未提醒。"""
    now = now or datetime.datetime.now()
    settings = get_training_settings()
    if settings.get("reminder_frequency") == "off":
        return False
    if settings.get("reminder_frequency") == "only_due" and not due_cards(on_date=now.date(), limit=1):
        return False
    try:
        hour, minute = (int(item) for item in str(settings.get("reminder_time", "08:30")).split(":", 1))
    except (ValueError, TypeError):
        hour, minute = 8, 30
    return (now.hour, now.minute) >= (hour, minute) and settings.get("last_reminder_date") != now.date().isoformat()


def mark_reminder_shown(on_date=None):
    state = _load_state()
    state.setdefault("settings", {})["last_reminder_date"] = (on_date or datetime.date.today()).isoformat()
    _save_state(state)
    return state["settings"]["last_reminder_date"]


def review_reminder():
    """基于打卡文件，给出今天该复习哪张卡的提醒。"""
    due = due_cards(limit=DAILY_TRAINING_LIMIT)
    if not list_cards():
        return "暂无记忆卡片。请先运行自动闭环或手工生成卡片。"
    if not due:
        lines = ["今天没有到期卡片。"]
    else:
        lines = [f"今日到期 {len(due)} 张："]
        for item in due:
            lines.append(f"  - 第{item['round']}次：{item['file']}（掌握度 {item.get('mastery', 0)}%，应复习 {item['due']}）")
    weak = weak_cards(limit=5)
    if weak:
        lines.append(f"\n薄弱知识点 {len(weak)} 张（掌握度低于 {WEAK_THRESHOLD}%）：")
        lines.extend(f"  - {item['file']}（{item['mastery']}%）" for item in weak)
    return "\n".join(lines)
