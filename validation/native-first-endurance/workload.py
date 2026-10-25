"""长时真实编码任务；期望由验收进程持有，不放入模型工作目录。"""
from copy import deepcopy

BASE = """实现纯标准库 Python 财务流水项目 ledgerlab，启动方式 python -m ledgerlab。
每次进程从 stdin 读取一个 JSON 对象，stdout 只输出一个 JSON 对象；成功 {\"ok\":true,\"data\":...}，
业务错误 {\"ok\":false,\"error\":错误码}，两种情况均退出0。输入 op 决定操作。
交易原始字段为 id,date(YYYY-MM-DD),account,amount(十进制字符串),currency,category；
规范交易字段为 id,date,account,cents(整数),currency,category（不保留 amount）。
缺省 category 为 uncategorized；currency 大写。每个阶段保留全部旧接口和契约。
不得访问网络、环境凭据或工作区外文件；唯一例外是验收请求传入的 db 路径，只能作为 SQLite 数据库使用。
不要修改独立验收器、fixture、控制文件。不要建立自己的假验收结果。
使用 Read/Write/Edit/Glob/Grep 调查并真实编辑代码，用获准的唯一 Bash 验收命令验证。
不要运行其他 shell 命令或安装依赖。代码应有实际多模块职责分离，不是硬编码样例。
"""

STAGES = [
    ("decimal_money", "修复 normalize：输入 rows 原始交易列表，输出同序规范交易列表。金额必须用精确十进制，ROUND_HALF_EVEN 舍入到分；拒绝非有限数、bool 或非字符串金额(error=invalid_amount)。非法日历日期 error=invalid_date。id/account 不得为空，currency 必须3个ASCII字母，否则 invalid_row；错误时整个请求失败。"),
    ("group_summary", "新增 summarize：输入规范 rows；按 currency,account,category 汇总 cents/count，输出按这三个字段字典序排列的对象列表。空列表返回[]；正负数都应精确累计。"),
    ("csv_ingestion", "新增 parse_csv：输入 text 为带表头的CSV，允许UTF-8 BOM、CRLF、引号内逗号和空白行；列至少含id,date,account,amount,currency，category可选，额外列忽略。返回规范交易；缺列 error=invalid_csv。"),
    ("sqlite_persistence", "新增 import：输入 db 路径及原始 rows，持久化规范交易，返回 {inserted:N,duplicates:N}。同id同规范内容重放为duplicates；同id不同内容 error=conflict。新增 list：输入 db，返回按date,id排序的全部规范交易。跨进程持久化；不存在数据库时创建空库。"),
    ("atomic_import", "修复批导入事务边界：同批或已存库id冲突，以及任意坏金额/日期，整个 import 必须回滚，不能保留前面的有效行；同批完全相同记录只插入一次。错误码继续沿用。"),
    ("query_filters", "扩展 list：可选 account,currency,category,from_date,to_date，全部AND，日期含端点；currency查询大小写不敏感。from_date>to_date返回 invalid_range，非法日期 invalid_date。"),
    ("atomic_transfer", "新增 transfer：db,id,date,from_account,to_account,amount,currency；正金额且不同账户，生成id:out和id:in两行，category=transfer，分值分别负/正，返回 {inserted:2,duplicates:0}。完全重放返回{inserted:0,duplicates:2}。无效金额或同账户 error=invalid_transfer；冲突原子回滚。"),
    ("reversal", "新增 reverse：db,id(原交易id),reversal_id,date；生成反向分值、原account/currency/category的新交易。返回规范新交易；原记录不删除。原id不存在 error=not_found；重复同reversal_id同内容幂等返回同交易，不同内容 conflict。"),
    ("running_balances", "新增 balances：输入 db，可选 account,currency,from_date,to_date；按date,id排序返回交易对象并增加 balance_cents。每个(account,currency)独立累计，from_date之前交易计入期初但不返回；to_date之后不参与。"),
    ("budget_report", "新增 budget：输入规范 rows 和 limits(对象数组，每个含currency,category,limit_cents)。支出为负cents绝对值之和，收入不抵扣支出；输出按currency,category排序的 {currency,category,limit_cents,spent_cents,remaining_cents} 列表，无交易限额也输出。"),
    ("merge_batches", "新增 merge：输入 batches 为多个规范交易数组。按id合并去重，相同id不同内容 conflict；输出按date,id排序的规范交易数组，空批可用。不得更改传入对象。"),
    ("csv_export", "新增 export_csv：输入规范 rows，输出字符串：表头 id,date,account,amount,currency,category；按date,id排序，金额固定两位小数，换行固定\\n，CSV正确引号转义，最后保留换行。输出能通过 parse_csv 无损重导入。"),
    ("reconciliation", "新增 reconcile：输入 expected 和 actual 两个规范交易列表，各自id唯一；输出 {missing:[id],unexpected:[id],changed:[id]}，各数组字典序排列。相同id任何字段差异算changed；任一输入id重复 error=duplicate_id。"),
    ("stable_pagination", "扩展 list：可选 offset(缺省0),limit(缺省不限制)，必须非bool的非负整数，否则 invalid_pagination。在全部过滤和date,id排序之后分页，limit=0返回[]。保持之前返回格式。"),
    ("storage_migration", "兼容旧库：旧 transactions 表列 id TEXT PRIMARY KEY,date TEXT,account TEXT,amount TEXT,currency TEXT,category TEXT；在任意list/import访问时自动迁移为规范cents存储。旧数据必须用与normalize相同精度规则；迁移可重复访问，旧交易不丢失，随后可导入新记录。"),
    ("module_refactor", "完成结构重构：至少三个有真实函数或类的非__init__/__main__模块，分别承担金额/规范化、存储、报表或CSV职责；__main__只做协议/分发，最多160行。全部旧契约仍通过。不要靠复制无用函数或空模块满足结构。"),
    ("monthly_report", "新增 monthly：输入规范 rows，按date前7位month,currency汇总 income_cents(非负分值之和),expense_cents(负分值绝对值之和),net_cents，输出按month,currency排序数组。空输入[]。"),
    ("category_relabel", "新增 relabel：输入db,ids(交易id数组),category。所有id必须存在，否则not_found且整个操作不修改任何行；category空串invalid_row。重复id只计一次，返回 {updated:N}；持久化且不改变金额和排序。"),
    ("account_statement", "新增 statement：输入db,account,currency,from_date,to_date(必需)，返回{opening_cents,income_cents,expense_cents,closing_cents,count}，期初为from_date之前的余额，区间含端点，count为区间交易数量。沿用日期和范围校验；无流水所有数值0。"),
    ("daily_cashflow", "新增 daily：输入规范rows,from_date,to_date,currency(必需)，为范围内每个日历日输出{date,income_cents,expense_cents,net_cents}，无流水日期补0，忽略其他币种及范围外流水，按日期升序；范围/日期错误沿用旧错误码。")
]


