"""显式扩展至30阶段的任务说明、可信业务预期和磁盘交付验收。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from workload import err, norm, ok, row


STAGES = [
    ("fx_convert", "新增 fx_convert：输入规范 rows、target_currency（3个ASCII字母，输出规范为大写）、rates（源币种到每1源币种多少目标币种的正有限十进制字符串）。同目标币种不需rate；其它每行以精确Decimal将cents*rate按ROUND_HALF_EVEN舍入为整数分，保留其它字段及输入顺序，将currency改为目标币种。非法/缺失/非字符串/非正rate返回invalid_rate；非法目标返回invalid_row。覆盖正负半偶边界与大于2^53的整数，不能经float。"),
    ("import_csv", "新增 import_csv：输入db,text，复用parse_csv规范化，再在一个事务原子导入，返回{inserted:N,duplicates:N}。CSV解析、金额、日期或id冲突任一失败，整批不保留前面的有效行，错误码沿用既有契约。支持BOM、CRLF、引号内逗号、空白行及同内容幂等重放。"),
    ("allocate", "新增 allocate：输入cents（非bool整数）、weights（非空对象，非空account名称映射到正的非bool整数）。对abs(cents)按最大余数法分配，尾差按余数降序、account字典序破同分，再统一施加输入符号。返回按account排序的{account,cents}数组，严格保持总和。非法输入返回invalid_allocation；0、负数与大整数不得损失精度。"),
    ("batch", "新增 batch：输入db,operations数组；子操作只能是import/transfer/reverse/relabel，第25阶段起再允许patch。子操作不许含db，不允许嵌套batch或未知op，否则invalid_batch。所有操作在同一事务内按顺序执行，全成功返回各操作data组成的数组；任一业务失败返回该原错误码并整体回滚。后续独立进程list不得看见失败批次的任何改动，完整重放遵守各子操作原有幂等契约。"),
    ("patch", "新增 patch：输入db,id,expected（完整规范旧行）,changes（只能含category非空字符串/cents非bool整数且至少一项）。行不存在not_found；现存行与expected不完全相等返回conflict；非法changes返回invalid_patch。成功原子比较更新并返回完整规范新行，保留id/date/account/currency。过期expected不得覆盖别人更新；失败不改库。batch现在允许patch，并须在后面操作失败时撤销patch。"),
    ("snapshot", "新增 snapshot：输入db，返回{rows,sha256}；rows为按date,id排序的全部规范行；sha256为json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')的SHA256小写hex，无换行。不可修改数据库，空库、Unicode、负值及大整数遵守同一格式。"),
    ("restore", "新增 restore：输入db,snapshot（上述{rows,sha256}格式）。核对digest及完整规范行：id唯一、合法日期、非bool整数cents、3位大写ASCII currency、非空id/account/category，不含amount或额外字段。任意失败invalid_snapshot且原库完全不变。成功在一个事务替换全部流水并返回{restored:N}，后续list/snapshot一致，同包重复恢复幂等；空快照允许清空。"),
    ("release_gate", "新增 release_gate：required=[{id,kind}]、checks=[{id,kind,status,其它说明字段可保留}]；kind仅technical/review；status仅PASS/FAIL/UNVERIFIED/BLOCKED。两数组各自重复id、未知kind/status、已知id的kind冲突均invalid_review。返回{technical_status,release_status,checks,missing}，checks保留所有实际项和原说明字段且按id排序，missing为缺失required id字典序列表。缺项视UNVERIFIED，优先级FAIL>BLOCKED>UNVERIFIED>PASS；技术状态只合并实际technical及缺失required technical，总体合并全部实际项及缺失项；没有任何technical项时技术状态UNVERIFIED；required与checks同时为空时两个状态均为UNVERIFIED，不能以空集合制造PASS。外部review FAIL不得污染technical PASS，也不得删除逐项明细。"),
    ("settle", "新增 settle：输入db,rows原始交易，复用normalize后按各币种分别检查cents总和为0，至少两条不同id且每条规范分值非0，否则invalid_journal。金额/日期等规范化错误沿用原错误码。通过后原子import并返回{inserted:N,duplicates:N}；跨币种合计为0不算平衡，舍入后必须仍平衡，冲突不得部分写库，重放幂等。"),
    ("delivery_manifest", "不加新业务op。真实完成docs/ARCHITECTURE.md（实际模块职责、调用和事务边界）与docs/RECOVERY.md（失败回滚、快照校验、恢复原子性与限制），内容将人工回读，不靠字数、标题或自报PASS。创建DELIVERY.json：schema_version=1、implemented_stages=30、fixture_sha256为fixed_fixture.txt当前字节SHA256、source_files=[{path,sha256}]覆盖ledgerlab目录全部.py；path相对工作区、POSIX规范路径，列表按path排序，不重复、不引用工作区外文件，sha256为当前文件字节的小写hex。源码/文档/交付清单准备完后最后运行获准验收命令，不能把工具started/completed或部分PASS当成整体交付。"),
]


def _snapshot(rows):
    """只对手写预期行计算摘要，绝不从候选进程返回值派生预期。"""
    ordered = sorted(deepcopy(rows), key=lambda item: (item["date"], item["id"]))
    payload = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"rows": ordered, "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}


def cases(stage):
    """扩展用例累计加入；每组独立数据库，每一步由独立业务进程执行。"""
    groups = []

    def add(name, steps):
        groups.append({"name": name, "steps": steps})

    def one(name, request, expected):
        add(name, [(request, expected)])

    if stage <= 20:
        return groups
    a = norm()
    inserted = ok({"inserted": 1, "duplicates": 0})
    two = ok({"inserted": 2, "duplicates": 0})
    replay_two = ok({"inserted": 0, "duplicates": 2})
    initial = ({"op": "import", "rows": [row()]}, inserted)
    unchanged = ({"op": "list"}, ok([a]))
    fx_rows = [norm(str(i), cents, currency="USD") for i, cents in enumerate([10, 30, -10, -30, 9007199254740993])]
    one("fx_precision", {"op": "fx_convert", "rows": fx_rows, "target_currency": "cny", "rates": {"USD": "7.05"}}, ok([norm(str(i), cents) for i, cents in enumerate([70, 212, -70, -212, 63500754745924001])]))
    one("fx_identity_order", {"op": "fx_convert", "rows": [norm("z", -3), norm("a", 2, currency="USD")], "target_currency": "CNY", "rates": {"USD": "0.5"}}, ok([norm("z", -3), norm("a", 1)]))
    one("fx_unbounded_integer", {"op": "fx_convert", "rows": [norm(cents=100000000000000000000000000001, currency="USD")], "target_currency": "CNY", "rates": {"USD": "1"}}, ok([norm(cents=100000000000000000000000000001)]))
    one("fx_empty", {"op": "fx_convert", "rows": [], "target_currency": "USD", "rates": {}}, ok([]))
    one("fx_missing_rate", {"op": "fx_convert", "rows": [norm(currency="USD")], "target_currency": "CNY", "rates": {}}, err("invalid_rate"))
    for i, rate in enumerate(["0", "-1", "NaN", "Infinity", "", "bad", True, 7.05, None]):
        one("fx_invalid_rate_" + str(i), {"op": "fx_convert", "rows": [norm(currency="USD")], "target_currency": "CNY", "rates": {"USD": rate}}, err("invalid_rate"))
    for i, target in enumerate(["CN", "USDD", "中文元", "ＵＳＤ", "1SD", True, None]):
        one("fx_invalid_target_" + str(i), {"op": "fx_convert", "rows": [], "target_currency": target, "rates": {}}, err("invalid_row"))

    if stage >= 22:
        header = "id,date,account,amount,currency,category\r\n"
        text = '\ufeff' + header + 'a,2024-01-02,cash,1.005,cny,"food,drink"\r\n\r\nb,2024-01-01,bank,-0.015,USD,fee\r\n'
        csv_rows = [norm("b", -2, date="2024-01-01", account="bank", currency="USD", category="fee"), norm(cents=100, category="food,drink")]
        add("import_csv_replay", [({"op": "import_csv", "text": text}, two), ({"op": "import_csv", "text": text}, replay_two), ({"op": "list"}, ok(csv_rows))])
        one("import_csv_missing_header", {"op": "import_csv", "text": "id,amount\na,1\n"}, err("invalid_csv"))
        one("import_csv_empty", {"op": "import_csv", "text": header}, ok({"inserted": 0, "duplicates": 0}))
        for name, bad, error in [("amount", "b,2024-01-02,cash,NaN,CNY,food\r\n", "invalid_amount"), ("date", "b,2024-02-30,cash,1,CNY,food\r\n", "invalid_date"), ("conflict", "x,2024-01-02,cash,2,CNY,food\r\n", "conflict")]:
            add("import_csv_rollback_" + name, [({"op": "import_csv", "text": header + "x,2024-01-02,cash,1,CNY,food\r\n" + bad}, err(error)), ({"op": "list"}, ok([]))])
        add("import_csv_existing_conflict", [initial, ({"op": "import_csv", "text": header + "new,2024-01-02,cash,1,CNY,food\r\na,2024-01-02,cash,2,CNY,food\r\n"}, err("conflict")), unchanged])

    if stage >= 23:
        for name, cents, weights, expected in [
            ("tie", 1, {"c": 1, "b": 1, "a": 1}, [("a", 1), ("b", 0), ("c", 0)]),
            ("negative", -1, {"c": 1, "a": 1, "b": 1}, [("a", -1), ("b", 0), ("c", 0)]),
            ("weighted", 7, {"b": 2, "a": 1}, [("a", 2), ("b", 5)]),
            ("zero", 0, {"b": 2, "a": 1}, [("a", 0), ("b", 0)]),
            ("large", 9007199254740994, {"b": 2, "a": 1}, [("a", 3002399751580331), ("b", 6004799503160663)]),
        ]:
            one("allocation_" + name, {"op": "allocate", "cents": cents, "weights": weights}, ok([{"account": account, "cents": value} for account, value in expected]))
        for i, (cents, weights) in enumerate([(True, {"a": 1}), (1.5, {"a": 1}), ("1", {"a": 1}), (1, {}), (1, []), (1, {"": 1}), (1, {"a": True}), (1, {"a": 0}), (1, {"a": -1}), (1, {"a": 1.5}), (1, {"a": "1"})]):
            one("allocation_invalid_" + str(i), {"op": "allocate", "cents": cents, "weights": weights}, err("invalid_allocation"))

    if stage >= 24:
        transfer = {"op": "transfer", "id": "t", "date": "2024-01-03", "from_account": "cash", "to_account": "bank", "amount": "1.25", "currency": "CNY"}
        reversal = norm("r", -1234, date="2024-01-04")
        operations = [{"op": "import", "rows": [row()]}, transfer, {"op": "reverse", "id": "a", "reversal_id": "r", "date": "2024-01-04"}, {"op": "relabel", "ids": ["a"], "category": "food"}]
        final_rows = [a, norm("t:in", 125, date="2024-01-03", account="bank", category="transfer"), norm("t:out", -125, date="2024-01-03", category="transfer"), reversal]
        add("batch_sequential_replay", [({"op": "batch", "operations": operations}, ok([inserted["data"], two["data"], reversal, {"updated": 1}])), ({"op": "batch", "operations": operations}, ok([{"inserted": 0, "duplicates": 1}, replay_two["data"], reversal, {"updated": 1}])), ({"op": "list"}, ok(final_rows))])
        add("batch_late_conflict", [initial, ({"op": "batch", "operations": [transfer, {"op": "relabel", "ids": ["a"], "category": "changed"}, {"op": "import", "rows": [row(amount="9")]}]}, err("conflict")), unchanged])
        add("batch_late_invalid_amount", [({"op": "batch", "operations": [{"op": "import", "rows": [row("new")]}, {"op": "import", "rows": [row("bad", "NaN")]}]}, err("invalid_amount")), ({"op": "list"}, ok([]))])
        add("batch_invalid_after_write", [({"op": "batch", "operations": [{"op": "import", "rows": [row("new")]}, {"op": "unknown"}]}, err("invalid_batch")), ({"op": "list"}, ok([]))])
        one("batch_empty", {"op": "batch", "operations": []}, ok([]))
        for i, operations in enumerate([[{"op": "batch", "operations": []}], [{"op": "list"}], [{"op": "import", "db": "forbidden.sqlite", "rows": []}], [{"op": "unknown"}], [None], "bad"]):
            add("batch_invalid_" + str(i), [({"op": "batch", "operations": operations}, err("invalid_batch")), ({"op": "list"}, ok([]))])

    if stage >= 25:
        patched = norm(cents=-7, category="new")
        patch = {"op": "patch", "id": "a", "expected": a, "changes": {"cents": -7, "category": "new"}}
        add("patch_compare_and_swap", [initial, (patch, ok(patched)), ({**patch, "changes": {"cents": 8}}, err("conflict")), ({"op": "list"}, ok([patched])), ({**patch, "expected": patched, "changes": {"cents": 9}}, ok(norm(cents=9, category="new"))), ({"op": "list"}, ok([norm(cents=9, category="new")]))])
        one("patch_not_found", patch, err("not_found"))
        for i, changes in enumerate([{}, {"category": ""}, {"category": None}, {"category": 5}, {"cents": True}, {"cents": "5"}, {"cents": 1.5}, {"date": "2024-02-01"}, {"cents": 2, "id": "other"}, []]):
            add("patch_invalid_" + str(i), [initial, ({**patch, "changes": changes}, err("invalid_patch")), unchanged])
        add("batch_patch_late_failure", [initial, ({"op": "batch", "operations": [patch, {"op": "relabel", "ids": ["missing"], "category": "new"}]}, err("not_found")), unchanged])
        add("batch_patch_sequential", [initial, ({"op": "batch", "operations": [patch, {**patch, "expected": patched, "changes": {"cents": 2}}]}, ok([patched, norm(cents=2, category="new")])), ({"op": "list"}, ok([norm(cents=2, category="new")]))])

    if stage >= 26:
        unicode_rows = [norm("早", -5, date="2024-01-01", account="银行", category="餐饮"), norm("晚", 9007199254740993)]
        snapshot = _snapshot(unicode_rows)
        one("snapshot_empty", {"op": "snapshot"}, ok(_snapshot([])))
        add("snapshot_unicode", [({"op": "import", "rows": [row("晚", "90071992547409.93"), row("早", "-0.05", date="2024-01-01", account="银行", category="餐饮")]}, two), ({"op": "snapshot"}, ok(snapshot)), ({"op": "snapshot"}, ok(snapshot)), ({"op": "list"}, ok(unicode_rows))])

    if stage >= 27:
        restored_rows = [norm("新", -3, date="2024-01-01", account="银行", category="餐饮"), norm("z", 9007199254740993)]
        snapshot = _snapshot(restored_rows)
        add("restore_replace_replay", [initial, ({"op": "restore", "snapshot": snapshot}, ok({"restored": 2})), ({"op": "list"}, ok(restored_rows)), ({"op": "snapshot"}, ok(snapshot)), ({"op": "restore", "snapshot": snapshot}, ok({"restored": 2})), ({"op": "list"}, ok(restored_rows))])
        add("restore_clear", [initial, ({"op": "restore", "snapshot": _snapshot([])}, ok({"restored": 0})), ({"op": "list"}, ok([])), ({"op": "snapshot"}, ok(_snapshot([])))])
        invalid_rows = {
            "duplicate_id": [norm("x"), norm("x", 1)],
            "bool_cents": [norm("new"), norm("bad", True)],
            "float_cents": [norm(cents=1.5)],
            "string_cents": [norm(cents="1")],
            "date": [norm("new"), norm("bad", date="2024-02-30")],
            "currency_lowercase": [norm(currency="cny")],
            "currency_unicode": [norm(currency="ＣＮＹ")],
            "empty_id": [norm("")],
            "empty_account": [norm(account="")],
            "empty_category": [norm(category="")],
            "missing_field": [{key: value for key, value in a.items() if key != "category"}],
            "extra_field": [{**a, "amount": "12.34"}],
        }
        for name, rows in invalid_rows.items():
            # 摘要正确而行内容错误，不能靠只比较摘要侥幸通过。
            payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            bad = {"rows": rows, "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest()}
            add("restore_invalid_" + name, [initial, ({"op": "restore", "snapshot": bad}, err("invalid_snapshot")), unchanged])
        for name, bad in [("digest", {**snapshot, "sha256": "0" * 64}), ("digest_uppercase", {**snapshot, "sha256": snapshot["sha256"].upper()}), ("rows_type", {"rows": {}, "sha256": "0" * 64}), ("missing_digest", {"rows": []}), ("shape", None)]:
            add("restore_invalid_" + name, [initial, ({"op": "restore", "snapshot": bad}, err("invalid_snapshot")), unchanged])

    if stage >= 28:
        technical = {"id": "unit", "kind": "technical", "status": "PASS", "count": 17}
        external = {"id": "external", "kind": "review", "status": "FAIL", "reason": "人工审查发现风险", "evidence": ["issue-7"]}
        one("gate_external_fail", {"op": "release_gate", "required": [{"id": "unit", "kind": "technical"}, {"id": "external", "kind": "review"}], "checks": [technical, external]}, ok({"technical_status": "PASS", "release_status": "FAIL", "checks": [external, technical], "missing": []}))
        required = [{"id": "z-tech", "kind": "technical"}, {"id": "a-review", "kind": "review"}, {"id": "unit", "kind": "technical"}]
        one("gate_missing", {"op": "release_gate", "required": required, "checks": [technical]}, ok({"technical_status": "UNVERIFIED", "release_status": "UNVERIFIED", "checks": [technical], "missing": ["a-review", "z-tech"]}))
        one("gate_missing_review_only", {"op": "release_gate", "required": [{"id": "review", "kind": "review"}], "checks": [technical]}, ok({"technical_status": "PASS", "release_status": "UNVERIFIED", "checks": [technical], "missing": ["review"]}))
        one("gate_empty", {"op": "release_gate", "required": [], "checks": []}, ok({"technical_status": "UNVERIFIED", "release_status": "UNVERIFIED", "checks": [], "missing": []}))
        only_review = {"id": "review", "kind": "review", "status": "PASS"}
        one("gate_review_only", {"op": "release_gate", "required": [], "checks": [only_review]}, ok({"technical_status": "UNVERIFIED", "release_status": "PASS", "checks": [only_review], "missing": []}))
        for name, statuses, expected in [("blocked", ["UNVERIFIED", "BLOCKED", "PASS"], "BLOCKED"), ("fail", ["BLOCKED", "FAIL", "UNVERIFIED"], "FAIL"), ("unverified", ["PASS", "UNVERIFIED"], "UNVERIFIED")]:
            checks = [{"id": str(i), "kind": "technical", "status": status} for i, status in enumerate(statuses)]
            one("gate_priority_" + name, {"op": "release_gate", "required": [], "checks": checks[::-1]}, ok({"technical_status": expected, "release_status": expected, "checks": checks, "missing": []}))
        for name, required, checks in [
            ("duplicate_required", [{"id": "x", "kind": "technical"}] * 2, []),
            ("duplicate_check", [], [technical, technical]),
            ("required_kind", [{"id": "x", "kind": "other"}], []),
            ("check_kind", [], [{**technical, "kind": "other"}]),
            ("status", [], [{**technical, "status": "SKIP"}]),
            ("kind_conflict", [{"id": "unit", "kind": "review"}], [technical]),
        ]:
            one("gate_invalid_" + name, {"op": "release_gate", "required": required, "checks": checks}, err("invalid_review"))

    if stage >= 29:
        journal = [row("c-out", "-1.005"), row("c-in", "1.00"), row("u-out", "-90071992547409.93", currency="USD"), row("u-in", "90071992547409.93", currency="usd")]
        expected_rows = [norm("c-in", 100), norm("c-out", -100), norm("u-in", 9007199254740993, currency="USD"), norm("u-out", -9007199254740993, currency="USD")]
        add("settle_replay", [({"op": "settle", "rows": journal}, ok({"inserted": 4, "duplicates": 0})), ({"op": "settle", "rows": journal}, ok({"inserted": 0, "duplicates": 4})), ({"op": "list"}, ok(expected_rows))])
        invalid = [
            ("cross_currency", [row("x", "1", currency="CNY"), row("y", "-1", currency="USD")], "invalid_journal"),
            ("rounded_unbalanced", [row("x", "1.015"), row("y", "-1.005")], "invalid_journal"),
            ("rounded_zero", [row("x", "0.005"), row("y", "-0.005")], "invalid_journal"),
            ("one_id", [row("x", "1"), row("x", "-1")], "invalid_journal"),
            ("single", [row("x", "1")], "invalid_journal"),
            ("empty", [], "invalid_journal"),
            ("zero_member", [row("x", "1"), row("y", "-1"), row("z", "0")], "invalid_journal"),
            ("amount", [row("x", "1"), row("y", "NaN")], "invalid_amount"),
            ("date", [row("x", "1"), row("y", "-1", date="2024-02-30")], "invalid_date"),
            ("row", [row("x", "1"), row("y", "-1", account="")], "invalid_row"),
        ]
        for name, rows, error in invalid:
            add("settle_invalid_" + name, [initial, ({"op": "settle", "rows": rows}, err(error)), unchanged])
        add("settle_conflict_rollback", [initial, ({"op": "settle", "rows": [row("new", "1"), row("a", "-1")]}, err("conflict")), unchanged])
    return deepcopy(groups)


_HEX = re.compile(r"[0-9a-f]{64}\Z")


def delivery_failures(workspace, fixture_sha256):
    """核对清单与当前磁盘；文档内容质量仍必须由主线程人工回读。"""
    root = Path(workspace).resolve()
    failures = []

    def fail(error, path=None):
        item = {"case": "delivery_manifest", "error": error}
        if path is not None:
            item["path"] = path
        failures.append(item)

    def local_file(path):
        candidate = root / path
        # 不沿目录或文件链接读取工作区外材料，也拒绝链接造成的清单别名。
        current = root
        for part in PurePosixPath(path).parts:
            current = current / part
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                fail("交付文件路径不得经过符号链接或目录联接", path)
                return None
        if not candidate.resolve().is_relative_to(root):
            fail("交付文件解析到工作区外", path)
            return None
        if not candidate.is_file():
            fail("交付文件缺失或不是普通文件", path)
            return None
        return candidate

    for path in ("docs/ARCHITECTURE.md", "docs/RECOVERY.md"):
        try:
            local_file(path)
        except (OSError, RuntimeError) as exc:
            fail("无法检查文档: " + str(exc), path)
    try:
        manifest_path = local_file("DELIVERY.json")
        if manifest_path is None:
            return failures
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        fail("无法读取有效DELIVERY.json: " + str(exc), "DELIVERY.json")
        return failures
    if not isinstance(manifest, dict):
        fail("DELIVERY.json必须是对象", "DELIVERY.json")
        return failures
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        fail("schema_version必须为整数1")
    if type(manifest.get("implemented_stages")) is not int or manifest["implemented_stages"] != 30:
        fail("implemented_stages必须为整数30")
    if manifest.get("fixture_sha256") != fixture_sha256:
        fail("fixture_sha256与受保护fixture摘要不一致")
    inventory = manifest.get("source_files")
    if not isinstance(inventory, list):
        fail("source_files必须是数组")
        return failures
    seen = set()
    paths = []
    for index, item in enumerate(inventory):
        if not isinstance(item, dict):
            fail("source_files第" + str(index) + "项必须是对象")
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path or "\\" in path or ":" in path or "\x00" in path or PurePosixPath(path).is_absolute() or any(part in ("", ".", "..") for part in path.split("/")):
            fail("source_files路径必须是工作区相对POSIX规范路径", path if isinstance(path, str) else None)
            continue
        paths.append(path)
        if path in seen:
            fail("source_files路径重复", path)
        seen.add(path)
        expected_hash = item.get("sha256")
        if not isinstance(expected_hash, str) or not _HEX.fullmatch(expected_hash):
            fail("sha256必须是64位小写十六进制字符串", path)
            continue
        try:
            candidate = local_file(path)
            if candidate is not None and hashlib.sha256(candidate.read_bytes()).hexdigest() != expected_hash:
                fail("sha256与当前文件字节不一致", path)
        except (OSError, RuntimeError) as exc:
            fail("无法校验源文件sha256: " + str(exc), path)
    if paths != sorted(paths):
        fail("source_files必须按path字典序排列")
    package = root / "ledgerlab"
    try:
        if package.is_symlink() or getattr(package, "is_junction", lambda: False)() or not package.is_dir():
            fail("ledgerlab必须是工作区内真实目录", "ledgerlab")
        else:
            # 显式遍历，不跟随目录链接，避免漏报链接目录中的未列出源码。
            pending = [package]
            source_count = 0
            while pending:
                directory = pending.pop()
                for path in sorted(directory.iterdir()):
                    relative = path.relative_to(root).as_posix()
                    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                        fail("ledgerlab源码树不得包含符号链接或目录联接", relative)
                    elif path.is_dir():
                        pending.append(path)
                    elif path.suffix == ".py" and path.is_file():
                        source_count += 1
                        if relative not in seen:
                            fail("source_files遗漏Python源文件", relative)
            if not source_count:
                fail("ledgerlab目录没有Python源文件", "ledgerlab")
    except (OSError, RuntimeError) as exc:
        fail("无法枚举ledgerlab源码: " + str(exc), "ledgerlab")
    return failures
