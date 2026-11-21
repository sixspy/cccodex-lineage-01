"""台参数：影响深度与通知渠道；命运口径只读。"""
from __future__ import annotations

from app.db import get_db
from app.services import fate as fate_svc
from app.services.errors import ServiceError


def list_settings(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
        return [dict(r) for r in rows]


def fate_readonly(db_path: str | None = None) -> dict:
    """设置页只读展示当前命运口径与闸口，不可从设置页改命运。"""
    with get_db(db_path) as conn:
        gate = fate_svc.gate_status(conn)
        return {
            "fate_mode": gate["fate_mode"] or "未定",
            "fate_locked": gate["fate_locked"],
            "pinned_edges": gate["pinned_edges"],
            "catalog_fingerprint": gate["catalog_fingerprint"],
            "gate_ok": gate["ok"],
            "gate_issues": gate["issues"],
        }


def update_setting(key: str, value: str, db_path: str | None = None) -> dict:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        raise ServiceError("参数键与值不能为空")
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lab_settings WHERE key=?", (key,)
        ).fetchone()
        if not row:
            raise ServiceError("参数不存在", 404)
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?", (value, key)
        )
        return dict(
            conn.execute(
                "SELECT key, value, label FROM lab_settings WHERE key=?", (key,)
            ).fetchone()
        )
