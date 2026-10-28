# M1-08 ADQ账户与计划只读镜像

负责人BE，协作F04/F07；WAITING_REVIEW。基线cbbf684666f6644af98ffe34d0f3c4f67ff1fa1e，实现b198a3dfd6b8f9221f40cbfb8f2f2ba8f9de0482；M1-06/M1-15a实际技术输入已交付，依持续开发授权实施本地只读镜像。额外复用M1-02六阶段、M1-03b存档、M1-04治理与M1-17原生关联。真实平台P验收仍缺E01实际业务租户、店铺/账户绑定、已批准scope及明确只读许可；G0/E08保留。

三条DoD：所有页落库并隔离同native_id的不同账户；BOUND/AUTHORIZED分开且不可跨账户/店铺拼装目标；拉取失败不删除旧镜像、配置变化追加修订与来源。

## 实现与来源

核对冻结目录后确认：原1,818条目录没有`campaigns/get`，不能借用`adgroups/get`的ID。0044和固定官方SDK修订提供独立、只读补充目录；不替换原目录head，不改变前端冻结目录/ID，不自动产生账户、scope、六阶段或写许可。来源行、SDK指纹与PATH阶段均需独立维护身份录入，过期、停用或原目录基线改变即关闭。补充ID不向当前PlatformService暴露。

[官方SDK的计划接口](https://github.com/TencentAd/marketing-api-go-sdk/blob/81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9/pkg/api/api_campaigns.go)明确使用相对`campaigns/get`和1.3协议；本次固定保留`/v1.3/campaigns/get`，账户读取保留`/v3.0/advertiser/get`。SDK配置中的默认地址为sandbox，源码核对不等于实际生产账户可用。逐文件来源和SHA见source-spec.json；本次真实平台调用0。

0045交付账户/计划资产镜像、逐页总量依据、逐次观察与不可变配置版本。身份来自已核对GatewayContext和实际授权关联；每页经过当前六阶段、实际scope、应用、账户原生ID快照、账户/店铺权限、授权版本、Governed准入、请求/响应档案及SyncStore租约游标守卫。HTTP外不持PG长事务。解析、页记录、镜像、版本和检查点在同一提交事务，投影失败整页回滚。

数字分页显式声明首值1，并校验实际响应页码/大小/总页数/总量和逐页稳定性；同页和跨页重复ID、缺页、总量变化、其他账户数据、float/bool ID均不推进。源上下文新增店铺、授权、授权版本及分页协议，不能跨店铺或授权版本续跑。失败保留旧镜像，按原run_id从已确认页续跑；配置相同仅追加观察，配置变化追加before/after和来源版本，不覆盖历史。乱序旧观察不覆盖较新配置。

SDK原生int64 ID和预算保留精确十进制字符串，不经JS浮点或单位猜测；预算unit/currency/budget_minor未知，不补0。保留原生状态和业务标签，当前不推定前端运行状态；未确认的时间单位不写source_updated_at。请求只采账户标识/业务别名/状态/预算或计划标识/业务标签/状态/预算，不请求联系人、执照、订单PII；安全存档投影不保存标签。

资产内部UUID与可执行`campaigns`目标分开：账户级计划列表不能证明店铺归属。只有维护身份持独立、有效关系依据才能登记计划与店铺的内部映射；拉取所用店铺仅作来源，不当作所属店铺。resolve_campaign重新核对实际店铺/账户权限和关系期限；BOUND和当前平台AUTHORIZED分别计算，撤权后缓存仍BOUND但不再AUTHORIZED。镜像不产生执行能力，所有asset.executable=false；旧版CAMPAIGN/1.3不能作为ADGROUP/v3写入，执行登记SQL和网关出站均拦截。

PlatformRuntime.sync_adq_assets已装配固定服务端入口、真实私有资源检查、现有治理客户端和加密游标。未新增任意URL/query代理、公共同步授权入口或计划定时器；现有私有资源仍未绑定业务租户/不同店铺，保持disabled。

## 验证与复核

已执行：Ruff 283文件、mypy 229源码、654项单元/契约、12项镜像专项、5项SDK来源专项、8项原生授权交接、12项同步基线、21项执行准备专项通过；新增发送前层级变化用例一并通过最终全量。最终全量717项通过、1项生命周期默认跳过，483.11秒。保留的开发库已迁移0045，26类相关记录为0、迁移角色NOLOGIN恢复；见development.json。平台HTTP、主体、scope和关系证据均为合成测试，PG/Redis/会话及迁移为实际本地设施，不能登记P验收通过。

mirrors.json保存实际本地request/outbound/run ID、两页结果、精确整数镜像及配置before/after；来源和缺陷留痕见verification.json、source-spec.json。首次全量发现空scope资源被镜像装配拦住授权回调，现保留授权入口、镜像关闭，8项交接专项及717项全量通过；初次失败仍如实保留。

专项覆盖：完整多页/原run重投不重复HTTP、超JS安全整数、同native不同账户、配置重复与变化、第二页拒绝后续跑、跨页重复/总量漂移/缺页/串账户/float、独立第二店铺、无关系目标关闭、跨租户RLS、运行角色不可改版本/自签关系、撤权、授权版本变化、投影后故障整页回滚。缺陷与修复见verification.json。

原06/12未提交文档各2行保持原样，不进入本卡提交。回滚采用前向停用补充目录或源同步，保留资产与档案，不删除业务记录；数据库迁移不提供破坏性downgrade。BE/F04/F07具名复核PENDING；外部实际账户/计划各页、真实关系与金额/时间口径待E01核对，未冒充真实验收或整体完成。
