# -*- coding: utf-8 -*-
"""知识库备份、列表和安全恢复。"""

import datetime
import os
import shutil
import zipfile


def backup_dir(root):
    path = os.path.join(root, "00_系统说明", "备份")
    os.makedirs(path, exist_ok=True)
    return path


def create_backup(root, target=None):
    """Create a zip snapshot without including the backup directory itself."""
    target = target or os.path.join(
        backup_dir(root), f"知识库备份_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    root_abs = os.path.abspath(root)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for current, dirs, files in os.walk(root_abs):
            dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(current, d)) != os.path.abspath(backup_dir(root))]
            for name in files:
                path = os.path.join(current, name)
                if os.path.abspath(path) == os.path.abspath(target):
                    continue
                archive.write(path, os.path.relpath(path, root_abs))
    return target


def list_backups(root):
    folder = backup_dir(root)
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder), reverse=True) if name.lower().endswith(".zip")]


def restore_backup(root, archive_path):
    """Restore selected files after creating a safety backup first."""
    if not os.path.isfile(archive_path):
        raise FileNotFoundError(archive_path)
    safety_target = os.path.join(
        backup_dir(root),
        f"恢复前安全备份_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.zip",
    )
    safety = create_backup(root, safety_target)
    root_abs = os.path.abspath(root)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = os.path.abspath(os.path.join(root_abs, member.filename))
            if os.path.commonpath([root_abs, destination]) != root_abs:
                raise ValueError("备份包含越界路径，已停止恢复")
        archive.extractall(root_abs)
    return safety
