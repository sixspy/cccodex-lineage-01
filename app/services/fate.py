"""命运：把边确认成正式关系时，一次写完「下游可达集合」，且此后不可改。

两条命运互斥，本台只走一条：

- ``凭证``：旁路凭证表 ``fate_vouchers`` 写入确认瞬间可达下游节点清单，拒再改；
- ``归档``：归档层独立落一份不可改的闭包物化包 ``fate_archives`` +
  ``fate_archive_items``。

选定哪条后，影响分析只读对应那套集合。节点目录指纹与闭包清单须同源，
拧了（漂移）就拦影响开跑与入库编排闸口。
"""
from __future__ import annotations

import hashlib
from collections import deque

from app.db import get_db, serialized
from app.services.errors import ServiceError

MODE_VOUCHER = "凭证"
MODE_ARCHIVE = "归档"
MODES = (MODE_VOUCHER, MODE_ARCHIVE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def catalog_fingerprint(conn, node_ids=None) -> str:
    """节点目录指纹。

    传入 ``node_ids`` 时只对这批（闭包内）节点取目录现状摘要：
    ``编号|层级|状态`` 有序拼接后取摘要。闭包清单与目录指纹须同源，
    所以指纹只覆盖命运钉住的那批节点，园区里新建无关节点不会误伤。

    不传时对全目录取摘要，仅供页面展示。
    """
    if node_ids is None:
        rows = conn.execute(
            "SELECT id, node_code, layer, status FROM lineage_nodes "
            "WHERE status != '停用' ORDER BY node_code"
        ).fetchall()
    else:
        ids = sorted({int(i) for i in node_ids})
        if not ids:
            return _sha("")
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, node_code, layer, status FROM lineage_nodes "
            f"WHERE id IN ({placeholders}) ORDER BY node_code",
            tuple(ids),
        ).fetchall()
        present = {int(r["id"]) for r in rows}
        missing = [i for i in ids if i not in present]
        if missing:
            # 钉住节点在目录里消失了：用「缺失」占位，指纹必然变化
            extra = "\n".join(f"{i}|缺失|缺失" for i in missing)
            base = "\n".join(
                f"{r['node_code']}|{r['layer']}|{r['status']}" for r in rows
            )
            return _sha((base + "\n" + extra).strip())
    return _sha("\n".join(f"{r['node_code']}|{r['layer']}|{r['status']}" for r in rows))


def manifest_hash(items) -> str:
    """闭包清单摘要：按 (跳数, 节点编号, 层级) 归一后取摘要，与指纹同源可复算。"""
    rows = sorted((int(it["hop"]), it["node_code"], it["layer"]) for it in items)
    return _sha("\n".join(f"{hop}|{code}|{layer}" for hop, code, layer in rows))


def downstream_closure(conn, start_node_id: int) -> list[dict]:
    """从边下游节点本身起算，沿已发布边向下可达的全部节点（含起点）。

    跳数：起点为 1，逐跳 +1。确认瞬间调用，作为命运清单的唯一来源。
    """
    seen: dict[int, int] = {start_node_id: 1}
    queue = deque([start_node_id])
    while queue:
        cur = queue.popleft()
        hop = seen[cur]
        kids = conn.execute(
            "SELECT downstream_id FROM lineage_edges "
            "WHERE upstream_id=? AND status='已发布'",
            (cur,),
        ).fetchall()
        for kid in kids:
            kid_id = int(kid["downstream_id"])
            if kid_id not in seen:
                seen[kid_id] = hop + 1
                queue.append(kid_id)
    if not seen:
        return []
    placeholders = ",".join("?" for _ in seen)
    nodes = conn.execute(
        f"SELECT id, node_code, layer FROM lineage_nodes WHERE id IN ({placeholders})",
        tuple(seen.keys()),
    ).fetchall()
    by_id = {int(n["id"]): n for n in nodes}
    items = []
    for nid, hop in seen.items():
        node = by_id.get(nid)
        if not node:
            continue
        items.append(
            {
                "node_id": nid,
                "node_code": node["node_code"],
                "layer": node["layer"],
                "hop": hop,
            }
        )
    items.sort(key=lambda x: (x["hop"], x["node_code"]))
    return items


def current_mode(conn) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='fate_mode'").fetchone()
    return row["value"] if row else ""


def get_fate(conn, edge_id: int):
    return conn.execute(
        "SELECT * FROM edge_fates WHERE edge_id=?", (edge_id,)
    ).fetchone()


