# -*- coding: utf-8 -*-
"""系统迁移包：迁移知识库和可恢复运行数据，不迁移 API 密钥。"""

import copy
import datetime
import hashlib
import json
import os
import zipfile

import backup


FORMAT_VERSION = 1


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _safe_member(name):
    normalized = name.replace("\\", "/")
    return normalized and not normalized.startswith("/") and ".." not in normalized.split("/")


def _sanitized_config(config):
    value = copy.deepcopy(config or {})
    value["knowledge_base"] = "__TARGET_KNOWLEDGE_BASE__"
    value.setdefault("default_tool", "rule")
    value.setdefault("ai_tools", {})
    for tool in value["ai_tools"].values():
        if isinstance(tool, dict):
            tool["api_key"] = ""
            # A destination without a re-entered key must not claim an AI tool is usable.
            tool["enabled"] = False
    return value


def _iter_files(root, secret_root=None):
    root = os.path.abspath(root)
    secret_root = os.path.abspath(secret_root) if secret_root else None
    if not os.path.isdir(root):
        return
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in {"备份", ".secrets", "__pycache__"}]
        for name in names:
            path = os.path.join(current, name)
            if secret_root and os.path.commonpath([secret_root, os.path.abspath(path)]) == secret_root:
                continue
            if name.endswith((".secret", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
                continue
            yield path, os.path.relpath(path, root).replace(os.sep, "/")


def create_package(source_root, target, config=None, secret_root=None):
    """创建迁移包。知识正文、任务状态、训练记录和规则反馈会被纳入。"""
    source_root = os.path.abspath(source_root)
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    entries = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        sanitized = _sanitized_config(config)
        config_bytes = json.dumps(sanitized, ensure_ascii=False, indent=2).encode("utf-8")
        archive.writestr("config.sanitized.json", config_bytes)
        entries.append({"path": "config.sanitized.json", "sha256": _sha256_bytes(config_bytes), "size": len(config_bytes)})
        for path, relative in _iter_files(source_root, secret_root):
            with open(path, "rb") as stream:
                data = stream.read()
            member = "knowledge_base/" + relative
            archive.writestr(member, data)
            entries.append({"path": member, "sha256": _sha256_bytes(data), "size": len(data)})
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_root_name": os.path.basename(source_root),
            "contains_secrets": False,
            "entries": entries,
        }
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return os.path.abspath(target)


def inspect_package(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with zipfile.ZipFile(path) as archive:
        if "manifest.json" not in archive.namelist():
            raise ValueError("迁移包缺少 manifest.json")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        for member in archive.infolist():
            if not _safe_member(member.filename):
                raise ValueError("迁移包包含越界路径")
        manifest["contains_secrets"] = bool(manifest.get("contains_secrets")) or any(
            name.endswith(".secret") or ".secrets/" in name for name in archive.namelist()
        )
        manifest["file_count"] = sum(1 for name in archive.namelist() if name.startswith("knowledge_base/"))
        return manifest


def restore_package(path, destination_root):
    """校验并恢复迁移包；恢复前自动生成安全备份。"""
    manifest = inspect_package(path)
    if manifest.get("contains_secrets"):
        raise ValueError("迁移包包含密钥文件，出于安全原因拒绝恢复")
    destination_root = os.path.abspath(destination_root)
    os.makedirs(destination_root, exist_ok=True)
    safety = backup.create_backup(destination_root)
    expected = {item["path"]: item["sha256"] for item in manifest.get("entries", [])}
    restored = 0
    sanitized_config = {}
    with zipfile.ZipFile(path) as archive:
        config_data = archive.read("config.sanitized.json") if "config.sanitized.json" in archive.namelist() else b"{}"
        sanitized_config = json.loads(config_data.decode("utf-8"))
        if expected.get("config.sanitized.json") != _sha256_bytes(config_data):
            raise ValueError("迁移包配置校验失败")
        for member in archive.infolist():
            if not member.filename.startswith("knowledge_base/") or member.is_dir():
                continue
            if not _safe_member(member.filename):
                raise ValueError("迁移包包含越界路径")
            data = archive.read(member)
            if expected.get(member.filename) != _sha256_bytes(data):
                raise ValueError(f"文件校验失败：{member.filename}")
            relative = member.filename[len("knowledge_base/"):]
            target = os.path.abspath(os.path.join(destination_root, relative))
            if os.path.commonpath([destination_root, target]) != destination_root:
                raise ValueError("迁移目标路径越界")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            temporary = target + ".tmp"
            with open(temporary, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            restored += 1
    return {"safety_backup": safety, "restored_files": restored, "config": sanitized_config, "manifest": manifest}
