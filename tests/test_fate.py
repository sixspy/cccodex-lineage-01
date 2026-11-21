import threading

import pytest

from app.db import get_db
from app.services import coverage, edges, fate, impact, nodes, orchestration, settings
from app.services.errors import ServiceError


def _edge(db_path, code):
    return next(e for e in edges.list_edges(db_path=db_path) if e["edge_code"] == code)


def _node(db_path, code):
    return next(n for n in nodes.list_nodes(db_path=db_path) if n["node_code"] == code)


def test_fate_chip_and_count_after_confirm(db_path):
    e = _edge(db_path, "E-03")  # DWD-ORD -> DWS-REV; closure = DWS-REV, ADS-DASH, ADS-ALERT
    assert e["fate_mode"] == ""
    r = fate.confirm_edge(e["id"], "凭证", "值班甲", db_path=db_path)
    assert r["mode"] == "凭证"
    assert r["node_count"] == 3
    chip = _edge(db_path, "E-03")
    assert chip["fate_mode"] == "凭证"
    assert chip["fate_node_count"] == 3
    assert chip["fate_synced"] is True


def test_fate_confirmed_is_immutable(db_path):
    e = _edge(db_path, "E-05")
    fate.confirm_edge(e["id"], "凭证", "值班甲", db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        fate.confirm_edge(e["id"], "归档", "值班乙", db_path=db_path)
    assert exc.value.status_code == 409
    assert "不可再改" in exc.value.message

    with pytest.raises(Exception):
        with get_db(db_path) as conn:
            conn.execute("UPDATE fate_vouchers SET node_code='X' WHERE edge_id=?", (e["id"],))


def test_two_modes_are_exclusive(db_path):
    a = _edge(db_path, "E-03")
    b = _edge(db_path, "E-06")
    fate.confirm_edge(a["id"], "凭证", "值班甲", db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        fate.confirm_edge(b["id"], "归档", "值班乙", db_path=db_path)
    assert exc.value.status_code == 409
    assert "不可并开" in exc.value.message
    # 同口径仍可继续确认
    r = fate.confirm_edge(b["id"], "凭证", "值班乙", db_path=db_path)
    assert r["mode"] == "凭证"


def test_impact_reads_pinned_set_and_count_matches(db_path):
    e = _edge(db_path, "E-03")
    r = fate.confirm_edge(e["id"], "凭证", "值班甲", db_path=db_path)
    root = _node(db_path, "N-DWD-ORD")
    job = impact.start_impact(int(root["id"]), db_path=db_path)
    assert job["status"] == "成功"
    assert job["pinned_count"] == r["node_count"]
    assert job["fate_mode"] == "凭证"
    # 钉住边为终点：确认后新加的现行边不影响该根节点的下游
    new = nodes.register_node("N-ADS-NEW", "ads_new", "ADS", "分析组", db_path=db_path)
    edges.create_edge("E-NEW", _node(db_path, "N-DWS-REV")["id"], new["id"], db_path=db_path)
    edges.publish_edge(_edge(db_path, "E-NEW")["id"], db_path=db_path)
    job2 = impact.start_impact(int(root["id"]), db_path=db_path)
    assert job2["affected_count"] == job["affected_count"]


def test_gate_blocks_on_catalog_drift(db_path):
    e = _edge(db_path, "E-03")
    fate.confirm_edge(e["id"], "归档", "值班甲", db_path=db_path)
    assert fate.status(db_path=db_path)["ok"] is True
    # 无关新节点不误伤
    nodes.register_node("N-UNREL", "unrel", "ODS", "采集组", db_path=db_path)
    assert fate.status(db_path=db_path)["ok"] is True
    # 钉住节点改层级 -> 漂移
    nid = int(_node(db_path, "N-DWS-REV")["id"])
    with get_db(db_path) as conn:
        conn.execute("UPDATE lineage_nodes SET layer='ADS' WHERE id=?", (nid,))
    st = fate.status(db_path=db_path)
    assert st["ok"] is False
    assert any("N-DWS-REV" in s for s in st["issues"])
    root = _node(db_path, "N-DWD-ORD")
    with pytest.raises(ServiceError) as exc:
        impact.start_impact(int(root["id"]), db_path=db_path)
    assert exc.value.status_code == 409
    assert "同源" in exc.value.message
    with pytest.raises(ServiceError):
        orchestration.preflight(db_path=db_path)


def test_coverage_and_settings_expose_fate(db_path):
    e = _edge(db_path, "E-05")
    fate.confirm_edge(e["id"], "凭证", "值班甲", db_path=db_path)
    rep = coverage.run_coverage(db_path=db_path)
    assert rep["pinned_edges"] == 1
    assert rep["fate_drift"] == 0
    info = settings.fate_readonly(db_path=db_path)
    assert info["fate_mode"] == "凭证"
    assert info["fate_locked"] is True
    assert info["gate_ok"] is True


def test_concurrent_confirm_single_writer(db_path):
    e = _edge(db_path, "E-05")
    results = []
    barrier = threading.Barrier(2)

    def worker(user):
        barrier.wait()
        try:
            r = fate.confirm_edge(e["id"], "凭证", user, db_path=db_path)
            results.append(("ok", user, r["node_count"]))
        except ServiceError as exc:
            results.append(("err", user, exc.status_code))

    threads = [threading.Thread(target=worker, args=(u,)) for u in ("甲", "乙")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1
    assert len(errs) == 1
    assert errs[0][2] == 409
    with get_db(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) c FROM edge_fates WHERE edge_id=?", (e["id"],)
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) c FROM fate_vouchers WHERE edge_id=?", (e["id"],)
        ).fetchone()["c"] == 1
