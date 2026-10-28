"""应用控件类型；不提供平台未核验的上下限、关键词或枚举。"""

from yuliang.modules.configuration.models import FieldCapability, FieldId, ValueType

FIELD_TYPES: dict[FieldId, ValueType] = {
    "name": "TEXT",
    "account": "SELECT",
    "shop": "SELECT",
    "objective": "SELECT",
    "target": "MULTI_SELECT",
    "placement": "MULTI_SELECT",
    "optimization": "SELECT",
    "strategy": "SELECT",
    "targetingMode": "SELECT",
    "region": "MULTI_SELECT",
    "locationType": "SELECT",
    "age": "RANGE",
    "gender": "SELECT",
    "interest": "MULTI_SELECT",
    "behavior": "MULTI_SELECT",
    "intention": "MULTI_SELECT",
    "keyword": "MULTI_SELECT",
    "behaviorScene": "MULTI_SELECT",
    "behaviorWindow": "SELECT",
    "behaviorIntensity": "SELECT",
    "customAudience": "MULTI_SELECT",
    "expansion": "SELECT",
    "hardConstraints": "MULTI_SELECT",
    "template": "SELECT",
    "creators": "MULTI_SELECT",
    "groups": "MULTI_SELECT",
    "budgetType": "SELECT",
    "budget": "INTEGER",
    "bidMode": "SELECT",
    "bid": "DECIMAL",
    "schedule": "TEXT",
    "timeSlots": "MULTI_SELECT",
    "creative": "MEDIA",
    "title": "TEXT",
    "copy": "TEXT",
    "portrait": "MEDIA",
    "duration": "INTEGER",
    "orderType": "SELECT",
    "fundType": "SELECT",
    "deliveryMode": "SELECT",
    "creativeMethod": "SELECT",
    "productRange": "MULTI_SELECT",
}


def unconfirmed(field: FieldId, reason: str = "该目标/版位的字段规范尚未核对") -> FieldCapability:
    return FieldCapability(state="UNCONFIRMED", reason=reason, value_type=FIELD_TYPES[field])


def initial_fields() -> dict[FieldId, FieldCapability]:
    result = {field: unconfirmed(field) for field in FIELD_TYPES}
    result["name"] = FieldCapability(
        state="PUBLIC",
        value_type="TEXT",
        reason="应用草稿名称；不代表平台计划名称限制或写入许可已核验",
    )
    return result
