"""保守的确定性解析与模型后置核对；未知语法澄清，不猜金额或扩大对象。"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from yuliang.modules.aikernel.prompts.registry import (
    Action,
    Boolean,
    CaseContext,
    Handoff,
    Interpretation,
    JobValue,
    Money,
    Ratio,
    validate_context,
)

RULE_VERSION = "command-rules-v2"
PATTERNS: tuple[tuple[Action, str], ...] = (
    ("CHANGE_DAILY_BUDGET", r"(?:日)?预算"),
    ("CHANGE_BID", r"出价"),
    ("SET_ROI_TARGET", r"ROI|投入产出比"),
    ("PAUSE_AD_PLAN", r"暂停|停用"),
    ("RESUME_AD_PLAN", r"恢复|启用"),
    ("REQUEST_LIVE_START", r"开播|开始直播"),
    ("REQUEST_LIVE_END", r"关播|结束直播|停止直播"),
)
UNSAFE = re.compile(
    r"忽略|绕过|跳过|直接执行|审批|批准|密码|密钥|令牌|token|secret|system|"
    r"另一个租户|租户[一二三四五六七八九十0-9]|充值|删除|所有|全部|同时|"
    r"不|勿|别(?:再)?|取消|如果|假如|是否|能否|吗|么|\?|美元|美金|港币|港元|欧元|微信豆|豆|%|百分",
    re.IGNORECASE,
)
NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"


def rejected(reason: str = "请提供授权范围内的明确对象、单一动作和目标值。") -> Interpretation:
    return Interpretation(kind="UNRECOGNIZED", text=reason)


def _normal(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _value(statement: str, action: Action) -> JobValue | None:
    if action in ("PAUSE_AD_PLAN", "RESUME_AD_PLAN"):
        return Boolean(kind="BOOLEAN", value=action == "RESUME_AD_PLAN")
    if action in ("REQUEST_LIVE_START", "REQUEST_LIVE_END"):
        return Handoff(kind="HANDOFF", value="START" if action == "REQUEST_LIVE_START" else "END")
    # 只读取字段后的明确绝对设值；不将“增加100元”等相对变化当目标值。
    marker = dict(PATTERNS)[action]
    tail = re.split(marker, statement, flags=re.IGNORECASE, maxsplit=1)[-1]
    match = re.fullmatch(
        rf"(?:目标)?(?:调整到|调整为|调到|改到|改为|改成|设为|设置为|设到)"
        rf"({NUMBER})(元|分|人民币|CNY)?[。.!！]?",
        tail,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    number, unit = match.groups()
    try:
        amount = Decimal(number.replace(",", ""))
        if amount <= 0:
            return None
        if action == "SET_ROI_TARGET":
            if unit or not amount.is_finite():
                return None
            return Ratio(kind="RATIO", value=float(amount))
        if unit is None or (unit != "分" and amount != amount.quantize(Decimal("0.01"))):
            return None
        minor = amount if unit == "分" else amount * 100
        if minor != minor.to_integral_value() or minor > 9007199254740991:
            return None
        return Money(kind="MONEY", currency="CNY", amount_minor=int(minor))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def parse_rules(statement: str, context: CaseContext) -> Interpretation:
    value = unicodedata.normalize("NFKC", statement).strip()
    if not value or len(value.encode("utf-16-le")) > 1000 or UNSAFE.search(value):
        return rejected("这条指令包含不支持的操作、单位或授权要求，请拆分并重新说明。")
    actions = [
        action for action, pattern in PATTERNS for _ in re.finditer(pattern, value, re.IGNORECASE)
    ]
    if not actions:
        if re.fullmatch(r"(?:帮我)?(?:查看|看看|查询|看下|看一看).{0,60}", value):
            objects = [c for c in context.candidates if c.target_type == "LIVE_SESSION"]
            remaining = re.sub(r"^(?:帮我)?(?:查看|看看|查询|看下|看一看)", "", value)
            named = [c for c in objects if c.name and c.name in remaining]
            for candidate in sorted(named, key=lambda c: len(c.name), reverse=True):
                remaining = remaining.replace(candidate.name, "")
            remaining = re.sub(
                r"当前|今天|本场|直播间|直播|场次|情况|数据|概况|经营|的|一下", "", remaining
            )
            if remaining.strip(" 。.!！"):
                return rejected("没有找到明确的授权查询对象，请核对场次名称。")
            lives = [c.id for c in (named or objects)]
            return Interpretation(
                kind="QUERY",
                text="已识别查询意图，需从授权数据源读取结果。",
                live_session_id=lives[0] if len(lives) == 1 else None,
            )
        return rejected()
    if len(actions) != 1:
        return rejected("一条指令只能包含一个动作，请分别说明。")
    action = actions[0]
    if action not in context.allowed_actions:
        return rejected("当前范围未授权此动作。")
    parsed = _value(_normal(value), action)
    if parsed is None:
        return rejected("请明确绝对目标值；金额须使用人民币元或整数分，ROI须为正比值。")
    target_type = "LIVE_SESSION" if isinstance(parsed, Handoff) else "AD_PLAN"
    objects = [
        candidate for candidate in context.candidates if candidate.target_type == target_type
    ]
    # 同名/简称均收集全部，绝不按模型推荐或列表顺序挑一个。
    matched = []
    aliases: list[str] = []
    prefix = re.split(dict(PATTERNS)[action], _normal(value), maxsplit=1, flags=re.IGNORECASE)[0]
    prefix = re.sub(r"^(?:帮我|请|把|将)*", "", prefix)
    for candidate in objects:
        name = _normal(candidate.name)
        alias = re.sub(r"^(?:adq|微信广告)", "", name)
        named_target = (
            prefix in (name, alias)
            if isinstance(parsed, (Money, Ratio))
            else name in _normal(value) or (bool(alias) and alias in _normal(value))
        )
        if name and named_target:
            matched.append(candidate.id)
            aliases.extend((name, alias))
    if isinstance(parsed, (Boolean, Handoff)):
        remaining = _normal(value)
        for alias in sorted(set(aliases), key=len, reverse=True):
            remaining = remaining.replace(alias, "")
        remaining = re.sub(dict(PATTERNS)[action], "", remaining, flags=re.IGNORECASE)
        remaining = re.sub(r"帮我|请|把|将|一下|adq计划|计划|adq|[。.!！]", "", remaining)
        if remaining:
            return rejected("请直接说明一个动作和一个目标，不混入其他操作或条件。")
    if not matched:
        # 只有没有指定名称的泛指才能返回全部候选；不存在的名称不能退回全列表。
        generic = re.sub(dict(PATTERNS)[action], "", _normal(value), flags=re.IGNORECASE)
        generic = re.sub(r"^(?:帮我|请|把|将)*", "", generic)
        if target_type == "AD_PLAN" and generic not in ("adq计划", "计划", "adq"):
            return rejected("没有找到当前授权范围内的目标，请核对名称。")
        if target_type == "LIVE_SESSION" and generic:
            return rejected("请明确当前授权的直播场次。")
        matched = [candidate.id for candidate in objects]
    if not matched or len(matched) > 20:
        return rejected("目标为空或候选过多，请缩小范围。")
    # “A和B”或两个明确命名均属于多目标请求；同名对象仍留作消歧。
    names = {re.sub(r"^(?:adq|微信广告)", "", _normal(c.name)) for c in objects if c.id in matched}
    if aliases and len(names) > 1:
        return rejected("请每次只指定一个变更目标。")
    result = Interpretation(
        kind="ACTION",
        text="已识别候选，请核对对象与目标值；尚未执行操作。"
        if target_type == "AD_PLAN"
        else "已识别人工交接候选，请人工处理开关播；尚未控制推流。",
        action_type=action,
        new_value=parsed,
        target_type=target_type,
        candidate_ids=tuple(matched),
    )
    validate_context(result, context)
    return result


def guard_model(statement: str, context: CaseContext, proposed: Interpretation) -> Interpretation:
    """模型不能改变原话的动作/绝对数值、缩减歧义集合或增加授权对象。"""
    expected = parse_rules(statement, context)
    try:
        validate_context(proposed, context)
    except ValueError:
        return rejected("模型候选超出授权范围，请重新核对。")
    if expected.kind == "UNRECOGNIZED":
        return expected
    if expected.kind == "QUERY":
        compatible = proposed.kind == "QUERY" and proposed.live_session_id in (
            None,
            expected.live_session_id,
        )
    else:
        compatible = (
            proposed.kind == "ACTION"
            and proposed.action_type == expected.action_type
            and proposed.target_type == expected.target_type
            and proposed.new_value == expected.new_value
            and set(expected.candidate_ids) <= set(proposed.candidate_ids)
        )
    if not compatible:
        return rejected("模型候选与确定性核对不一致，请明确对象、动作和目标值。")
    # 目录解析可以根据原话收窄模型给出的授权候选超集，但不能采信模型缩减真正歧义。
    # 查询场次由当前授权范围决定；模型缺省不阻止确定性补全，错误的明确对象仍拒绝。
    # 不返回模型自由文本，避免把“已完成”或编造指标写成事实。
    return expected


def wire(value: Interpretation) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": value.kind, "text": value.text}
    if value.kind == "QUERY":
        result["live_session_id"] = str(value.live_session_id) if value.live_session_id else None
    elif value.kind == "ACTION":
        result.update(
            action_type=value.action_type,
            new_value=value.new_value.model_dump(mode="json") if value.new_value else None,
            target_type=value.target_type,
            candidate_ids=[str(identifier) for identifier in value.candidate_ids],
        )
    return result