def fate_manifest(conn, fate_row) -> list[dict]:
    """按命运来源读那套集合：凭证读凭证表，归档读归档清单。"""
    if fate_row["fate_mode"] == MODE_VOUCHER:
        rows = conn.execute(
            "SELECT node_id, node_code, layer, hop FROM fate_vouchers "
            "WHERE edge_id=? ORDER BY hop, node_code",
            (fate_row["edge_id"],),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT i.node_id, i.node_code, i.layer, i.hop FROM fate_archive_items i "
            "JOIN fate_archives a ON a.id=i.archive_id WHERE a.edge_id=? "
            "ORDER BY i.hop, i.node_code",
            (fate_row["edge_id"],),
        ).fetchall()
    return [dict(r) for r in rows]


def _lab_mode_or_set(conn, mode: str) -> None:
    mode = (mode or "").strip()
    if mode not in MODES:
        raise ServiceError("命运只许选「凭证」或「归档」", 400)
    locked = current_mode(conn)
    if locked and locked != mode:
        raise ServiceError(
            f"本台命运口径已定为「{locked}」，两条命运不可并开，本次不可改走「{mode}」",
            409,
        )
    if not locked:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('fate_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (mode,),
        )


def confirm_edge(edge_id: int, mode: str, user: str, db_path: str | None = None) -> dict:
    """服务端一次写完选定命运。串行闸下先判既成命运，后到者拒绝而非覆盖。"""
    mode = (mode or "").strip()
    with serialized(db_path) as conn:
        edge = conn.execute(
            "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
        ).fetchone()
        if not edge:
            raise ServiceError("边不存在", 404)
        if edge["status"] == "已作废":
            raise ServiceError("已作废边不可确认成正式关系", 409)
        existing = get_fate(conn, edge_id)
        if existing:
            raise ServiceError(
                f"边 {edge['edge_code']} 命运已确认（{existing['fate_mode']} · "
                f"{existing['node_count']} 个下游），不可再改",
                409,
            )
        _lab_mode_or_set(conn, mode)

        # 草稿在确认时一并转为正式关系；校验有效节点
        if edge["status"] == "草稿":
            for nid in (edge["upstream_id"], edge["downstream_id"]):
                node = conn.execute(
                    "SELECT status FROM lineage_nodes WHERE id=?", (nid,)
                ).fetchone()
                if not node or node["status"] == "停用":
                    raise ServiceError("上下游须有效，草稿边不可确认", 409)
                if node["status"] == "登记":
                    conn.execute(
                        "UPDATE lineage_nodes SET status='在用' WHERE id=?", (nid,)
                    )
            conn.execute(
                "UPDATE lineage_edges SET status='已发布' WHERE id=?", (edge_id,)
            )

        items = downstream_closure(conn, int(edge["downstream_id"]))
        if not items:
            raise ServiceError("下游可达集合为空，无法确认命运", 409)

        # 指纹与清单同源：只对这批闭包节点取目录现状
        fp = catalog_fingerprint(conn, [it["node_id"] for it in items])
        mh = manifest_hash(items)
        count = len(items)

        # 先落清单/物化包，最后落命运索引；触发器据此保证索引落定后清单拒再改
        try:
            if mode == MODE_VOUCHER:
                for it in items:
                    conn.execute(
                        "INSERT INTO fate_vouchers(edge_id, node_id, hop, node_code, layer) "
                        "VALUES (?,?,?,?,?)",
                        (edge_id, it["node_id"], it["hop"], it["node_code"], it["layer"]),
                    )
            else:
                seq = conn.execute("SELECT COUNT(*) AS c FROM fate_archives").fetchone()["c"] + 1
                package_code = f"ARC-{seq:04d}"
                cur = conn.execute(
                    "INSERT INTO fate_archives(edge_id, package_code, fingerprint, "
                    "manifest_hash, node_count) VALUES (?,?,?,?,?)",
                    (edge_id, package_code, fp, mh, count),
                )
                archive_id = cur.lastrowid
                for it in items:
                    conn.execute(
                        "INSERT INTO fate_archive_items(archive_id, node_id, hop, node_code, layer) "
                        "VALUES (?,?,?,?,?)",
                        (archive_id, it["node_id"], it["hop"], it["node_code"], it["layer"]),
                    )
            conn.execute(
                "INSERT INTO edge_fates(edge_id, fate_mode, node_count, fingerprint, "
                "manifest_hash, confirmed_by) VALUES (?,?,?,?,?,?)",
                (edge_id, mode, count, fp, mh, user or "值班"),
            )
        except ServiceError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ServiceError(f"命运写入失败，已整体回滚：{exc}", 500) from exc

        fate = get_fate(conn, edge_id)
        return {
            "edge": dict(
                conn.execute(
                    "SELECT * FROM lineage_edges WHERE id=?", (edge_id,)
                ).fetchone()
            ),
            "fate": dict(fate),
            "mode": mode,
            "node_count": count,
            "fingerprint": fp,
            "manifest_hash": mh,
        }


def edge_fate_map(conn) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM edge_fates").fetchall()
    return {int(r["edge_id"]): dict(r) for r in rows}


def gate_status(conn) -> dict:
    """入库编排闸口 / 影响开跑闸：节点目录指纹与闭包清单须同源。"""
    fates = conn.execute(
        "SELECT f.*, e.edge_code FROM edge_fates f "
        "JOIN lineage_edges e ON e.id=f.edge_id ORDER BY e.edge_code"
    ).fetchall()
    issues: list[str] = []
    drift_edges: set[str] = set()
    mode = current_mode(conn)

    for f in fates:
        code = f["edge_code"]
        items = fate_manifest(conn, f)
        if len(items) != int(f["node_count"]):
            issues.append(f"{code}：闭包清单条数 {len(items)} 与命运索引 {f['node_count']} 不符")
            drift_edges.add(code)
        if manifest_hash(items) != f["manifest_hash"]:
            issues.append(f"{code}：闭包清单摘要与命运索引不同源（清单被改）")
            drift_edges.add(code)
        now_fp = catalog_fingerprint(conn, [it["node_id"] for it in items])
        if f["fingerprint"] != now_fp:
            issues.append(
                f"{code}：节点目录指纹已变（确认时 {f['fingerprint']}，现 {now_fp}），"
                "闭包清单与目录不同源"
            )
            drift_edges.add(code)
        # 逐节点点名：钉住节点的编号/层级/状态与目录现状不一致
        for it in items:
            row = conn.execute(
                "SELECT node_code, layer, status FROM lineage_nodes WHERE id=?",
                (it["node_id"],),
            ).fetchone()
            if not row:
                issues.append(f"{code}：钉住节点 {it['node_code']} 已不在目录")
                drift_edges.add(code)
                continue
            if row["node_code"] != it["node_code"] or row["layer"] != it["layer"]:
                issues.append(
                    f"{code}：钉住节点 {it['node_code']} 在目录变为 "
                    f"{row['node_code']}/{row['layer']}，清单与目录不同源"
                )
                drift_edges.add(code)
            elif row["status"] == "停用":
                issues.append(f"{code}：钉住节点 {it['node_code']} 已停用，清单与目录不同源")
                drift_edges.add(code)
        if f["fate_mode"] == MODE_ARCHIVE:
            arc = conn.execute(
                "SELECT * FROM fate_archives WHERE edge_id=?", (f["edge_id"],)
            ).fetchone()
            if not arc:
                issues.append(f"{code}：归档命运缺少闭包物化包")
                drift_edges.add(code)
            else:
                if arc["fingerprint"] != f["fingerprint"]:
                    issues.append(f"{code}：归档包指纹与命运索引不同源（两源拧了）")
                    drift_edges.add(code)
                if arc["manifest_hash"] != f["manifest_hash"]:
                    issues.append(f"{code}：归档包清单摘要与命运索引不同源（两源拧了）")
                    drift_edges.add(code)
            if conn.execute(
                "SELECT 1 FROM fate_vouchers WHERE edge_id=?", (f["edge_id"],)
            ).fetchone():
                issues.append(f"{code}：归档命运上出现凭证清单，两条命运并开了")
                drift_edges.add(code)
        else:
            if conn.execute(
                "SELECT 1 FROM fate_archives WHERE edge_id=?", (f["edge_id"],)
            ).fetchone():
                issues.append(f"{code}：凭证命运上出现归档包，两条命运并开了")
                drift_edges.add(code)

    return {
        "ok": not issues,
        "issues": issues,
        "drift_edges": sorted(drift_edges),
        "fate_mode": mode,
        "fate_locked": bool(mode),
        "pinned_edges": len(fates),
        "catalog_fingerprint": catalog_fingerprint(conn),
    }


def status(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        info = gate_status(conn)
        info["modes"] = list(MODES)
        return info
