"""影响分析：从根节点向下展开。

已确认命运的边不再跟着现行关系瞎算，直接读确认瞬间钉住的下游可达集合；
未确认的边仍按已发布关系展开。开跑前须过节点目录指纹与闭包清单同源闸。
"""
from __future__ import annotations

from collections import deque

from app.db import get_db
from app.services import fate as fate_svc
from app.services.errors import ServiceError


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id ORDER BY j.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def job_detail(job_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not job:
            raise ServiceError("作业不存在", 404)
        hits = conn.execute(
            "SELECT h.*, n.node_code, n.table_name, n.layer FROM impact_hits h "
            "JOIN lineage_nodes n ON n.id=h.node_id WHERE h.job_id=? ORDER BY h.hop, h.id",
            (job_id,),
        ).fetchall()
        return {"job": dict(job), "hits": [dict(x) for x in hits]}


def start_impact(root_node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        root = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (root_node_id,)
        ).fetchone()
        if not root:
            raise ServiceError("根节点不存在", 404)
        if root["status"] == "停用":
            raise ServiceError("停用节点不可开影响分析", 409)

        # 入库编排 / 影响开跑同闸：目录指纹与闭包清单拧了就拦
        gate = fate_svc.gate_status(conn)
        if not gate["ok"]:
            raise ServiceError(
                "闸口未对齐，影响不可开跑：" + "；".join(gate["issues"]), 409
            )

        running = conn.execute(
            "SELECT id FROM impact_jobs WHERE root_node_id=? AND status IN ('待执行','执行中')",
            (root_node_id,),
        ).fetchone()
        if running:
            raise ServiceError("该根节点已有在途影响作业", 409)

        seq = conn.execute("SELECT COUNT(*) AS c FROM impact_jobs").fetchone()["c"] + 1
        job_code = f"IMP-{seq:04d}"
        cur = conn.execute(
            "INSERT INTO impact_jobs(job_code, root_node_id, status, message, started_at) "
            "VALUES (?,?,'执行中','展开中',datetime('now'))",
            (job_code, root_node_id),
        )
        job_id = cur.lastrowid

        fates = fate_svc.edge_fate_map(conn)
        modes_used: set[str] = set()
        pinned_mode: dict[int, str] = {}
        # BFS：未确认边走现行已发布关系并继续展开；已确认边是「钉死」的终点，
        # 直接并入确认瞬间的闭包、不再沿现行关系向前展开。
        seen: dict[int, int] = {root_node_id: 0}
        via: dict[int, str] = {root_node_id: ""}
        expanded: set[int] = set()
        queue = deque([root_node_id])
        while queue:
            cur_id = queue.popleft()
            if cur_id in expanded:
                continue
            expanded.add(cur_id)
            hop = seen[cur_id]
            edges = conn.execute(
                "SELECT id, downstream_id FROM lineage_edges "
                "WHERE upstream_id=? AND status='已发布'",
                (cur_id,),
            ).fetchall()
            for edge in edges:
                fate = fates.get(int(edge["id"]))
                if fate:
                    mode = fate["fate_mode"]
                    modes_used.add(mode)
                    for it in fate_svc.fate_manifest(conn, fate):
                        nid = int(it["node_id"])
                        pinned_mode[nid] = mode
                        jhop = hop + int(it["hop"])
                        if nid not in seen or jhop < seen[nid]:
                            seen[nid] = jhop
                        if via.get(nid) == "":
                            via[nid] = mode
                    continue
                kid_id = int(edge["downstream_id"])
                if kid_id not in seen or hop + 1 < seen[kid_id]:
                    seen[kid_id] = hop + 1
                    via[kid_id] = ""
                if kid_id not in expanded:
                    queue.append(kid_id)

        # 只统计真正进入本次作业命中集合的同源节点（菱形图里被捷径覆盖时也算）
        pinned = sum(1 for nid in seen if nid in pinned_mode)
        for nid in pinned_mode:
            if nid in seen:
                via[nid] = pinned_mode[nid]

        for nid, hop in seen.items():
            conn.execute(
                "INSERT OR IGNORE INTO impact_hits(job_id, node_id, hop, via_fate) "
                "VALUES (?,?,?,?)",
                (job_id, nid, hop, via.get(nid, "")),
            )

        affected = len(seen)
        fate_mode = modes_used.pop() if len(modes_used) == 1 else ""
        fate_mixed = 1 if len(modes_used) > 1 else 0
        msg = f"影响到 {affected} 个节点"
        if pinned:
            msg += f"，其中 {pinned} 个走「{fate_mode or '混合'}」命运同源集合"
        conn.execute(
            "UPDATE impact_jobs SET status='成功', affected_count=?, pinned_count=?, "
            "fate_mode=?, fate_mixed=?, message=?, finished_at=datetime('now') "
            "WHERE id=?",
            (affected, pinned, fate_mode, fate_mixed, msg, job_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM impact_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )

