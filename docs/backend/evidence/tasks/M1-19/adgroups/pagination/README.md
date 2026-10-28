# M1-19 ADGROUP 稳定跨页技术切片

状态：本技术切片 TECH_VERIFIED，等待复核。BE/F10 最终契约确认及实际平台验收另行保留，不代表整个M1-19或前端验收完成。

沿用 V9 `filters.cursor`、`nextCursor` 与既有 scopeKey，不增加前端生产字段。仅使用已完整同步的 ADGROUP 来源；原始完整观察最多10000条，数值原生ID升序，每页100条，禁止悄悄截断。每页在同一 REPEATABLE READ 事务重新检查当前身份、店铺账户范围、授权及声明、完整 run/口径依据和投影指纹；新批次、权限变化、上下文或筛选变化拒绝续页。

`adgroup-c1.` 随机 selector 只定位当前 actor 私有的服务端位置，不作为授权凭据，也不复用任何 OAuth/会话/费用密钥。首屏原子生成固定后续链，同 cursor 重放同页与同 nextCursor；位置由服务器记录。每身份最多32条活跃链、3200条私有位置，TTL最多600秒；每次首屏至多清理3条完整过期链，避免部分清理破坏链外键。next 外键同时约束 tenant、membership、user、chain 和 id，已知他人 selector 不能制造跨 actor 外键引用。

只有 ADGROUP 查询允许写入上述私有位置元数据，其余资源继续只读事务。无平台请求位于查询/重试事务内；最多三次40001重试，耗尽返回固定409 VERSION_CONFLICT，其他数据库错误不吞掉、不自动重试。

## 当前验证

- Ruff：本切片源码及新增测试通过。
- mypy：4个相关源码通过。
- 单元：20通过，0.94秒，包含有界位置、规范selector、数字排序、40001耗尽和非40001透传、原广告组投影。
- PG入口：`tests/integration/test_adgroup_query_pagination.py`，13通过53.67秒；包含实际同步与HTTP连续1101条、重放、筛选及来源/权限变化、297条过期整链清理、上限及跨actor外键负例。
- 原查询组：`test_resource_query_plans.py`，14通过15.37秒；1001来源改为分页，真实10001上游总量由既有同步协议拒绝，失败批次不回退旧成功结果。
- 两组均真实空库升级至 `0075_execution_evidence_reads`；本迁移文件名0074_resource_query_continuations，实际revision为 `0074_resource_query_cursors`（适配既有Alembic版本列32字符上限）。数据库窗口已释放。
- 真实平台/真实模型/付费调用：0。

## 首轮与修复记录

1. 0074初版revision全名33字符导致13项setup错误（4.63秒）；缩短revision并由主Agent同步0075依赖，不扩公共版本表。
2. 分页首轮10通过/3失败（54.67秒）：数字ID校验返回字符串导致字典序，改为验证后整数排序并补单元；另两处测试owner计数/取样未限定当前租户，已收紧。最终13项全部通过。
3. 原查询首轮12通过/2旧期待失败（15.22秒）：按新分页合同更新无来源续页409及1001条分页期待。第二轮13通过/1失败（15.95秒）：测试证实10001已由既有同步协议提前拒绝，改为实际拒绝与失败批次保护断言。最终14项全部通过。

SOURCE和SQL已冻结。真实平台、真实权限配置/数据证明及BE/F10签字仍为独立门禁。

实际前端消费 capture 仅写 `.runtime`；环境变量 `YULIANG_ADGROUP_PAGINATION_CAPTURE` 指定落盘位置，含真实HTTP context/identity、primary/pages/replay与合成ID全集。归档证据只保留脱敏汇总和源码hash，不提交原始selector或会话。