def row(id="a", amount="12.34", date="2024-01-02", account="cash", currency="CNY", category="food"):
    return dict(id=id, amount=amount, date=date, account=account, currency=currency, category=category)


def norm(id="a", cents=1234, **kwargs):
    item = row(id=id, **kwargs)
    item.pop("amount")
    item["cents"] = cents
    return item


def ok(value):
    return {"ok": True, "data": value}


def err(value):
    return {"ok": False, "error": value}


def cases(stage):
    """每组独立数据库；同组步骤共享库，全部期望在受信父进程比较。"""
    groups = []
    def add(name, steps):
        groups.append({"name": name, "steps": steps})
    def one(name, request, expected):
        add(name, [(request, expected)])
    a = norm()
    b = norm("b", -205, date="2024-02-01")
    original = row()
    inserted = ok({"inserted": 1, "duplicates": 0})
    one("money_ties", {"op": "normalize", "rows": [row(str(i), x) for i, x in enumerate(["1.005", "1.015", "-1.005", "90071992547409.93", "0.29"])]}, ok([norm(str(i), n) for i, n in enumerate([100, 102, -100, 9007199254740993, 29])]))
    one("currency_default", {"op": "normalize", "rows": [{k:v for k,v in row(currency="usd").items() if k != "category"}]}, ok([norm(currency="USD", category="uncategorized")]))
    for value in ["NaN", "Infinity", True, 1.25]:
        one("invalid_amount_" + str(value), {"op": "normalize", "rows": [row(amount=value)]}, err("invalid_amount"))
    one("invalid_date", {"op": "normalize", "rows": [row(date="2023-02-29")]}, err("invalid_date"))
    one("leap_date", {"op": "normalize", "rows": [row(date="2024-02-29")]}, ok([norm(date="2024-02-29")]))
    for field, value in [("id", ""), ("account", ""), ("currency", "中文元")]:
        bad = row(); bad[field] = value
        one("invalid_" + field, {"op": "normalize", "rows": [bad]}, err("invalid_row"))
    if stage >= 2:
        one("summary", {"op": "summarize", "rows": [a, b, norm("u", 10, currency="USD")]}, ok([dict(currency="CNY", account="cash", category="food", cents=1029, count=2), dict(currency="USD", account="cash", category="food", cents=10, count=1)]))
        one("summary_empty", {"op": "summarize", "rows": []}, ok([]))
    if stage >= 3:
        one("csv_bom_quotes", {"op": "parse_csv", "text": '\ufeffid,date,account,amount,currency,category,ignored\r\na,2024-01-02,cash,12.34,cny,"food,drink",x\r\n\r\n'}, ok([norm(category="food,drink")]))
        one("csv_missing", {"op": "parse_csv", "text": "id,amount\na,1.00\n"}, err("invalid_csv"))
    if stage >= 4:
        add("persistent_replay", [({"op":"import", "rows":[original]}, inserted), ({"op":"import", "rows":[original]}, ok({"inserted":0,"duplicates":1})), ({"op":"import", "rows":[row(amount="2")]}, err("conflict")), ({"op":"list"}, ok([a]))])
        one("empty_database", {"op":"list"}, ok([]))
    if stage >= 5:
        for reason, bad in [("conflict", row("x", "2")), ("invalid_amount", row("y", "NaN")), ("invalid_date", row("y", date="2024-02-30"))]:
            add("rollback_"+reason, [({"op":"import","rows":[row("x"), bad]}, err(reason)), ({"op":"list"}, ok([]))])
        add("same_batch_duplicate", [({"op":"import","rows":[original, original]}, ok({"inserted":1,"duplicates":1})), ({"op":"list"}, ok([a]))])
    if stage >= 6:
        add("filter_intersection", [({"op":"import","rows":[original,row("b","-2.05",date="2024-02-01"),row("u",currency="USD")]}, ok({"inserted":3,"duplicates":0})), ({"op":"list","currency":"cny","account":"cash","category":"food","from_date":"2024-01-02","to_date":"2024-01-02"}, ok([a]))])
        one("invalid_range", {"op":"list","from_date":"2024-02-01","to_date":"2024-01-01"}, err("invalid_range"))
    if stage >= 7:
        transfer = dict(op="transfer",id="t",date="2024-01-02",from_account="cash",to_account="bank",amount="1.25",currency="CNY")
        entries = [norm("t:in",125,account="bank",category="transfer"),norm("t:out",-125,category="transfer")]
        add("transfer_replay", [(transfer,ok({"inserted":2,"duplicates":0})), (transfer,ok({"inserted":0,"duplicates":2})), ({"op":"list"},ok(entries))])
        one("invalid_transfer", {**transfer,"to_account":"cash"},err("invalid_transfer"))
        add("transfer_conflict_atomic", [({"op":"import","rows":[row("t:in")]},inserted), (transfer,err("conflict")), ({"op":"list"},ok([norm("t:in")]))])
    if stage >= 8:
        reverse = dict(op="reverse",id="a",reversal_id="r",date="2024-02-01")
        r = norm("r",-1234,date="2024-02-01")
        add("reverse_idempotent", [({"op":"import","rows":[original]},inserted),(reverse,ok(r)),(reverse,ok(r)),({"op":"list"},ok([a,r]))])
        one("reverse_missing", reverse,err("not_found"))
    if stage >= 9:
        add("opening_balance", [({"op":"import","rows":[original,row("b","-2.05",date="2024-02-01"),row("u","7",date="2024-02-01",currency="USD")]},ok({"inserted":3,"duplicates":0})), ({"op":"balances","from_date":"2024-02-01","to_date":"2024-02-01"},ok([{**b,"balance_cents":1029},{**norm("u",700,date="2024-02-01",currency="USD"),"balance_cents":700}]))])
    if stage >= 10:
        one("budget_gross_expenses", {"op":"budget","rows":[a,b],"limits":[dict(currency="USD",category="rent",limit_cents=500),dict(currency="CNY",category="food",limit_cents=200)]}, ok([dict(currency="CNY",category="food",limit_cents=200,spent_cents=205,remaining_cents=-5),dict(currency="USD",category="rent",limit_cents=500,spent_cents=0,remaining_cents=500)]))
    if stage >= 11:
        one("merge_sorted", {"op":"merge","batches":[[b,a],[],[a]]},ok([a,b]))
        one("merge_conflict", {"op":"merge","batches":[[a],[{**a,"cents":1}]]},err("conflict"))
    if stage >= 12:
        one("csv_export_quoting", {"op":"export_csv","rows":[b,norm(category='food,"drink"')]},ok('id,date,account,amount,currency,category\na,2024-01-02,cash,12.34,CNY,"food,""drink"""\nb,2024-02-01,cash,-2.05,CNY,food\n'))
        one("csv_export_empty", {"op":"export_csv","rows":[]},ok("id,date,account,amount,currency,category\n"))
    if stage >= 13:
        one("reconcile_changes", {"op":"reconcile","expected":[a,b],"actual":[{**a,"cents":1},norm("z")]},ok(dict(missing=["b"],unexpected=["z"],changed=["a"])))
        one("reconcile_duplicate", {"op":"reconcile","expected":[a,a],"actual":[]},err("duplicate_id"))
    if stage >= 14:
        add("pagination_after_sort", [({"op":"import","rows":[row("b"),original,row("c",currency="USD")]},ok({"inserted":3,"duplicates":0})),({"op":"list","currency":"CNY","offset":1,"limit":1},ok([norm("b")])),({"op":"list","limit":0},ok([]))])
        for value in [-1,True,1.5]:
            one("bad_limit_"+str(value), {"op":"list","limit":value},err("invalid_pagination"))
    if stage >= 15:
        add("legacy_migration", [({"op":"list","_legacy":True},ok([norm("legacy",102)])), ({"op":"import","rows":[original]},inserted), ({"op":"list"},ok([a,norm("legacy",102)]))])
    if stage >= 17:
        one("monthly_currencies", {"op":"monthly","rows":[a,b,norm("u",20,currency="USD")]},ok([dict(month="2024-01",currency="CNY",income_cents=1234,expense_cents=0,net_cents=1234),dict(month="2024-01",currency="USD",income_cents=20,expense_cents=0,net_cents=20),dict(month="2024-02",currency="CNY",income_cents=0,expense_cents=205,net_cents=-205)]))
    if stage >= 18:
        add("relabel_atomic", [({"op":"import","rows":[original]},inserted),({"op":"relabel","ids":["a","missing"],"category":"new"},err("not_found")),({"op":"list"},ok([a])),({"op":"relabel","ids":["a","a"],"category":"new"},ok({"updated":1})),({"op":"list"},ok([norm(category="new")]))])
    if stage >= 19:
        add("statement_opening", [({"op":"import","rows":[original,row("b","-2.05",date="2024-02-01")]},ok({"inserted":2,"duplicates":0})),({"op":"statement","account":"cash","currency":"CNY","from_date":"2024-02-01","to_date":"2024-02-29"},ok(dict(opening_cents=1234,income_cents=0,expense_cents=205,closing_cents=1029,count=1)))])
    if stage >= 20:
        one("daily_gap_filling", {"op":"daily","rows":[a,b],"from_date":"2024-01-01","to_date":"2024-01-03","currency":"CNY"},ok([dict(date="2024-01-01",income_cents=0,expense_cents=0,net_cents=0),dict(date="2024-01-02",income_cents=1234,expense_cents=0,net_cents=1234),dict(date="2024-01-03",income_cents=0,expense_cents=0,net_cents=0)]))
    return deepcopy(groups)


STARTER = {
    "ledgerlab/__init__.py": '"""本地财务流水处理。"""\n',
    "ledgerlab/money.py": '''"""已有实现：本轮需要修复小数精度和校验。"""
def normalize(rows):
    result = []
    for row in rows:
        item = dict(row)
        item["cents"] = round(float(item.pop("amount")) * 100)
        item["currency"] = item["currency"].upper()
        item.setdefault("category", "uncategorized")
        result.append(item)
    return result
''',
    "ledgerlab/__main__.py": '''import json
import sys
from .money import normalize

request = json.load(sys.stdin)
if request.get("op") == "normalize":
    response = {"ok": True, "data": normalize(request["rows"])}
else:
    response = {"ok": False, "error": "unknown_operation"}
print(json.dumps(response, ensure_ascii=False))
'''
}
