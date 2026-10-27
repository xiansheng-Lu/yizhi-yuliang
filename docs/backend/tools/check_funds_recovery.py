"""R00-03标准库自检：核对资金与恢复冻结规格、机器清单与06/12/15册的采用情况，不连接数据库。

仅做静态规格一致性核对：PASS不证明真实数据库、真实平台写入、真实恢复演练或前端联调通过。
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/backend/contracts/funds-recovery-contract.json"
SPEC = ROOT / "docs/backend/contracts/R00-03-资金与恢复规格.md"
VOLUMES = {
    "06": ROOT / "docs/backend/architecture/06-执行内核与风控设计.md",
    "12": ROOT / "docs/backend/architecture/12-核心状态机详设.md",
    "15": ROOT / "docs/backend/architecture/15-关键链路时序与事务边界.md",
}
PLAN = ROOT / "docs/backend/architecture/20-后端逐任务开发与验收卡.md"
ALLOWED_ACCEPTANCE = {f"A{index}" for index in range(17, 26)} | {"A33", "A36"}


class SpecError(ValueError):
    pass


def require(value, message):
    if not value:
        raise SpecError(message)


def normalized(value):
    return re.sub(r"\s+", " ", value.strip().lower())


def read(path):
    return path.read_text(encoding="utf-8-sig")


def decision_headings(document):
    return {match[1]: match[2] for match in re.finditer(r"^### (D-\d{2}) (.+)$", document, re.MULTILINE)}


def inbox_rows(document):
    rows = set()
    for match in re.finditer(
        r"^\| (PROCESSED|RECEIVED) \| ([A-Z0-9_]+) \| ([A-Z0-9_]+) \| ([A-Z0-9_]+(?:; [A-Z0-9_]+)*) \|", document, re.MULTILINE
    ):
        actions = tuple(part.strip() for part in match[4].split(";"))
        rows.add((match[1], match[2], match[3], actions))
    return rows


def validate(document, contract, volumes):
    # 1. 决定清单：编号连续、标题与正文一一对应、正文不得多出未登记决定
    decisions = contract["decisions"]
    require(len(decisions) == 14, f"决定数量变化：{len(decisions)}")
    expected_ids = [f"D-{index:02d}" for index in range(1, 15)]
    require([item["id"] for item in decisions] == expected_ids, "决定编号不连续或顺序变化")
    headings = decision_headings(document)
    require(set(headings) == set(expected_ids), f"正文决定标题与清单不一致：{sorted(headings)}")
    for item in decisions:
        require(item["title"] == headings[item["id"]], f"决定标题漂移{item['id']}")
        require(item["statement"].strip() and item["sources"], f"决定缺陈述或来源{item['id']}")
        require(item["acceptanceCases"], f"决定未绑定验收用例{item['id']}")

    # 2. 单一领取闸：三个竞争动作、两两优先级、共同谓词
    contention = contract["contention"]
    actions = contention["competingActions"]
    require(sorted(actions) == ["cancel", "claim", "expire"], f"竞争动作集合变化：{actions}")
    pairs = {frozenset(pair) for pair in contention["precedence"]}
    require(pairs == {frozenset(pair) for pair in [(a, b) for a in actions for b in actions if a != b]},
            "优先级未覆盖全部竞争对")
    require(contention["singleTerminalOutcome"] is True, "未保证单一终态出口")
    require(contention["loserError"]["http"] == 409, "竞争失败方未按409处理")
    predicate = normalized(contention["commonPredicate"])
    require(predicate in normalized(document), "共同条件谓词未写入规格正文")
    require("not exists (select 1 from execution_attempts" in predicate, "共同谓词缺SENT尝试条件")
    require("expires_at > now()" in predicate, "共同谓词缺未过期条件")
    require("version = :expected" in predicate, "共同谓词缺版本条件")

    # 3. 预占：创建时点、存在区间、减额与执行前边界
    reservations = contract["reservations"]
    require(reservations["createdAt"] == "confirm", "预占创建时点被改动")
    require(reservations["existingOnlyIn"] == ["PENDING_APPROVAL", "APPROVED"], "预占存在区间被放宽")
    require("DRAFT" in reservations["releaseIsNoopIn"], "DRAFT未声明为幂等空操作")
    require(reservations["decreaseCreatesReservation"] is False, "减额被允许创建预占")
    require(reservations["decreaseAdjustOnMatchedOnly"] is True, "减额未限定MATCHED后调减")
    require(reservations["newReservationBeforeExecution"] is False, "执行前被允许新建预占")
    require(reservations["states"] == ["HELD", "SETTLED", "RELEASED"], "预占状态集合变化")
    require(sorted(reservations["terminal"]) == ["RELEASED", "SETTLED"], "预占终态集合变化")
    evidence = contract["evidenceLevels"]
    for transition in reservations["transitions"]:
        require(transition["from"] in reservations["states"], f"预占转移起点非法{transition}")
        require(transition["to"] in reservations["states"], f"预占转移目标非法{transition}")
        require(transition["evidence"] in evidence, f"预占转移引用未知证据等级{transition}")
        allowed = evidence[transition["evidence"]]["allows"]
        if transition["to"] == "SETTLED":
            require("SETTLED" in allowed, f"结算使用了不允许结算的证据{transition}")
        if transition["to"] == "RELEASED":
            require("RELEASED" in allowed, f"释放使用了不允许释放的证据{transition}")
        if transition["to"] == "HELD":
            require(not allowed, f"未定性被允许结算或释放{transition}")
    terminal_sources = {item["from"] for item in reservations["transitions"]} & set(reservations["terminal"])
    require(not terminal_sources, f"预占终态仍可继续转移{terminal_sources}")

    # 4. 证据等级：E2结算、E1释放、E0全禁
    require("SETTLED" in evidence["E2"]["allows"], "E2不再允许结算")
    require("RELEASED" in evidence["E1"]["allows"], "E1不再允许释放")
    require("GUARD_RELEASE" in evidence["E1"]["allows"] and "GUARD_RELEASE" in evidence["E2"]["allows"],
            "解除隔离缺少允许证据")
    require(not evidence["E0"]["allows"], "E0仍被允许产生动作")
    for action in ("SETTLED", "RELEASED", "GUARD_RELEASE", "RESEND_WRITE", "AUTO_RETRY"):
        require(action in evidence["E0"]["forbids"], f"E0未禁止{action}")
    require(evidence["E0"]["examples"], "E0缺少反例清单")
    require(evidence["persistedEvidenceRequired"] is True, "证据未要求落库可核对")

    # 5. 守卫：UNKNOWN必隔离、租约过期不解隔离
    guard = contract["guard"]
    require(guard["unknownAlwaysQuarantines"] is True, "UNKNOWN未强制隔离守卫")
    require(guard["leaseExpiryDoesNotRelease"] is True, "租约过期被允许解除隔离")
    require(guard["writeRejectedIn"] == ["QUARANTINED"], "隔离态写拒绝范围被改动")
    require(guard["writeRejectedErrorCode"] == "TARGET_QUARANTINED", "隔离写错误码变化")
    require(guard["sameTargetSerializedByDatabase"] is True, "同目标串行未落到数据库")
    require(guard["redisLockIsAdvisoryOnly"] is True, "Redis锁被当成正确性依赖")

    # 6. UNKNOWN 投影：两种来源不重发、状态不外扩
    unknown = contract["unknown"]
    require(unknown["isTerminal"] is False, "UNKNOWN被当成终态")
    require(unknown["resendAllowed"] is False, "UNKNOWN被允许重发")
    require(unknown["publicStatusExtensionAllowed"] is False, "被允许私自扩展前端状态枚举")
    submitted = unknown["submitted"]
    readback = unknown["readback"]
    require(submitted["jobStatus"] == "EXECUTING", "提交未知投影状态被改动")
    require(submitted["accepted"] is None, "提交未知不得声称已受理")
    require(submitted["verification"] == "UNKNOWN", "提交未知核验态被改动")
    require(submitted["errorCode"] == "RESULT_UNKNOWN", "提交未知错误码被改动")
    require(submitted["retryable"] is False, "提交未知被允许重试")
    require(readback["jobStatus"] == "VERIFYING", "回读未知投影状态被改动")
    require(readback["accepted"] is True, "回读未知应保留已受理")
    require(readback["verification"] == "UNKNOWN", "回读未知核验态被改动")
    require(readback["retryable"] is False, "回读未知被允许重试")
    for branch in (submitted, readback):
        require(branch["keepsReservation"] is True, "UNKNOWN期间预占被释放")
        require(branch["quarantinesGuard"] is True, "UNKNOWN期间守卫未被隔离")
    require("RESULT_UNKNOWN" in document, "正文缺少RESULT_UNKNOWN表达")
    require("TARGET_QUARANTINED" in document, "正文缺少隔离错误码")

    # 7. inbox 恢复判定表：正文与清单逐格一致
    rules = contract["inboxRules"]
    require(rules["directAckOnlyWhenProcessed"] is True, "PROCESSED之外被允许直接ack")
    require(rules["inboxRowGrantsWriteEligibility"] is False, "inbox行被当成平台写资格")
    require(rules["recoveryScheduleIsNotWriteCall"] is True, "恢复调度被当成写平台")
    require(rules["platformWriteCountMax"] == 1, "平台写次数上限被放宽")
    rows = contract["inboxRecovery"]
    require(rows, "inbox判定表为空")
    contract_rows = {(item["inbox"], item["attempt"], item["guard"], tuple(item["actions"])) for item in rows}
    require(inbox_rows(document) == contract_rows, "正文判定表与机器清单逐格不一致")
    processed = [item for item in rows if item["inbox"] == "PROCESSED"]
    require(processed and processed[0]["actions"] == ["ACK_ONLY"], "PROCESSED行动作被改动")
    for item in rows:
        require(item["actions"], f"判定行缺动作{item}")
        if item["attempt"] in ("SENT", "UNKNOWN"):
            require("QUERY_ONLY" in item["actions"], f"在途未强制只查{item}")
            for action in item["actions"]:
                require(action not in ("RESCHEDULE_ONLY", "RESEND_WRITE", "WAIT_LEASE"),
                        f"在途被允许继续写或重投{item}")

    # 8. 外部超限：事实可入账、只对新增预占校验
    over = contract["overLimit"]
    require(over["noGlobalCheckConstraint"] is True, "账本被加回全局上限约束")
    require(over["checkScope"] == "new_reservation_only", "上限校验范围被放宽")
    require(over["checkInsideLedgerRowLock"] is True, "上限校验未在额度桶行锁内")
    require(over["allowFactOverLimit"] is True, "超限事实不再允许入账")
    require(over["approvedNotExecutedRecheck"] is True, "已批准未执行的增额不再复检")
    require(over["recheckFailAction"] == "FAILED_NO_EXECUTION + RELEASE_BY_E1 + REQUIRE_NEW_REQUEST",
            "复检不通过的处理被改动")
    require(over["newIncreaseRejectedWhenOverLimit"] is True, "超限期间新增额未被拒绝")
    require(over["decreaseOnlyOnMatched"] is True, "减额不再限定MATCHED")

    # 9. 灰度首写：六阶段、白名单、完整链路、5次MATCHED、人工许可
    first = contract["firstWrite"]
    require(first["requires"] == [
        "CAPABILITY_SIX_STAGES_ALL_PASS",
        "ACCOUNT_ACTION_AMOUNT_WHITELIST",
        "FULL_CHAIN_ACCEPTED_AND_INDEPENDENT_READBACK",
        "DEDICATED_LEDGER_RESERVATION",
        "ONLY_THIS_ACCOUNT_ACTION_WRITABLE",
        "NO_MOCK_OR_FIXTURE_FALLBACK",
        "FIVE_MATCHED_EVIDENCE",
    ], "首写必备条件被改动")
    require(first["sixStageOrder"] == [
        "PATH_VERIFIED", "SCOPE_GRANTED", "ACCOUNT_AUTHORIZED",
        "BACKEND_INTEGRATED", "SCHEMA_VERIFIED", "EXECUTION_VERIFIED",
    ], "能力六阶段顺序被改动")
    require(first["skipStageForbidden"] is True, "能力阶段可跳级")
    require(first["failedFirstWriteFallsBackToMock"] is False, "首写失败被允许降级为Mock")
    require(first["matchedCountRequired"] == 5, "首写MATCHED次数被放宽")
    require(first["businessLicenseRequired"] is True, "首写不再需要业务许可")

    # 10. safe-retry：沿用原单、新增attempt、UNKNOWN与增额禁止
    retry = contract["safeRetry"]
    for field in ("reusesApplication", "reusesExecutionOrder", "newAttemptOnly",
                  "newPlatformRequestKey", "reusesIdempotencyKey"):
        require(retry[field] is True, f"safe-retry的{field}被改动")
    require(retry["allowedSourceStates"] == ["FAILED"], "safe-retry允许从非FAILED发起")
    require("UNKNOWN" in retry["blockedSourceStates"], "safe-retry不再禁止UNKNOWN")
    require("INCREASE_BUDGET" in retry["alwaysBlockedActions"], "资金增加类被移出重试白名单")
    require(len(retry["recheck"]) == 5, "safe-retry复检项数量变化")

    # 11. 终态集合与唯一重开例外
    terminals = contract["terminalStates"]
    require(terminals["commandJob"] == ["SUCCEEDED", "FAILED", "REJECTED", "CANCELLED",
                                         "EXPIRED", "BLOCKED", "MANUAL_REQUIRED"],
            "command_job终态集合变化")
    require(terminals["executionOrder"] == ["EFFECTIVE", "FAILED", "CANCELLED", "MANUAL", "PARTIAL"],
            "execution_order终态集合变化")
    require("UNKNOWN" not in terminals["commandJob"] and "UNKNOWN" not in terminals["executionOrder"],
            "UNKNOWN被列入终态")

    # 12. 跨册差异：必须带归属卡且归属卡真实存在
    plan = read(PLAN)
    task_ids = set(re.findall(r"^### ((?:R00-\d{2}|M[0-5]-\d{2}[a-z]?))$", plan, re.MULTILINE))
    diffs = contract["crossVolumeDiffs"]
    require(len(diffs) >= 4, "跨册差异登记缺失")
    for entry in diffs:
        require(entry["id"] and entry["topic"] and entry["location"] and entry["resolution"],
                f"跨册差异不完整{entry}")
        require(entry["owner"] in task_ids, f"跨册差异归属卡不存在{entry}")
    by_owner = {entry["owner"] for entry in diffs}
    require("R00-04" in by_owner, "A22措辞差异未指派给R00-04")
    require("R00-03" in by_owner, "本卡可自收的差异未指派本卡")

    # 13. 真实用例：只登记不冒充通过
    cases = contract["acceptanceCases"]
    require(cases, "真实用例为空")
    for case in cases:
        require(case["state"] == "PLANNED_REAL_TEST", f"规格检查冒充真实验收{case['id']}")
        for field in ("setup", "action", "expected", "evidence"):
            require(case.get(field), f"真实用例缺{field}{case['id']}")
        require(case["acceptance"] in ALLOWED_ACCEPTANCE, f"用例不属于本卡映射的验收案例{case['id']}")
        for decision in case["decisions"]:
            require(decision in expected_ids, f"用例引用未知决定{case['id']}")
    require(len(cases) == contract["realVerification"]["plannedRealCaseCount"], "真实用例计数与登记数不一致")
    require(contract["realVerification"]["state"] == "NOT_RUN", "真实验收状态被标为已通过")
    require(contract["migrations"]["applicable"] is False, "D类规格卡不应声明迁移适用")

    # 14. 06/12/15 三册必须实际采用本规格
    for name, path in volumes.items():
        content = read(path) if isinstance(path, Path) else path
        require("R00-03-资金与恢复规格.md" in content, f"{name}册未引用本卡冻结规格")
    require("B + R + delta <= limit" in document, "正文缺少额度上限公式")
    return {
        "result": "PASS",
        "layer": "D_STATIC_SPEC_ONLY",
        "decisions": len(decisions),
        "competingActions": len(actions),
        "reservationTransitions": len(reservations["transitions"]),
        "evidenceLevels": len(evidence),
        "inboxRows": len(rows),
        "crossVolumeDiffs": len(diffs),
        "plannedRealCases": len(cases),
        "adoptedVolumes": sorted(volumes),
    }


def negative_checks(document, contract, volumes):
    cases = []

    def doc_case(name, old, new):
        require(old in document, f"负例输入未找到{name}")
        cases.append((name, document.replace(old, new), contract, volumes))

    def contract_case(name, mutate):
        value = copy.deepcopy(contract)
        mutate(value)
        cases.append((name, document, value, volumes))

    def volume_case(name, target, old, new):
        value = dict(volumes)
        content = read(value[target])
        require(old in content, f"负例输入未找到{name}")
        value[target] = content.replace(old, new, 1)
        cases.append((name, document, contract, value))

    doc_case("共同谓词丢失SENT条件",
             "not exists (select 1 from execution_attempts where execution_order_id = :id and status = 'SENT')", "")
    doc_case("共同谓词丢失未过期条件", "and expires_at > now()", "")
    doc_case("共同谓词丢失版本条件", "and version = :expected", "")
    doc_case("正文丢失RESULT_UNKNOWN", "RESULT_UNKNOWN", "UNKNOWN_STATE")
    doc_case("正文丢失隔离错误码", "TARGET_QUARANTINED", "GUARD_BUSY")
    doc_case("正文丢失额度上限公式", "B + R + delta <= limit", "B + delta <= limit")
    doc_case("判定表在途动作被改写",
             "| RECEIVED | SENT | LEASED | QUERY_ONLY; RECONCILE |",
             "| RECEIVED | SENT | LEASED | RESCHEDULE_ONLY |")
    contract_case("竞争动作漏过期", lambda c: c["contention"]["competingActions"].remove("expire"))
    contract_case("优先级缺竞争对", lambda c: c["contention"]["precedence"].remove(["cancel", "expire"]))
    contract_case("不再保证单一终态", lambda c: c["contention"].update(singleTerminalOutcome=False))
    contract_case("竞争失败方不再409", lambda c: c["contention"]["loserError"].update(http=500))
    contract_case("预占改为批准时创建", lambda c: c["reservations"].update(createdAt="approve"))
    contract_case("预占被允许存在于DRAFT", lambda c: c["reservations"]["existingOnlyIn"].append("DRAFT"))
    contract_case("减额被允许创建预占", lambda c: c["reservations"].update(decreaseCreatesReservation=True))
    contract_case("执行前被允许新建预占", lambda c: c["reservations"].update(newReservationBeforeExecution=True))
    contract_case("减额不再限定MATCHED", lambda c: c["reservations"].update(decreaseAdjustOnMatchedOnly=False))
    contract_case("结算改用E1证据",
                  lambda c: next(t for t in c["reservations"]["transitions"]
                                 if t["event"] == "verification_matched").update(evidence="E1"))
    contract_case("释放改用E0证据",
                  lambda c: next(t for t in c["reservations"]["transitions"]
                                 if t["event"] == "confirmed_not_executed").update(evidence="E0"))
    contract_case("E0被允许释放预占", lambda c: c["evidenceLevels"]["E0"].update(allows=["RELEASED"]))
    contract_case("E0不再禁止重发", lambda c: c["evidenceLevels"]["E0"]["forbids"].remove("RESEND_WRITE"))
    contract_case("E1不再允许释放", lambda c: c["evidenceLevels"]["E1"]["allows"].remove("RELEASED"))
    contract_case("E2不再允许结算", lambda c: c["evidenceLevels"]["E2"]["allows"].remove("SETTLED"))
    contract_case("E0反例清单被清空", lambda c: c["evidenceLevels"]["E0"].update(examples=[]))
    contract_case("UNKNOWN被当成终态", lambda c: c["unknown"].update(isTerminal=True))
    contract_case("UNKNOWN被允许重发", lambda c: c["unknown"].update(resendAllowed=True))
    contract_case("提交未知投影被改为VERIFYING", lambda c: c["unknown"]["submitted"].update(jobStatus="VERIFYING"))
    contract_case("回读未知投影被改为EXECUTING", lambda c: c["unknown"]["readback"].update(jobStatus="EXECUTING"))
    contract_case("提交未知被允许声称已受理", lambda c: c["unknown"]["submitted"].update(accepted=True))
    contract_case("提交未知不再隔离守卫", lambda c: c["unknown"]["submitted"].update(quarantinesGuard=False))
    contract_case("回读未知释放预占", lambda c: c["unknown"]["readback"].update(keepsReservation=False))
    contract_case("被允许私自扩展状态枚举", lambda c: c["unknown"].update(publicStatusExtensionAllowed=True))
    contract_case("租约过期被允许解除隔离", lambda c: c["guard"].update(leaseExpiryDoesNotRelease=False))
    contract_case("UNKNOWN不再强制隔离", lambda c: c["guard"].update(unknownAlwaysQuarantines=False))
    contract_case("隔离态写拒绝范围被放宽", lambda c: c["guard"].update(writeRejectedIn=[]))
    contract_case("inbox行被当成写资格", lambda c: c["inboxRules"].update(inboxRowGrantsWriteEligibility=True))
    contract_case("恢复调度被当成写平台", lambda c: c["inboxRules"].update(recoveryScheduleIsNotWriteCall=False))
    contract_case("PROCESSED之外也可直接ack", lambda c: c["inboxRules"].update(directAckOnlyWhenProcessed=False))
    contract_case("平台写次数上限被放宽", lambda c: c["inboxRules"].update(platformWriteCountMax=3))
    contract_case("在途被允许重投写",
                  lambda c: next(r for r in c["inboxRecovery"]
                                 if r["attempt"] == "SENT" and r["guard"] == "LEASED")["actions"].append("RESEND_WRITE"))
    contract_case("账本被加回全局约束", lambda c: c["overLimit"].update(noGlobalCheckConstraint=False))
    contract_case("超限期间允许新增额", lambda c: c["overLimit"].update(newIncreaseRejectedWhenOverLimit=False))
    contract_case("减额不再限定MATCHED", lambda c: c["overLimit"].update(decreaseOnlyOnMatched=False))
    contract_case("已批准未执行增额不再复检", lambda c: c["overLimit"].update(approvedNotExecutedRecheck=False))
    contract_case("首写失败可降级为Mock", lambda c: c["firstWrite"].update(failedFirstWriteFallsBackToMock=True))
    contract_case("首写MATCHED次数被放宽", lambda c: c["firstWrite"].update(matchedCountRequired=1))
    contract_case("能力阶段允许跳级", lambda c: c["firstWrite"].update(skipStageForbidden=False))
    contract_case("首写不再需要业务许可", lambda c: c["firstWrite"].update(businessLicenseRequired=False))
    contract_case("safe-retry改为新建申请", lambda c: c["safeRetry"].update(reusesApplication=False))
    contract_case("safe-retry允许UNKNOWN发起", lambda c: c["safeRetry"].update(allowedSourceStates=["FAILED", "UNKNOWN"]))
    contract_case("资金增加类被移出白名单", lambda c: c["safeRetry"].update(alwaysBlockedActions=[]))
    contract_case("终态集合被改动", lambda c: c["terminalStates"]["commandJob"].remove("EXPIRED"))
    contract_case("UNKNOWN被列入终态", lambda c: c["terminalStates"]["commandJob"].append("UNKNOWN"))
    contract_case("用例冒充已通过", lambda c: c["acceptanceCases"][0].update(state="PASS"))
    contract_case("真实用例缺证据", lambda c: c["acceptanceCases"][0].update(evidence=""))
    contract_case("真实用例引用未映射验收", lambda c: c["acceptanceCases"][0].update(acceptance="A41"))
    contract_case("跨册差异无归属卡", lambda c: c["crossVolumeDiffs"][0].update(owner=""))
    contract_case("跨册差异归属卡不存在", lambda c: c["crossVolumeDiffs"][0].update(owner="R00-99"))
    contract_case("规格卡被声明为含迁移", lambda c: c["migrations"].update(applicable=True))
    contract_case("真实验收被标为已通过", lambda c: c["realVerification"].update(state="PASS"))
    contract_case("决定标题与正文漂移", lambda c: c["decisions"][0].update(title="单一领取闸（改）"))
    volume_case("12册未采用本规格", "12", "R00-03-资金与恢复规格.md", "资金与恢复规格")
    volume_case("06册未采用本规格", "06", "R00-03-资金与恢复规格.md", "资金与恢复规格")
    volume_case("15册未采用本规格", "15", "R00-03-资金与恢复规格.md", "资金与恢复规格")

    results = []
    for name, doc, spec, vols in cases:
        try:
            validate(doc, spec, vols)
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
    document = read(SPEC)
    volumes = {name: path for name, path in VOLUMES.items()}
    report = validate(document, contract, volumes)
    report["sourceSha256Utf8Lf"] = {
        str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
            path.read_bytes()).hexdigest()
        for path in [SPEC, CONTRACT, PLAN, *VOLUMES.values()]
    }
    if options.self_test:
        report["negativeMutationResults"] = negative_checks(document, contract, volumes)
        report["negativeMutationsRejected"] = len(report["negativeMutationResults"])
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if options.report:
        options.report.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
