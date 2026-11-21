"""入库编排闸口：入库前必须与命运闭包同源，拧了就拦。

本台没有独立编排执行器，闸口只做一次预检并落检查结论；真正的编排由上游
调度读取本闸口的 ``ok`` 后再放行。影响分析开跑走同一套 ``gate_status``，
两处对的是同一份节点目录指纹与闭包清单。
"""
from __future__ import annotations

from app.db import get_db
from app.services import fate as fate_svc
from app.services.errors import ServiceError


def preflight(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        gate = fate_svc.gate_status(conn)
        if not gate["ok"]:
            raise ServiceError(
                "入库编排闸口未对齐：" + "；".join(gate["issues"]), 409
            )
        return {
            "ok": True,
            "fate_mode": gate["fate_mode"] or "未定",
            "pinned_edges": gate["pinned_edges"],
            "catalog_fingerprint": gate["catalog_fingerprint"],
            "message": (
                f"入库编排放行：命运口径「{gate['fate_mode'] or '未定'}」，"
                f"钉住 {gate['pinned_edges']} 条边，节点目录与闭包清单同源"
            ),
        }
