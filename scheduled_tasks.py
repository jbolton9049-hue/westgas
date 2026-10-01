# -*- coding: utf-8 -*-
"""可选安装 Windows 当前用户的知识治理计划任务。"""

import os
import subprocess
import sys


TASK_PREFIX = "天然气管理知识库"


def _job_command(kind):
    if getattr(sys, "frozen", False):
        return subprocess.list2cmdline([sys.executable, "--job", kind])
    base_dir = os.path.dirname(os.path.abspath(__file__))
    python_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(python_exe):
        raise FileNotFoundError("找不到 pythonw.exe；请使用打包版程序安装后台任务")
    script = os.path.join(base_dir, "knowledge_job.py")
    return subprocess.list2cmdline([python_exe, script, kind])


def _run(arguments):
    result = subprocess.run(
        ["schtasks.exe", *arguments], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "schtasks 执行失败").strip())
    return result.stdout.strip()


def install_tasks():
    """创建当前用户的每日、每周和每月任务。"""
    if os.name != "nt":
        raise OSError("周期计划任务仅支持 Windows")
    results = []
    definitions = [
        ("每日增量整理", ["/SC", "DAILY", "/ST", "06:00"], "daily"),
        ("每周知识周报", ["/SC", "WEEKLY", "/D", "MON", "/ST", "07:00"], "weekly"),
        ("每月知识月报", ["/SC", "MONTHLY", "/D", "1", "/ST", "07:30"], "monthly"),
    ]
    for label, schedule, kind in definitions:
        name = f"{TASK_PREFIX} - {label}"
        action = _job_command(kind)
        results.append(_run([
            "/Create", "/F", "/TN", name, *schedule,
            "/TR", action,
        ]))
    return results


def list_tasks():
    """只读查询本系统计划任务状态。"""
    if os.name != "nt":
        raise OSError("周期计划任务仅支持 Windows")
    tasks = []
    for label in ["每日增量整理", "每周知识周报", "每月知识月报"]:
        name = f"{TASK_PREFIX} - {label}"
        result = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", name, "/FO", "LIST"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode:
            tasks.append({"name": label, "installed": False})
        else:
            tasks.append({"name": label, "installed": True, "details": result.stdout.strip()})
    return tasks


def remove_tasks():
    """删除本系统创建的三个计划任务。"""
    if os.name != "nt":
        raise OSError("周期计划任务仅支持 Windows")
    results = []
    for label in ["每日增量整理", "每周知识周报", "每月知识月报"]:
        name = f"{TASK_PREFIX} - {label}"
        query = subprocess.run(
            ["schtasks.exe", "/Query", "/TN", name], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        if query.returncode:
            continue
        result = subprocess.run(
            ["schtasks.exe", "/Delete", "/F", "/TN", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or f"删除任务失败: {label}").strip())
        results.append(result.stdout.strip())
    return results
