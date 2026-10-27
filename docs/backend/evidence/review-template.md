# 人工复核记录模板

复制到对应 `tasks/<任务ID>/README.md`；BE和FE各自确认，不以Agent自检代签。

- 任务ID：
- 基线：完整Git提交号（含本次采用的规格/代码）；工作区草稿不作为签字基线。
- 复核人姓名与角色：BE / FE
- 复核日期与确认来源：具名提交、评审评论或明确授权归档的原始确认索引。
- 范围、输入依赖及证据：

| 验收标准 | 预期与实际 | 证据或缺陷ID | 结论 |
|---|---|---|---|
| DoD 1 | | | PASS / FAIL / NOT_RUN / BLOCKED |
| DoD 2 | | | PASS / FAIL / NOT_RUN / BLOCKED |
| DoD 3 | | | PASS / FAIL / NOT_RUN / BLOCKED |

- 联合契约项与决定：
- 未决问题：负责人、解除条件、是否阻塞本卡。
- 不适用层级与理由：
- 结论：通过 / 退回 / 待补证据。
- 批准范围：规格基线 / 本端技术交付 / 对应联调；真实平台写与付费必须另附账户×动作×额度许可。

通过后在台账 `review.backend` / `review.frontend` 填 `PASS`，并登记
`review.records.<角色> = {name, date, baseline, source}`。不涉及FE的实现卡，
`review.frontend` 可为 `NOT_APPLICABLE`，须附 `review.frontendReason`；BE仍须具名复核。
仅全部适用DoD、技术验证、依赖和人工复核通过才可DONE。
