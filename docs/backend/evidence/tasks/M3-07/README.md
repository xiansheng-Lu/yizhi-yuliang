# M3-07 V9目录与字段上下文能力

负责人BE，协作F11；WAITING_REVIEW。基线db239cc；实现d55fc2b、契约工具修复5038eff；M1-02/M1-17技术输入可用，按持续开发授权推进；G0/E08及真实平台证据保留。

三条DoD：完整version/contextKey/有效期/类型单位通过AdvertisingService校验；目标/版位分别计算、过期降级；未对接产品不可写、未确认字段明确状态，不伪造平台枚举。

## 实现与边界

真实只读HTTP：`GET /v1/delivery/configuration/catalog?product=adq`和`GET /v1/delivery/configuration/capabilities`。沿用Cookie、精确Origin与服务端身份，不接受客户端租户/授权覆盖。完整15维上下文与前端JSON键一致，目标/版位按JS UTF-16排序；版本签名绑定上下文、字段依据、目录、成员权限和实际连接状态，重复查询不因当前时间变化产生假版本。有效期独立输出，最多60秒并截断至来源证据/授权期限。

0041保存精确上下文的字段依据修订，维护身份写入、运行身份只读；依据ID不可替换、原事务回滚不泄漏新修订。可用字段必须核对实际账户/产品、能力绑定版本、六阶段证据、实际授权版本和scope；不同目标/版位不回落到同账户旧枚举，过期/撤权/来源失效立即降级。目录只查询实际主体、双范围权限及已存在的店铺/账户关联，未映射时返回真实空目录。

字段覆盖现有42项、6态。金额按整数分、微信豆按整数豆，比率精度须有依据；无证据时不提供枚举、数值限制或可写默认值。只有应用草稿名标PUBLIC，不能据此推断平台名称限制。创建执行器、素材上传、人群估算归后续卡，当前写通道NONE，创建核验标志全部false。

## 已运行验证

- 637项单元/契约、21项真实PG与会话HTTP专项通过；Ruff 262文件、mypy 212源码通过。全量基础设施672项通过、1项生命周期演练默认跳过，用时438.63秒；未把跳过计为通过。开发库迁移0041，迁移角色NOLOGIN，字段依据及另外14类业务/事件记录均为0；详见verification.json。
- `context.json`来自实际测试接口输出，主体/账户/授权与字段依据全部显式合成；0次真实平台/付费调用。覆盖精确目标/版位、全部六态、零值、撤权、授权版本/scope、过期、双范围、跨租户、维护入口、回滚/重放、只读SQL和HTTP拒绝场景。
- `frontend-check.json`通过未修改的真实AdvertisingService读取归档，42字段、6态、6项非法变体；注入fetch 14次、网络0次。使用归档HTTP核验时刻回放有效期，不延长旧响应，也不算浏览器验收。
- 目录重构后旧契约工具仍指向根src，现改读frontend/src；保留原15个源码哈希与冻结逻辑名称。AST仍56调用/133类型/53路由；45黄金检查及Python冻结检查通过，未执行--write刷新基线。

复跑前端契约：`node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_configuration_frontend.mts docs/backend/evidence/tasks/M3-07/context.json`。依赖安装位置按frontend实际node_modules解析；本机复用了原根依赖目录。

专项期间修复了SQL把scope的text[]误当JSON数组的问题；另一个故障测试尝试修改不可变能力版本，被原有数据库守卫正确拒绝，已改为保留拒绝断言。没有降低既有守卫。

BE/F11具名复核PENDING；真实平台验收NOT_RUN。当前实际项目资源未绑定到业务租户/不同店铺，未建立假业务映射。
