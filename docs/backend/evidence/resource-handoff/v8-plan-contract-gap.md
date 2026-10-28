# V8计划真实来源与只读契约缺口

2026-10-28，待BE/FE契约确认；当前后端其他就绪任务继续。此处是源码能力与必填字段矛盾，不是再次请求平台只读许可。

## 已核对的事实

frontend/src/lib/domain.ts:59起AdPlan要求budgetMinor/bidMinor/targetRoi为number、enabled为boolean、sourceUpdatedAt和attributionWindow为string；frontend/src/lib/delivery.ts:125起Campaign.budgetMinor/bidMinor也是必填number。后端不能用0、false、本机时间或账户预算填这些未知的计划值。

[固定官方Campaign模型](https://github.com/TencentAd/marketing-api-go-sdk/blob/81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9/pkg/model/model_campaigns_get_list_struct.go)完整字段包含daily_budget/total_budget与created_time/last_modified_time，确实没有bid_amount或目标ROI字段；[同修订ADGROUP模型](https://github.com/TencentAd/marketing-api-go-sdk/blob/81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9/pkg/model/model_adgroups_get_list_struct.go)是另一层级，不能作为Campaign出价。SDK类型也没有证明币种/单位、ROI倍率或时间单位。0062已实现完整原始观察与独立字段依据，有据ADGROUP保留ADGROUP，CAMPAIGN缺出价时明确gap；尚无完整V8/canonical AdPlan映射。

## 建议的可复核决议

| 字段/行为 | 当前契约 | 建议只读契约及验收 |
|---|---|---|
| 预算/出价/目标ROI | 必填数字（Campaign目标ROI已可空） | 没有明确来源的字段允许null并附字段级原因，真实0仍为0；不跨层级借值 |
| 启停状态 | AdPlan.enabled必填布尔 | 无独立状态映射时保持未知并显示待同步，不能null自动当“已暂停”；有明确映射才布尔 |
| 源时间/归因 | 必填string | 未提供的真实源字段允许null并显示缺失，不使用抓取时间或统计日期冒充 |
| 标识层级 | AdPlan/Campaign容易共用“计划”名称 | CAMPAIGN/ADGROUP及父原生ID明确区分，canonical映射单独确认 |
| 执行动作 | 数字可参与调价/预算操作 | 当前能力、完整动作必需字段和版本未核验时关闭动作；只读nullable不授予写能力 |

相应前端改动必须同时覆盖类型、响应校验、显示、排序/统计和动作前置，不能只放宽TypeScript类型。以真实后端fixture逐项验证null/真实0/缺单位/未知状态/层级/源时间，当前“缺口显式拒绝”保留到联合契约就绪。

影响：完整M1-18 FrontendSnapshot、V8 workspace及其后续实际接线。解除条件：BE/FE确认上述可空契约与层级映射，或提供已存在的正式字段映射决议；金额/时间仍需独立官方口径依据。SOP、内容导入、已有V9规范查询和独立事实聚合不被该决议阻塞。

项目AGENTS.md约定：coding agent负责授权任务实现与验证；“两位负责人负责契约确认、本端复核、前后端联调与最终验收”。本文件不是Agent代签，也未改前端冻结契约。
