# M1-18 完整FrontendSnapshot与持久scope

2026-10-28；负责人BE，协作F02/F04/F05；**IN_PROGRESS**。基线739b721。本批实际实现工作区目录/范围/版本及可成立的规范投影；缺失的计划/直播等生产投影不会假装完整，整卡未通过。输入M1-08/09/10/11/12/13/16/17已有各自本地技术证据，正式平台映射、单位和G1/具名复核仍独立保留。

## 冻结消费者与实现

真实消费者为frontend/src/services/api-client.ts的ApiClient.getSnapshot/setScope、frontend/src/lib/domain.ts的FrontendSnapshot以及lib/reporting.ts的scopedPlans/scopedJobs/scopedEvidence、domain.ts的businessTotals/money。十二集合为tenants/shops/adAccounts/liveSessions/plans/products/materials/tasks/riskPolicies/replaySegments/replayReports/connectors；schemaVersion=command-job-v1.0、source=API、revision及Asia/Shanghai scope保持冻结形状。

0060_frontend_snapshot下接0059_content_asset_references，保存当前成员ReportingScope、内容摘要和单调revision；复合成员FK、强RLS限制本人，scope只是偏好不授予权限。每次读取重新核对活跃租户/用户/成员、当前snapshot.read角色以及权限mask对应的shop/account双端数据范围，使用同一REPEATABLE READ事务读取完整集合。当前无变化时不修改版本；目录/范围/权限版本变化才增revision，保持JS安全整数。并发首读/变更遇40001有限重试，稳定读避免无意义写冲突。

GET /v1/frontend/snapshot拒绝任何附加query，不接受客户端tenant/member/scope覆盖；PUT /v1/frontend/reporting-scope使用既有会话、Origin和CSRF链，严格校验body/未知字段，选择其他店铺/场次或不在范围的连接器拒绝；all仅代表当前已授权集合。持久选择撤权后重新裁剪/重置到当前授权集合，不能沿用旧选择扩大权限。

真实授权目录即使只有本地BOUND店铺也完整返回，不为旧M0空快照预期隐藏真实记录；已更新旧M0对应测试预期，空业务集合仍保持真空。每集合上限10000，查询10001行探测后明确DATA_COVERAGE_INSUFFICIENT，不将截断集合伪装完整。

## 当前投影边界与明确缺口

- 店铺/账户读取真实目录，账户跨多个店铺但冻结AdAccount只有单shopId时明确coverage要求收窄范围，不能猜归属。未映射产品线明确缺口；连接器返回实际目录涉及产品的UNKNOWN状态，不凭目录或一次只读升级写能力。
- 商品读取实际canonical products/skus及commerce观察，要求当前授权/能力、明确的独立CNY_MINOR价格依据与唯一SKU/目录关联；价格未知不得写0。确认真实0可返回，库存/点击/订单缺失保持null。多SKU冻结单行契约/缺命名目录/缺账户或单位依据时明确coverage，不悄悄选第一SKU。被本地移除commerce产品和已确认回收状态不视为可用产品。
- tasks使用真实已持久CommandJob投影及真实事件/回执，不生成空文档或Mock任务。策略从实际PolicyConfig/版本读取，仅当前具备全租户读取范围者取得租户整体策略；产品关联素材只取真实确认资产和稳定VersionId，执行现有overview.read双端权限，不开放未支持的音视频。
- **plans尚不能完整返回**：旧型AdPlan的budgetMinor/bidMinor/targetRoi/enabled/sourceUpdatedAt均有不可伪造的必填要求；当前镜像并未同时证明所有字段/单位和源时间。有相应真实目录或原生镜像时返回明确coverage gap，不能空plans或写默认0。后续需要补规范字段来源或另行审阅nullable前端契约。
- **liveSessions尚缺实际源时间/状态/指标投影**：有场次行时明确coverage；不将本地updated_at当平台更新时间，不将本地PLANNED伪装真实平台直播状态。
- **replaySegments/replayReports暂无生产producer/table**：本轮没有声称开发了回放业务读取。没有相关场次/记录时保留合法结构空数组；一旦存在实际场次，整快照因上述来源缺口拒绝，不能宣称完整回放源为空。此能力仍需后续卡。

原独立SDK累计10次真实只读成功不能代替上述驭量业务映射/财务口径或生产投影。该卡没有发生新平台请求、资金写或付费调用。

## 验证检查点

已实际通过：6项ReportingScope单元，0.39秒；Ruff及src+本卡测试mypy通过。7项真实PG/HTTP用例已实际通过（6.99秒），命令`python scripts/test_infrastructure.py --group frontend-snapshot --details`：目录完整/revision/scope、CSRF/越权/身份覆盖、未知计划字段coverage、真实10001店铺规模拒绝、状态RLS、当前双端scope裁剪、真实商品页投影与独立单位依据。

backend/scripts/check_snapshot_frontend.mts已通过隔离且忽略的tsx@4.20.6运行实际HTTP fixture，真实ApiClient及scopedPlans/scopedJobs/scopedEvidence/businessTotals/money全部PASS；5项非法结构被拒绝，6次注入fetch、0真实网络。该fixture含实际本地PG生成的合成身份/目录，空业务金额消费者正确返回null而非0；独立商品PG用例验证明确价格依据前拒绝、依据后真实0与缺失指标null。并非浏览器验收。结果见frontend-consumer.json。

DoD1：结构及当前已支持切片实际消费者通过；DoD2范围/CSRF/RLS/持久化技术通过；DoD3真实10001目录明确拒绝且收窄到1店后完整读取通过。完整业务plan/live/replay生产投影缺口和BE/FE具名复核不因fixture通过消除，整卡保持IN_PROGRESS。回滚为前向停用新增范围入口/应用回退，保留用户scope与revision，不删历史或伪造旧源。Git由主Agent统一处理，本子任务不提交。

首次专项发现app_login按既有安全设计不可SELECT users，未扩大用户表权限；0060新增窄SECURITY DEFINER函数access.snapshot_member_version，严格绑定current_actor/current_tenant及本人活跃用户/成员/租户，只返回当前authzVersion或null。修复后7项完整复验通过，M0-12/M0-14两项回归通过（4.40秒）。当前连接器状态仍保守UNKNOWN，不把本地目录或原SDK取样成功推成完整读写能力；其余业务投影缺口保留。
