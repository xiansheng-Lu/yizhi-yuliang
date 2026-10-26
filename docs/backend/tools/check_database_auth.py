"""R00-02标准库自检：解析受控DDL片段并核对结构/权限，不连接数据库。

仅支持本设计使用的CREATE TABLE、简单UNIQUE索引和ALTER FK/UNIQUE子集。
不是通用SQL语法解析器，PASS不证明PostgreSQL实际接受DDL或RLS有效。
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/backend/contracts/database-auth-contract.json"


class SpecError(ValueError):
    pass


def require(value, message):
    if not value:
        raise SpecError(message)


def normalized(value):
    return re.sub(r"\s+", " ", value.strip().lower())


def close_paren(text, start):
    depth, quote, i = 0, False, start
    while i < len(text):
        char = text[i]
        if char == "'":
            if quote and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quote = not quote
        if not quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise SpecError("DDL括号未闭合")


def split_columns(text):
    result, start, i = [], 0, 0
    while i < len(text):
        if text[i] == "(":
            i = close_paren(text, i)
        elif text[i] == "'":
            i += 1
            while i < len(text):
                if text[i] == "'":
                    if i + 1 < len(text) and text[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif text[i] == ",":
            result.append(text[start:i].strip())
            start = i + 1
        i += 1
    result.append(text[start:].strip())
    return [part for part in result if part]


def column_list(value):
    return tuple(normalized(part) for part in split_columns(value))


def ddl_tables(document):
    blocks = re.findall(r"```sql\n(.*?)\n```", document, re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", "\n".join(blocks)).lower()
    tables = {}
    fks = []
    for match in re.finditer(r"create table\s+(\w+)\s*\(", sql):
        name = match[1]
        require(name not in tables, f"重复表定义{name}")
        end = close_paren(sql, match.end() - 1)
        body = sql[match.end():end]
        item = {"columns": {}, "keys": set(), "body": normalized(body), "partition": None}
        for part in split_columns(body):
            part = re.sub(r"^constraint\s+\w+\s+", "", part.strip())
            key = re.match(r"(?:primary key|unique)\s*\((.*?)\)", part, re.DOTALL)
            if key:
                item["keys"].add(column_list(key[1]))
            elif not re.match(r"(?:foreign key|check)\b", part):
                col = re.match(r"(\w+)\s+(\w+)", part)
                require(col is not None, f"{name}列无法识别：{part[:60]}")
                item["columns"][col[1]] = {"type": col[2], "definition": normalized(part)}
                if re.search(r"\b(?:primary key|unique)\b", part):
                    item["keys"].add((col[1],))
                inline_fk = re.search(r"references\s+(\w+)\s*\(([^)]+)\)", part)
                if inline_fk:
                    fks.append((name, (col[1],), inline_fk[1], column_list(inline_fk[2])))
            fk = re.match(r"foreign key\s*\(([^)]+)\)\s*references\s+(\w+)\s*\(([^)]+)\)", part)
            if fk:
                fks.append((name, column_list(fk[1]), fk[2], column_list(fk[3])))
        tail = sql[end + 1:sql.find(";", end)]
        partition = re.search(r"partition by range\s*\((\w+)\)", tail)
        if partition:
            item["partition"] = partition[1]
        tables[name] = item
    indexes = {}
    for match in re.finditer(r"create unique index\s+(\w+)\s+on\s+(\w+)\s*\(", sql):
        end = close_paren(sql, match.end() - 1)
        cols = column_list(sql[match.end():end])
        tail = sql[end + 1:sql.find(";", end)]
        indexes[match[1]] = {"table": match[2], "columns": cols, "tail": normalized(tail)}
        # 部分唯一索引/表达式索引不能充当本设计的普通FK目标。
        if "where" not in tail and all(re.fullmatch(r"\w+", c) for c in cols):
            tables[match[2]]["keys"].add(cols)
    for match in re.finditer(
        r"alter table\s+(\w+)\s+add\s+(?:constraint\s+\w+\s+)?"
        r"foreign key\s*\(([^)]+)\)\s*references\s+(\w+)\s*\(([^)]+)\)", sql
    ):
        if match[1] in tables:  # workflow表未在13册展开，另由专用模板约束规则核对。
            fks.append((match[1], column_list(match[2]), match[3], column_list(match[4])))
    return tables, fks, indexes, normalized(sql)


def validate(document, contract, reference_sql):
    tables, fks, indexes, sql = ddl_tables(document)
    global_targets = set(contract["globalReferenceTargets"])
    for source, columns, target, target_columns in fks:
        require(target in tables, f"FK目标未定义：{source}→{target}")
        require(len(columns) == len(target_columns), f"FK列数不等：{source}→{target}")
        require(target_columns in tables[target]["keys"], f"FK目标缺唯一键：{target}{target_columns}")
        for col, target_col in zip(columns, target_columns):
            require(col in tables[source]["columns"], f"FK源列缺失：{source}.{col}")
            require(target_col in tables[target]["columns"], f"FK目标列缺失：{target}.{target_col}")
            require(tables[source]["columns"][col]["type"] == tables[target]["columns"][target_col]["type"],
                    f"FK类型不符：{source}.{col}→{target}.{target_col}")
        if target not in global_targets:
            require(columns[0] == target_columns[0] == "tenant_id", f"租户FK缺tenant：{source}→{target}")
    for source, columns, target, targets in contract["requiredForeignKeys"]:
        require((source, tuple(columns), target, tuple(targets)) in fks,
                f"关键FK缺失：{source}{columns}→{target}{targets}")
    for name, table in tables.items():
        if name not in contract["unscopedTables"] + contract["nullableTenantTables"]:
            require("tenant_id" in table["columns"], f"租户列缺失{name}")
            definition = table["columns"]["tenant_id"]["definition"]
            require(table["columns"]["tenant_id"]["type"] == "bigint" and
                    ("not null" in definition or "primary key" in definition), f"租户类型/可空错误{name}")
            require((name, ("tenant_id",), "tenants", ("id",)) in fks, f"缺租户根FK{name}")
    require("tenant_id" not in tables["users"]["columns"], "全局users仍带tenant_id")
    password = tables["users"]["columns"]["password_hash"]["definition"]
    require("not null" not in password, "验证码用户被强制配置密码")
    require("varchar(40)" in tables["users"]["columns"]["display_name"]["definition"], "昵称长度偏离40")
    require("relation_kind" not in tables["memberships"]["columns"], "成员类型仍有旧定义")
    for kind in ("internal", "agency_staff", "customer"):
        require(f"'{kind}'" in tables["memberships"]["columns"]["kind"]["definition"], f"成员类型缺{kind}")
    require("interval '7 days'" in tables["sessions"]["body"], "会话无绝对寿命约束")
    require("tenant_id is null or tenant_id > 0" in tables["auth_events"]["body"], "未知邮箱审计可写假租户0")
    for table, partition in contract["partitions"].items():
        require(tables[table]["partition"] == partition, f"{table}分区字段错误")
        for key in tables[table]["keys"]:
            require(partition in key, f"{table}唯一/主键缺分区字段")
    facts_index = indexes["ux_metric_facts_biz"]
    require(facts_index["columns"] == tuple(contract["factsUniqueColumns"]), "事实可比键缺维度/顺序漂移")
    require("nulls not distinct" in facts_index["tail"], "NULL维度重跑可能重复")
    require("sku_id is null or product_id is not null" in tables["metric_facts"]["body"], "SKU可绕过所属商品FK")
    require("campaign_id is null or account_id is not null" in tables["metric_facts"]["body"], "计划可绕过账户FK")
    require("currency is not distinct from 'cny'" in tables["metric_facts"]["body"] and
            "currency is not distinct from 'bean'" in tables["metric_facts"]["body"], "金额空币种可通过CHECK")
    for role in ("app_login", "app_auth"):
        statement = re.search(rf"create role {role}\b([^;]+);", sql)
        require(statement is not None, f"运行角色缺失{role}")
        for flag in ("nologin", "noinherit", "nosuperuser", "nocreatedb", "nocreaterole", "nobypassrls"):
            require(re.search(rf"\b{flag}\b", statement[1]), f"{role}缺安全默认{flag}")
        require(contract["roles"][role]["bypassRls"] is False, f"{role}配置允许BYPASSRLS")
        require(contract["roles"][role]["setRole"] == [], f"{role}配置允许SET ROLE")
        require(contract["roles"][role]["directAuthTables"] == [], f"{role}可直读认证表")
        require(not re.search(rf"grant (?:app_owner|auth_fn_owner) to {role}\b", sql), f"{role}继承特权角色")
    allowed_auth_tables = {"users", "tenants", "memberships", "sessions", "email_codes", "auth_challenges", "auth_rate_limits", "auth_events"}
    owner_statement = re.search(r"create role auth_fn_owner\b([^;]+);", sql)
    require(owner_statement is not None and "nologin" in owner_statement[1] and
            "nosuperuser" in owner_statement[1] and re.search(r"\bbypassrls\b", owner_statement[1]), "认证所有者角色属性错误")
    require(contract["roles"]["auth_fn_owner"]["loginAtRuntime"] is False and
            contract["roles"]["auth_fn_owner"]["ownsBusinessTables"] is False, "认证所有者可直接登录或拥有业务表")
    require(set(contract["roles"]["auth_fn_owner"]["tables"]) == allowed_auth_tables, "认证所有者超出认证表白名单")
    security = contract["functionSecurity"]
    require(security["securityDefiner"] is True and security["sqlIdentifiersFromCaller"] is False and
            security["trustsBackendPasswordVerification"] is True, "认证函数信任边界或调用方式错误")
    require(security["publicExecute"] is False and security["creationAndGrantSameTransaction"] is True, "认证函数PUBLIC权限或发布窗口错误")
    require(security["searchPath"] == ["pg_catalog", "pg_temp"], "不安全search_path")
    ref = normalized(re.sub(r"--[^\n]*", "", reference_sql))
    require("security definer set search_path = pg_catalog, pg_temp" in ref, "参考函数未固定安全search_path")
    require("revoke all on function auth.resolve_session(text) from public, app_login" in ref, "未撤销PUBLIC函数访问")
    require("grant execute on function auth.resolve_session(text) to app_auth" in ref, "函数未向准确调用方授权")
    require(ref.index("begin;") < ref.index("create or replace function") < ref.index("revoke all on function") < ref.index("commit;"), "函数发布与REVOKE不是同事务")
    for condition in ("s.revoked_at is null", "s.expires_at > pg_catalog.statement_timestamp()",
                      "s.absolute_expires_at > pg_catalog.statement_timestamp()", "m.status = 'active'",
                      "u.status = 'active'", "t.status = 'active'"):
        require(condition in ref, f"会话解析漏守卫：{condition}")
    output = ref.split("returns table (", 1)[1].split(") language", 1)[0]
    require(not any(secret in output for secret in ("password", "token_hash", "csrf")), "认证函数回显凭据")
    for grant in re.finditer(r"grant\s+(.+?)\s+on\s+(.*?)\s+to auth_fn_owner", ref):
        objects = grant[2]
        if objects.startswith("sequence "):
            require(objects == "sequence public.auth_events_id_seq", "认证角色获得无关序列")
            continue
        require(all(part.strip().removeprefix("public.") in allowed_auth_tables for part in objects.split(",")),
                "参考SQL向认证所有者授了无关对象权限")
    for needed in ("generated always as (nullif(tenant_id, 0)) stored references tenants(id)",
                   "not is_system and tenant_id > 0", "template_tenant_id = 0 or template_tenant_id = tenant_id",
                   "foreign key (template_tenant_id, template_id) references workflow_templates(tenant_id, id)",
                   "alter table workflow_templates force row level security"):
        require(needed in sql, f"系统模板约束不完整：{needed}")
    provided, seen, stage_of, ancestors = set(), set(), {}, {}
    for stage in contract["migrations"]:
        require(set(stage["depends"]) <= seen, f"迁移阶段循环/目标未就绪{stage['id']}")
        ancestors[stage["id"]] = set(stage["depends"])
        for dep in stage["depends"]:
            ancestors[stage["id"]] |= ancestors[dep]
        for table in stage["tables"]:
            require(table not in provided, f"迁移重复创建{table}")
            provided.add(table)
            stage_of[table] = stage["id"]
        seen.add(stage["id"])
    require(provided == set(tables), "迁移清单未覆盖实际DDL表或声明了不存在的表")
    ordered_tables = [name for stage in contract["migrations"] for name in stage["tables"]]
    order = {name: i for i, name in enumerate(ordered_tables)}
    for source, _, target, _ in fks:
        if source == target:
            continue
        require(order[target] < order[source], f"表创建先于FK目标{source}→{target}")
        if stage_of[source] != stage_of[target]:
            require(stage_of[target] in ancestors[stage_of[source]], f"迁移漏真实技术依赖{source}→{target}")
    require("P08" not in ancestors["P09"] and "P08" not in ancestors["P_AI"], "核心SSE/调用账本错误等待内容域")
    plan = (ROOT / "docs/backend/architecture/20-后端逐任务开发与验收卡.md").read_text(encoding="utf-8")
    task_ids = set(re.findall(r"^### (?:R00-\d{2}|M[0-5]-\d{2}[a-z]?)$", plan, re.MULTILINE))
    task_ids = {name.removeprefix("### ") for name in task_ids}
    for stage in contract["migrations"]:
        require(set(stage["tasks"]) <= task_ids, f"迁移引用未知任务{stage['id']}")
    for entry in contract["deferredReferences"]:
        require(entry["task"] in task_ids and entry["rule"], f"后续引用无任务/校验决定{entry}")
        require(entry["table"] in tables, f"后续引用未知表{entry['table']}")
    event = contract["eventCursor"]
    require(event["ordering"] == ["LOCK_TENANT_HEAD", "DEDUP_SOURCE_EVENT", "INCREMENT_HEAD", "INSERT_PROJECTION", "COMMIT_TOGETHER", "NOTIFY_AFTER_COMMIT"], "水位分配/发布顺序错误")
    require(event["globalIdentityIsCursor"] is False and event["advanceToHeadOnlyWhenPageExhausted"] is True, "游标可能跳过未提交/未发事件")
    require({"tenantId", "membershipId", "authzVersion", "scopeHash", "tenantSeq"} <= set(event["bindings"]), "游标缺授权范围绑定")
    use_case = contract["sharedUseCase"]
    require(use_case["implementation"] == "ONE_SYNC_SESSION_USE_CASE" and use_case["api"] == "AsyncSession.run_sync" and use_case["worker"] == "DIRECT_SAME_USE_CASE", "API与Worker可能复制业务用例")
    require(use_case["externalIoInTransaction"] is False and use_case["connectionSharedAcrossProcesses"] is False, "外调事务或跨进程连接违反决定")
    require(len(contract["functions"]) == len({f['name'] for f in contract['functions']}) == 13, "认证函数面重复或不完整")
    spec_document = (ROOT / "docs/backend/contracts/R00-02-数据库与认证规格.md").read_text(encoding="utf-8")
    documented_functions = {}
    for match in re.finditer(r"^\| (auth\.\w+) \| ([^|]+) \|", spec_document, re.MULTILINE):
        documented_functions[match[1]] = re.findall(r"\b(?:text|uuid|integer)\b", match[2])
    require(documented_functions == {f["name"]: f["args"] for f in contract["functions"]}, "文档与机器清单的认证签名不一致")
    email_guards = next(f["guards"] for f in contract["functions"] if f["name"] == "auth.email_session")
    require({"CODE_ROW_LOCK", "UNUSED", "CONSUME_AND_SESSION_ATOMIC", "FAILED_COUNT_COMMITTED"} <= set(email_guards), "邮件认证缺并发/次数守卫")
    require(all(case["state"] == "PLANNED_REAL_TEST" for case in contract["acceptanceCases"]), "规格检查冒充真实验收")
    require(all(case.get(field) for case in contract["acceptanceCases"] for field in ("setup", "action", "expected", "evidence")), "后续真实用例缺输入/操作/预期/证据")
    return {"result": "PASS", "layer": "D_STATIC_SPEC_ONLY", "tables": len(tables), "foreignKeys": len(fks), "migrationStages": len(seen), "authFunctions": len(contract["functions"]), "referenceSqlFunctionBodies": 1, "plannedRealCases": len(contract["acceptanceCases"])}


def negative_checks(document, contract, reference):
    cases = []
    def doc_case(name, old, new):
        require(old in document, f"负例输入未找到{name}")
        cases.append((name, document.replace(old, new, 1), contract, reference))
    def contract_case(name, mutate):
        value = copy.deepcopy(contract)
        mutate(value)
        cases.append((name, document, value, reference))
    doc_case("跨身份会话FK丢失", "foreign key (tenant_id, membership_id, user_id) references memberships(tenant_id, id, user_id)", "check (version > 0)")
    doc_case("目标三列唯一键丢失", "unique (tenant_id, id, user_id)", "check (authz_version > 0)")
    doc_case("旧单列租户FK复发", "order_id              uuid not null,", "order_id              uuid not null references execution_orders(id),")
    doc_case("FK类型错误", "membership_id   uuid not null,", "membership_id   bigint not null,")
    doc_case("审计分区PK非法", "primary key (id, created_at)", "primary key (id)")
    doc_case("NULL维度重复", "nulls not distinct;", ";")
    doc_case("强迫验证码用户有密码", "password_hash   varchar(255) null", "password_hash   varchar(255) not null")
    doc_case("全局用户强绑租户", "-- 全局身份不带tenant_id；成员关系是唯一业务租户归属来源。", "tenant_id bigint not null references tenants(id),")
    doc_case("运行角色绕过RLS", "create role app_login nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;", "create role app_login nologin noinherit nosuperuser nocreatedb nocreaterole bypassrls;")
    doc_case("SKU绕过商品关联", "check (sku_id is null or product_id is not null)", "check (true)")
    doc_case("金额币种为空绕过CHECK", "currency is not distinct from 'CNY'", "currency = 'CNY'")
    contract_case("认证所有者获得业务表", lambda c: c["roles"]["auth_fn_owner"]["tables"].append("campaigns"))
    contract_case("认证所有者直接登录", lambda c: c["roles"]["auth_fn_owner"].update(loginAtRuntime=True))
    contract_case("业务角色直读密码表", lambda c: c["roles"]["app_login"]["directAuthTables"].append("users"))
    contract_case("PUBLIC可调用认证函数", lambda c: c["functionSecurity"].update(publicExecute=True))
    contract_case("事实键漏币种", lambda c: c["factsUniqueColumns"].remove("currency"))
    contract_case("事件先通知再提交", lambda c: c["eventCursor"]["ordering"].reverse())
    contract_case("使用全局ID游标", lambda c: c["eventCursor"].update(globalIdentityIsCursor=True))
    contract_case("满页提前跳head", lambda c: c["eventCursor"].update(advanceToHeadOnlyWhenPageExhausted=False))
    contract_case("事务内外部付费IO", lambda c: c["sharedUseCase"].update(externalIoInTransaction=True))
    contract_case("静态检查冒充真实PASS", lambda c: c["acceptanceCases"][0].update(state="PASS"))
    contract_case("核心SSE反向等待内容", lambda c: next(s for s in c["migrations"] if s["id"] == "P09")["depends"].append("P08"))
    contract_case("邮件码错误次数回滚", lambda c: next(f for f in c["functions"] if f["name"] == "auth.email_session")["guards"].remove("FAILED_COUNT_COMMITTED"))
    cases.append(("不安全search_path", document, contract, reference.replace("search_path = pg_catalog, pg_temp", "search_path = public, pg_temp")))
    cases.append(("遗漏绝对到期", document, contract, reference.replace("and s.absolute_expires_at > pg_catalog.statement_timestamp()", "")))
    results = []
    for name, doc, spec, ref in cases:
        try:
            validate(doc, spec, ref)
        except SpecError as error:
            results.append({"case": name, "result": "REJECTED", "reason": str(error)})
            continue
        raise SpecError(f"负例未被拦截：{name}")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document = (ROOT / contract["ddlSource"]).read_text(encoding="utf-8")
    reference = (ROOT / contract["referenceSql"]).read_text(encoding="utf-8")
    report = validate(document, contract, reference)
    report["sourceSha256Utf8Lf"] = {
        name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        for name in (contract["ddlSource"], contract["referenceSql"],
                     "docs/backend/contracts/database-auth-contract.json",
                     "docs/backend/contracts/R00-02-数据库与认证规格.md")
    }
    if options.self_test:
        report["negativeMutationResults"] = negative_checks(document, contract, reference)
        report["negativeMutationsRejected"] = len(report["negativeMutationResults"])
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if options.report:
        options.report.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
