# M3-08a V9真实资源分页：自定义人群切片

2026-10-28；BE/F11；人群切片技术验证通过，整卡其余资源类别仍待实现。直接依赖M3-07/M1-03b已有技术实现；真实E01与人工E08保留。

本轮实现LookupKind=audiences的ADQ正式来源切片，不把广告计划冒充groups/targets，也不宣称所有标签/版位/目标/素材类别已经交付。其他kind返回CAPABILITY_UNAVAILABLE，类别来源按后续任务补齐。

## 来源与适用

已下载核对固定官方TencentAd/marketing-api-go-sdk revision 81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9：v3 API custom_audiences/get、response/list模型、ProcessStatus、AudienceOnlineStatus、PageInfo及v3 configuration默认https://api.e.qq.com/v3.0。文件摘要和原链接见source-spec.json。固定接口为GET /v3.0/custom_audiences/get，请求account_id/page/page_size/fields；响应data.list及page_info。仅采audience_id/account_id/name/status/online_status，不采人群成员、描述或其他未列字段。官方SDK证明协议和枚举，不证明应用实际scope、账户授权或当前投放上下文可用性。

独立维护LookupSourceProof冻结完整ConfigurationContext、binding/version、授权/version、实际scope、最大数据年龄、原始资料指纹和有效期。只有该精确上下文的ONLINE_SUCCESS_VERIFIED_FOR_EXACT_CONTEXT依据有效时，原生SUCCESS+ONLINE人群才eligible=true；离线、处理中和未知状态不可选择。运行身份不能自授依据，禁止由用户q生成新平台标签或默认可用资源。

## 实现

0052依赖0051_cloud_documents，增加强RLS来源上下文依据及不可变分页快照。AudienceLookupSync沿现有应用×账户准入/六阶段/原生关联/来源档案和SyncStore页事务读取完整真实来源。每页重核当前授权及维护依据，同run总数漂移/重复人群ID拒绝；页快照、镜像和确认游标同事务。总量超过10000显式DATA_COVERAGE_INSUFFICIENT，不静默截断。

LookupService只读取最近完整SUCCESS且未过期的来源run。上游错误或不完整同步返回UPSTREAM_ERROR，未接入/未同步/过期返回CAPABILITY_UNAVAILABLE，无范围权限FORBIDDEN；完整来源下无搜索匹配才返回真正空LookupPage。q仅在真实来源中搜索，category/parentId无协议依据时拒绝。分页游标HMAC绑定租户/成员/用户、精确上下文摘要、依据ID、快照run、kind/q/page-size；篡改或跨上下文重放拒绝。来源变更要求重新查询，保留原始contextKey用于前端核对。

实际GET /v1/delivery/configuration/options解析冻结前端完整contextQuery，拒绝重复或未知参数及客户端binding注入。LookupService.options根据服务端会话身份、维护依据和真实账户目录构造GatewayContext，客户端不能自报平台授权。root装配runtime的实际应用选择器及同步入口。

## 当前验证

9项协议单元通过（0.37秒）；新增源码、路由及测试共7文件mypy、Ruff通过。真实PG/Redis/HTTP专项最终7项通过（8.24秒），命令为WSL backend下 `python scripts/test_infrastructure.py --group resource-lookups --details`。覆盖完整分页和幂等、搜索不生成资源、离线不可选、真实空与未同步/未接入/上游错误、HMAC/q/context/user隔离、跨页重复及总数漂移拒绝；真实runtime选择器+会话HTTP /options返回冻结LookupPage并拒绝客户端binding注入及未登录。应用配置、HTTP和依据合成，runtime选择器/路由/服务/PG真实运行。真实平台调用0，付费0。所有测试平台身份、scope和适用依据均为合成，技术通过不关闭E01或P验收。

回滚采用停用该应用只读入口和撤销来源适用依据，保留分页档案，不做破坏性downgrade。BE/F11具名复核PENDING；其余LookupKind仍待正式来源与适配，整父卡不可因本切片通过而关闭。
`SyncStore.resource_kind`只允许小写字母数字点下划线；本轮专项发现来源键拼接标准UUID含横杠不合法，已改为proof.id.hex并完整复验。测试夹具先变更原生账户再注册新绑定，保留实际原生变更使既有能力失效的安全触发器，不通过弱化生产守卫修测试。
2026-10-28追加targets推广资产切片：固定SDK协议、0056按kind隔离来源、严格未删除/核对资产类型才可选；4项真实PG/Redis/HTTP专项、18项合并协议单元和7项audiences回归通过。见[targets切片证据](targets/README.md)。此前本文“仅audiences”描述第一切片；当前已技术实现audiences与targets，其他LookupKind仍未实现，整卡不关闭。
