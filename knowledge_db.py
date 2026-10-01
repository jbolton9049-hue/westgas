# -*- coding: utf-8 -*-
"""SQLite 加速索引层。

Markdown 仍然是知识正文和 Obsidian 的主数据源；本模块只保存可重建的
索引和检索数据，删除数据库不会删除知识正文。
"""

import datetime
import os
import re
import sqlite3
from contextlib import contextmanager


def db_path(root):
    return os.path.join(root, "00_系统说明", "知识库索引.sqlite3")


@contextmanager
def connect(root):
    os.makedirs(os.path.dirname(db_path(root)), exist_ok=True)
    conn = sqlite3.connect(db_path(root), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init(conn)
    try:
        yield conn
    finally:
        conn.close()


def init(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source_id TEXT,
            sha256 TEXT,
            normalized_sha256 TEXT,
            size INTEGER DEFAULT 0,
            mtime TEXT,
            status TEXT,
            review_status TEXT,
            imported_at TEXT,
            last_seen_at TEXT,
            removed_at TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            folder TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS db_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    # The FTS table is a rebuildable acceleration structure, not source data.
    # Keep it standalone so SQLite versions on older Windows installations do
    # not produce an external-content rowid mismatch.
    marker = conn.execute("SELECT value FROM db_meta WHERE key='fts_mode'").fetchone()
    if not marker or marker[0] != "standalone-v1":
        conn.execute("DROP TABLE IF EXISTS documents_fts")
        conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(name, content)")
        conn.execute("INSERT OR REPLACE INTO db_meta(key,value) VALUES('fts_mode','standalone-v1')")
    conn.commit()


def sync_governance_index(root, index):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with connect(root) as conn:
        for path, item in (index.get("files") or {}).items():
            conn.execute(
                """INSERT INTO files
                (path,name,source_id,sha256,normalized_sha256,size,mtime,status,
                 review_status,imported_at,last_seen_at,removed_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                name=excluded.name, source_id=excluded.source_id, sha256=excluded.sha256,
                normalized_sha256=excluded.normalized_sha256, size=excluded.size,
                mtime=excluded.mtime, status=excluded.status, review_status=excluded.review_status,
                imported_at=excluded.imported_at, last_seen_at=excluded.last_seen_at,
                removed_at=excluded.removed_at, updated_at=excluded.updated_at""",
                (path, item.get("name", os.path.basename(path)), item.get("source_id"),
                 item.get("hash"), item.get("normalized_hash"), item.get("size", 0),
                 item.get("mtime"), item.get("status"), item.get("review_status"),
                 item.get("imported_at"), item.get("last_seen_at"), item.get("removed_at"), now),
            )
        conn.commit()


def sync_markdown(root, folders):
    """Refresh searchable Markdown records from the vault."""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    seen = set()
    with connect(root) as conn:
        for folder in folders:
            base = os.path.join(root, folder)
            if not os.path.isdir(base):
                continue
            for current, _, names in os.walk(base):
                for name in names:
                    if not name.lower().endswith(".md"):
                        continue
                    path = os.path.abspath(os.path.join(current, name))
                    try:
                        with open(path, "r", encoding="utf-8", errors="ignore") as stream:
                            content = stream.read()
                    except OSError:
                        continue
                    seen.add(path)
                    conn.execute(
                        "INSERT INTO documents(path,name,folder,content,updated_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET name=excluded.name, folder=excluded.folder, "
                        "content=excluded.content, updated_at=excluded.updated_at",
                        (path, name, folder, content, now),
                    )
        if seen:
            marks = ",".join("?" for _ in seen)
            conn.execute(f"DELETE FROM documents WHERE path NOT IN ({marks})", tuple(seen))
        else:
            conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM documents_fts")
        conn.execute("INSERT INTO documents_fts(name,content) SELECT name,content FROM documents")
        conn.commit()


def search(root, folders, query, limit=30):
    sync_markdown(root, folders)
    terms = [item for item in re.split(r"[\s,，;；]+", query.strip()) if item]
    if not terms:
        return []
    # FTS5 supports phrase/token search; LIKE fallback keeps Chinese substring
    # search useful because Chinese text has no whitespace tokenization.
    with connect(root) as conn:
        where = " AND ".join("(content LIKE ? OR name LIKE ?)" for _ in terms)
        args = tuple(value for term in terms for value in (f"%{term}%", f"%{term}%"))
        rows = conn.execute(
            f"SELECT path,name,content FROM documents WHERE {where} LIMIT ?",
            (*args, limit * 3),
        ).fetchall()
    results = []
    for row in rows:
        score = sum(row["content"].lower().count(term.lower()) * 2 for term in terms)
        score += sum(row["name"].lower().count(term.lower()) * 4 for term in terms)
        if score <= 0:
            continue
        first = min((row["content"].lower().find(t.lower()) for t in terms if t.lower() in row["content"].lower()), default=0)
        snippet = re.sub(r"\s+", " ", row["content"][max(0, first - 80):first + 220]).strip()
        results.append({"score": score, "name": row["name"], "path": row["path"], "snippet": snippet})
    return sorted(results, key=lambda item: (-item["score"], item["name"]))[:limit]
