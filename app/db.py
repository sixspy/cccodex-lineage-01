import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lineage_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_code TEXT NOT NULL UNIQUE,
  table_name TEXT NOT NULL,
  layer TEXT NOT NULL CHECK (layer IN ('ODS','DWD','DWS','ADS')),
  owner_team TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('登记','在用','停用')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_code TEXT NOT NULL UNIQUE,
  upstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  downstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  transform_kind TEXT NOT NULL DEFAULT '直抽',
  status TEXT NOT NULL CHECK (status IN ('草稿','已发布','已作废')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(upstream_id, downstream_id)
);

CREATE TABLE IF NOT EXISTS impact_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_code TEXT NOT NULL UNIQUE,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  status TEXT NOT NULL CHECK (status IN ('待执行','执行中','成功','失败')),
  affected_count INTEGER NOT NULL DEFAULT 0,
  pinned_count INTEGER NOT NULL DEFAULT 0,
  fate_mode TEXT NOT NULL DEFAULT '',
  fate_mixed INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS impact_hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  hop INTEGER NOT NULL DEFAULT 0,
  via_fate TEXT NOT NULL DEFAULT '',
  UNIQUE(job_id, node_id)
);

CREATE TABLE IF NOT EXISTS notify_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  channel TEXT NOT NULL DEFAULT '站内',
  status TEXT NOT NULL CHECK (status IN ('待发','已发','失败')),
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coverage_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  summary TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  edge_count INTEGER NOT NULL,
  orphan_count INTEGER NOT NULL,
  uncovered_edges INTEGER NOT NULL,
  pinned_edges INTEGER NOT NULL DEFAULT 0,
  fate_drift INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edge_fates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_id INTEGER NOT NULL UNIQUE REFERENCES lineage_edges(id),
  fate_mode TEXT NOT NULL CHECK (fate_mode IN ('凭证','归档')),
  node_count INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  confirmed_by TEXT NOT NULL,
  confirmed_at TEXT NOT NULL DEFAULT (datetime('now')),
  note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fate_vouchers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_id INTEGER NOT NULL REFERENCES lineage_edges(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  hop INTEGER NOT NULL,
  node_code TEXT NOT NULL,
  layer TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(edge_id, node_id)
);

CREATE TABLE IF NOT EXISTS fate_archives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_id INTEGER NOT NULL UNIQUE REFERENCES lineage_edges(id),
  package_code TEXT NOT NULL UNIQUE,
  fingerprint TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  sealed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fate_archive_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_id INTEGER NOT NULL REFERENCES fate_archives(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  hop INTEGER NOT NULL,
  node_code TEXT NOT NULL,
  layer TEXT NOT NULL,
  UNIQUE(archive_id, node_id)
);

CREATE TABLE IF NOT EXISTS lab_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- 命运一经写入即不可改：索引、凭证清单、归档包、归档清单四张表全部只增不删不改
CREATE TRIGGER IF NOT EXISTS trg_edge_fates_immutable_update
BEFORE UPDATE ON edge_fates
BEGIN SELECT RAISE(ABORT, '命运索引一经写入不可修改'); END;

CREATE TRIGGER IF NOT EXISTS trg_edge_fates_immutable_delete
BEFORE DELETE ON edge_fates
BEGIN SELECT RAISE(ABORT, '命运索引一经写入不可删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_vouchers_guard_insert
BEFORE INSERT ON fate_vouchers
WHEN EXISTS (SELECT 1 FROM edge_fates WHERE edge_id = NEW.edge_id)
BEGIN SELECT RAISE(ABORT, '该边命运已确认，凭证清单拒再改'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_vouchers_immutable_update
BEFORE UPDATE ON fate_vouchers
BEGIN SELECT RAISE(ABORT, '凭证清单一经写入不可修改'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_vouchers_immutable_delete
BEFORE DELETE ON fate_vouchers
BEGIN SELECT RAISE(ABORT, '凭证清单一经写入不可删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archives_guard_insert
BEFORE INSERT ON fate_archives
WHEN EXISTS (SELECT 1 FROM edge_fates WHERE edge_id = NEW.edge_id)
BEGIN SELECT RAISE(ABORT, '该边命运已确认，归档包拒再落'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archives_immutable_update
BEFORE UPDATE ON fate_archives
BEGIN SELECT RAISE(ABORT, '归档包一经封存不可修改'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archives_immutable_delete
BEFORE DELETE ON fate_archives
BEGIN SELECT RAISE(ABORT, '归档包一经封存不可删除'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archive_items_guard_insert
BEFORE INSERT ON fate_archive_items
WHEN EXISTS (
  SELECT 1 FROM fate_archives a
  JOIN edge_fates f ON f.edge_id = a.edge_id
  WHERE a.id = NEW.archive_id
)
BEGIN SELECT RAISE(ABORT, '归档包已封存，清单一经写入不可改'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archive_items_immutable_update
BEFORE UPDATE ON fate_archive_items
BEGIN SELECT RAISE(ABORT, '归档清单不可修改'); END;

CREATE TRIGGER IF NOT EXISTS trg_fate_archive_items_immutable_delete
BEFORE DELETE ON fate_archive_items
BEGIN SELECT RAISE(ABORT, '归档清单不可删除'); END;
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout 让并发写锁等待而不是立刻报 database is locked
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def serialized(db_path: str | None = None):
    """串行闸：立即取写锁（BEGIN IMMEDIATE），两名仓管同时确认也只会有一个先落。

    事务内二次读库再做幂等判断，后到者看到先到者已写入的命运后拒绝或返回既成命运，
    不会各写一份互相覆盖。
    """
    conn = connect(db_path)
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.close()


def init_schema(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
