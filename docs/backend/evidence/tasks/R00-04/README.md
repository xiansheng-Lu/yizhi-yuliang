# R00-04 任务领取与验收登记规则

日期：2026-10-27（Asia/Shanghai）；输入基线2b286a8；本地分支feat/backend-wsl-foundation。
负责人BE，协作F00/F13。用户明确继续开发并提供WSL Docker作为环境。

状态WAITING_REVIEW。R00-01/02/03技术基线可用于工程准备，人工签字继续PENDING。

## 交付、范围与验证

- execution-rules.md：状态、领取、三条DoD、证据命名、外部输入与解除条件。
- review-template.md：任务/版本/角色/逐项证据/未决问题/结论/批准范围，具名确认来源。
- check_task_status.py：非法状态、失效/越界证据、DONE技术结果/三条DoD/双端记录/依赖的守卫，13项非法变体被拦截。
- 19册A22闭合X-01，UNKNOWN不是第十三种任务状态；20册链接登记规则。

实际命令：仓库根目录 `python docs/backend/tools/check_plan.py` PASS（149卡、7门禁、156节点、101父任务、42A/14F）；
`python docs/backend/tools/check_task_status.py --self-test` PASS（13非法变体被拒绝，正例仅驻内存）；
`python -m ruff check docs/backend/tools/check_task_status.py` PASS。
报告见static-check.json，最终代码基线以本目录Git提交为准。

## 三条DoD

1. 149张卡的负责人、范围、依赖、交付、三条DoD与入口齐全：计划检查PASS。
2. 156节点拓扑及101父任务映射完整，未重复关闭父卡：计划检查PASS。
3. 已授权任务按技术基线与台账推进；本轮选取M0-01准备交付、M0-04真实验证、M0-02信封复核。
   领取不代表最终验收；所有人工复核仍PENDING，没有将未跑或外部阻塞记为PASS。

## 未决、复核与回滚

BE/FE需按模板确认规则及验收归属，E08仍在。签字模板检查只校验记录结构，不能证明人的确认真实性。
门禁PASSED尚不接受自动登记，需后续实现对应真实证据校验。
本卡不运行真实平台、付费、数据库迁移；回滚为反向提交规则/工具与A22修订，保留已有任务历史。
下一张工程卡M0-01按R00技术基线准备；真实数据库迁移M0-06需M0-04实证，前端代码本轮不改。
