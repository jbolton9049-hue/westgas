# -*- coding: utf-8 -*-
"""统一任务状态、编号、暂停/取消、重试和崩溃恢复。

任务状态文件只保存任务元数据，不保存业务正文；删除后可以由业务重新建立。
"""

import datetime
import json
import os
import threading
import uuid


STATUSES = {
    "queued", "running", "paused", "cancelled", "failed", "completed", "retrying",
}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class TaskManager:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.path = os.path.join(self.root, "09_周期汇总", "任务状态.json")
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError):
            data = {"version": 1, "tasks": {}}
        data.setdefault("version", 1)
        data.setdefault("tasks", {})
        return data

    def _write(self, data):
        temporary = self.path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def create(self, name, items=None, metadata=None):
        now = _now()
        task_id = "TASK-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f") + "-" + uuid.uuid4().hex[:6].upper()
        item_list = list(items or [])
        task = {
            "task_id": task_id, "name": str(name), "status": "queued",
            "items": item_list, "total": len(item_list), "completed": 0,
            "failed": 0, "current": "", "error": "", "created_at": now,
            "updated_at": now, "metadata": dict(metadata or {}), "attempt": 1,
        }
        with self._lock:
            data = self._read()
            data["tasks"][task_id] = task
            self._write(data)
        return dict(task)

    def get(self, task_id):
        with self._lock:
            task = self._read()["tasks"].get(task_id)
            return dict(task) if task else None

    def list(self, statuses=None):
        wanted = set(statuses or [])
        with self._lock:
            values = list(self._read()["tasks"].values())
        return [dict(item) for item in values if not wanted or item.get("status") in wanted]

    def update(self, task_id, **changes):
        status = changes.get("status")
        if status and status not in STATUSES:
            raise ValueError(f"无效任务状态：{status}")
        with self._lock:
            data = self._read()
            if task_id not in data["tasks"]:
                raise KeyError(task_id)
            task = data["tasks"][task_id]
            task.update(changes)
            task["updated_at"] = _now()
            self._write(data)
            return dict(task)

    def pause(self, task_id):
        return self.update(task_id, status="paused")

    def resume(self, task_id):
        return self.update(task_id, status="queued", error="")

    def cancel(self, task_id):
        return self.update(task_id, status="cancelled")

    def fail(self, task_id, error):
        return self.update(task_id, status="failed", error=str(error), failed=1)

    def complete(self, task_id):
        task = self.get(task_id) or {}
        return self.update(task_id, status="completed", completed=task.get("total", task.get("completed", 0)), current="")

    def retry(self, task_id):
        task = self.get(task_id)
        if not task:
            raise KeyError(task_id)
        return self.update(task_id, status="queued", error="", attempt=int(task.get("attempt", 1)) + 1)

    def is_cancelled(self, task_id):
        task = self.get(task_id)
        return bool(task and task.get("status") == "cancelled")

    def recover_unfinished(self):
        return self.list({"queued", "running", "paused", "retrying", "failed"})


class TaskCenter:
    """统一任务中心外观，所有窗口通过同一管理器读取和操作任务。"""
    def __init__(self, manager):
        self.manager = manager

    def snapshot(self):
        return sorted(self.manager.list(), key=lambda item: item.get("updated_at", ""), reverse=True)

    def get(self, task_id):
        return self.manager.get(task_id)

    def pause(self, task_id):
        return self.manager.pause(task_id)

    def resume(self, task_id):
        return self.manager.resume(task_id)

    def cancel(self, task_id):
        return self.manager.cancel(task_id)

    def retry(self, task_id):
        return self.manager.retry(task_id)

    def recover(self):
        return self.manager.recover_unfinished()


def process_with_manager(manager, task_id, items, worker, progress=None):
    """执行批量 worker；支持取消/暂停检查并保留每项进度。"""
    results = []
    task = manager.get(task_id) or {}
    start = int(task.get("completed", 0))
    manager.update(task_id, status="running")
    for index, item in enumerate(items[start:], start + 1):
        state = manager.get(task_id)
        if not state or state.get("status") == "cancelled":
            break
        while state and state.get("status") == "paused":
            import time
            time.sleep(0.1)
            state = manager.get(task_id)
        if not state or state.get("status") == "cancelled":
            break
        try:
            result = worker(item)
            results.append(result)
            manager.update(task_id, completed=index, current=str(item), failed=0)
            if progress:
                progress(index, len(items), item, result)
        except Exception as exc:
            prior = manager.get(task_id) or {}
            # Keep the failed item as the resume point so retry() retries it
            # instead of silently skipping it after a crash or transient error.
            manager.update(task_id, completed=max(index - 1, 0), current=str(item),
                           status="failed", error=str(exc), failed=int(prior.get("failed", 0)) + 1)
            results.append(exc)
            if progress:
                progress(index, len(items), item, {"ok": False, "msg": str(exc)})
    state = manager.get(task_id)
    if state and state.get("status") == "running":
        manager.complete(task_id)
    return results
