"""扩展长测的可信预期与交付清单边界，不运行模型候选业务。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


WORKLOAD_DIR = Path(__file__).resolve().parents[1] / "validation" / "native-first-endurance"


def load_workloads():
    # 与真实运行器一样解析同目录模块，不依赖测试启动时的当前目录。
    original_path = sys.path[:]
    previous = sys.modules.get("workload")
    try:
        sys.path.insert(0, str(WORKLOAD_DIR))
        for name in ("workload", "workload_extended"):
            spec = importlib.util.spec_from_file_location(name, WORKLOAD_DIR / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            if name == "workload":
                sys.modules[name] = module
            spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original_path
        if previous is None:
            sys.modules.pop("workload", None)
        else:
            sys.modules["workload"] = previous


extended = load_workloads()


def group(name, stage=30):
    return next(item for item in extended.cases(stage) if item["name"] == name)


def test_fx_oracle_keeps_half_even_sign_and_integer_precision():
    request, expected = group("fx_precision")["steps"][0]
    assert [item["cents"] for item in request["rows"]] == [10, 30, -10, -30, 9007199254740993]
    assert [item["cents"] for item in expected["data"]] == [70, 212, -70, -212, 63500754745924001]
    assert all(item["currency"] == "CNY" for item in expected["data"])


@pytest.mark.parametrize("name,expected", [
    ("allocation_tie", [{"account": "a", "cents": 1}, {"account": "b", "cents": 0}, {"account": "c", "cents": 0}]),
    ("allocation_negative", [{"account": "a", "cents": -1}, {"account": "b", "cents": 0}, {"account": "c", "cents": 0}]),
    ("allocation_large", [{"account": "a", "cents": 3002399751580331}, {"account": "b", "cents": 6004799503160663}]),
])
def test_allocation_oracle_uses_exact_remainder_order(name, expected):
    assert group(name)["steps"][0][1] == {"ok": True, "data": expected}


def test_snapshot_oracle_uses_canonical_unicode_bytes_and_stable_order():
    snapshot = group("snapshot_unicode")["steps"][1][1]["data"]
    canonical = '[{"account":"银行","category":"餐饮","cents":-5,"currency":"CNY","date":"2024-01-01","id":"早"},{"account":"cash","category":"food","cents":9007199254740993,"currency":"CNY","date":"2024-01-02","id":"晚"}]'
    assert snapshot["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert [item["id"] for item in snapshot["rows"]] == ["早", "晚"]
    empty = group("snapshot_empty")["steps"][0][1]["data"]
    assert empty == {"rows": [], "sha256": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}


def test_review_oracle_separates_external_failure_from_technical_pass():
    result = group("gate_external_fail")["steps"][0][1]["data"]
    assert result["technical_status"] == "PASS"
    assert result["release_status"] == "FAIL"
    assert result["checks"] == [
        {"id": "external", "kind": "review", "status": "FAIL", "reason": "人工审查发现风险", "evidence": ["issue-7"]},
        {"id": "unit", "kind": "technical", "status": "PASS", "count": 17},
    ]
    assert result["missing"] == []
    empty = group("gate_empty")["steps"][0][1]["data"]
    assert empty == {"technical_status": "UNVERIFIED", "release_status": "UNVERIFIED", "checks": [], "missing": []}


def test_restore_oracles_distinguish_bad_digest_from_bad_rows():
    cases = extended.cases(27)
    relevant = [item for item in cases if item["name"].startswith("restore_invalid_")]
    assert relevant
    for item in relevant:
        steps = item["steps"]
        assert steps[1][1] == {"ok": False, "error": "invalid_snapshot"}
        assert steps[2][1] == {"ok": True, "data": [{"id": "a", "date": "2024-01-02", "account": "cash", "currency": "CNY", "category": "food", "cents": 1234}]}
    bad_bool = next(item for item in cases if item["name"] == "restore_invalid_bool_cents")["steps"][1][0]["snapshot"]
    bad_row = next(row for row in bad_bool["rows"] if row["id"] == "bad")
    assert bad_row["cents"] is True
    canonical = json.dumps(bad_bool["rows"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert bad_bool["sha256"] == hashlib.sha256(canonical.encode()).hexdigest()


def test_late_failures_observe_rollback_through_next_process_request():
    batch = group("batch_patch_late_failure", 25)["steps"]
    assert batch[1][0]["operations"][0]["op"] == "patch"
    assert batch[1][1] == {"ok": False, "error": "not_found"}
    assert batch[2][0] == {"op": "list"}
    assert batch[2][1]["data"][0]["cents"] == 1234
    settle = group("settle_conflict_rollback", 29)["steps"]
    assert settle[1][1] == {"ok": False, "error": "conflict"}
    assert [item["id"] for item in settle[2][1]["data"]] == ["a"]


@pytest.fixture
def delivery(tmp_path):
    (tmp_path / "ledgerlab").mkdir()
    (tmp_path / "ledgerlab" / "__init__.py").write_bytes(b'"""ledger"""\n')
    (tmp_path / "ledgerlab" / "money.py").write_bytes(b"def cents():\n    return 1\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ARCHITECTURE.md").write_text("模块职责由人工复核。", encoding="utf-8")
    (tmp_path / "docs" / "RECOVERY.md").write_text("事务与恢复边界由人工复核。", encoding="utf-8")
    fixture_hash = "a" * 64
    manifest = {
        "schema_version": 1, "implemented_stages": 30, "fixture_sha256": fixture_hash,
        "source_files": [{"path": path.relative_to(tmp_path).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                         for path in sorted((tmp_path / "ledgerlab").glob("*.py"))],
    }
    return tmp_path, fixture_hash, manifest


def save_manifest(delivery):
    workspace, _, manifest = delivery
    (workspace / "DELIVERY.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_manifest_matches_current_disk_bytes(delivery):
    save_manifest(delivery)
    assert extended.delivery_failures(*delivery[:2]) == []
    (delivery[0] / "ledgerlab" / "money.py").write_bytes(b"changed\n")
    failures = extended.delivery_failures(*delivery[:2])
    assert any(item.get("path") == "ledgerlab/money.py" and "sha256" in item["error"] for item in failures)


@pytest.mark.parametrize("change", ["omitted", "duplicate", "unsorted", "fixture", "version_bool", "stage_bool", "hash_uppercase"])
def test_manifest_rejects_incomplete_or_ambiguous_inventory(delivery, change):
    manifest = delivery[2]
    if change == "omitted":
        manifest["source_files"].pop()
    elif change == "duplicate":
        manifest["source_files"].append(dict(manifest["source_files"][0]))
    elif change == "unsorted":
        manifest["source_files"].reverse()
    elif change == "fixture":
        manifest["fixture_sha256"] = "b" * 64
    elif change == "version_bool":
        manifest["schema_version"] = True
    elif change == "stage_bool":
        manifest["implemented_stages"] = True
    else:
        manifest["source_files"][0]["sha256"] = manifest["source_files"][0]["sha256"].upper()
    save_manifest(delivery)
    assert extended.delivery_failures(*delivery[:2])


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/outside.py", "ledgerlab\\money.py", "ledgerlab/../money.py", "ledgerlab//money.py", "./ledgerlab/money.py", ""])
def test_manifest_rejects_noncanonical_and_external_paths(delivery, path):
    delivery[2]["source_files"][0]["path"] = path
    save_manifest(delivery)
    assert extended.delivery_failures(*delivery[:2])


@pytest.mark.parametrize("value", [None, [], {"source_files": None}, {"schema_version": 1, "implemented_stages": 30, "fixture_sha256": "a" * 64, "source_files": [None, 7, {"path": []}]}])
def test_manifest_malformed_shapes_return_concrete_errors(delivery, value):
    (delivery[0] / "DELIVERY.json").write_text(json.dumps(value), encoding="utf-8")
    failures = extended.delivery_failures(*delivery[:2])
    assert failures
    assert all(item.get("case") and isinstance(item.get("error"), str) for item in failures)


def test_manifest_missing_json_missing_docs_and_new_nested_source(delivery):
    assert extended.delivery_failures(*delivery[:2])
    save_manifest(delivery)
    (delivery[0] / "docs" / "RECOVERY.md").unlink()
    nested = delivery[0] / "ledgerlab" / "reports"
    nested.mkdir()
    (nested / "daily.py").write_bytes(b"x = 1\n")
    failures = extended.delivery_failures(*delivery[:2])
    assert {"docs/RECOVERY.md", "ledgerlab/reports/daily.py"} <= {item.get("path") for item in failures}


def test_manifest_invalid_json_is_a_failure(delivery):
    (delivery[0] / "DELIVERY.json").write_text("{", encoding="utf-8")
    assert extended.delivery_failures(*delivery[:2])


def test_manifest_cannot_hash_through_external_symlink(delivery, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.py")
    outside.write_bytes(b"not inside workspace\n")
    link = tmp_path / "ledgerlab" / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("当前环境未授予创建符号链接权限")
    delivery[2]["source_files"].append({"path": "ledgerlab/linked.py", "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()})
    delivery[2]["source_files"].sort(key=lambda item: item["path"])
    save_manifest(delivery)
    failures = extended.delivery_failures(*delivery[:2])
    assert any(item.get("path") == "ledgerlab/linked.py" for item in failures)
