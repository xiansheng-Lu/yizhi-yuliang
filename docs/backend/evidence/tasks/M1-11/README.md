# M1-11 商品、SKU与库存

当前状态WAITING_REVIEW：商品ID及详情/SKU/单位状态依据/软删守卫已本地技术通过；真实E02与具名复核待完成。最新实现和验收见末尾“详情、SKU与规范投影补齐”。

以下商品ID纵切内容为此前阶段快照，历史未完成项以末尾最新记录为准。

2026-10-28，负责人BE，协作F05/F09。IN_PROGRESS。基线ef40e99。部分范围技术通过，整卡SKU/库存/价格/软删验收未完成，真实P验收与BE/FE具名复核未执行，不记整卡PASS或DONE。

## 范围与输入

复用M1-07原生只读协议与M1-15a/b实际同步仓储、M1-02能力证据。当前已核验 /channels/ec/product/list/get 只返回商品标识与next_key，不能证明SKU、库存、价格、删除或上下架状态。用户最新明确两个独立项目属于yizhi租户；不自动建立具体shop/account UUID与scope关联，不轮换共享token。沿用两个不同店铺的关系。

## 实现

shop_products.product_id_page精确保留uint64十进制标识、剔除未证明字段，拒绝重复ID、畸形标识、超请求页、无记录继续页及过长游标；空终页仅为空观察，不生成删除。

ShopProductIdSync完成独立应用/实际scope/原生账户关联/六阶段/授权版本守卫，固定商品ID路径和页大小，Governed配额及脱敏档案、SyncStore原子页提交与失败/限流停止、原run续跑。PlatformRuntime增加独立最小字段策略装配及sync_shop_product_ids受控服务端入口。不开放任意HTTP路径、公共写接口或自动定时任务。

补齐既有OperationSpec分页依赖：明确幂等READ且有cursor参数时，允许空串首游标；None、空串与正数字首值严格区分，写spec/非幂等/无cursor/非法首值拒绝。小店固定使用空串，不放宽ADQ数字规则。无新迁移，复用sync_resource_mirrors。

## 实际验证

环境WSL Ubuntu、Python3.13、真实本地PG/Redis，上游HTTP、主体、scope及权限证据全部合成，平台/付费调用0。

- `python scripts/test_infrastructure.py --group shop-products --details`：4 passed，7.90秒。完整2页与精确最大uint64镜像/字段、完成run零重发；失败游标续跑与撤权；新run部分失败保留历史镜像；缺scope出站前拒绝。
- `pytest tests/unit tests/contract -q`：696 passed，最终6.08秒，其中本卡10项单元；包含空串首游标限制回归。
- 新增源码、runtime与对应测试Ruff通过；5文件mypy通过。共享http变更亦纳入全单元/契约回归。
- 专项后开发库只读快照：5类同步/授权记录均0，app_migrate为NOLOGIN，查询时head0046。其后主agent统一升级0047，最新开发库核对由M1-10证据负责；本卡不冒充新迁移验收。

## 三条DoD与剩余项

1. 商品/SKU稳定标识与价格/库存区别：本轮只完成商品ID观察；SKU、整数分价格和库存0/未知仍待详情/库存协议与实现。
2. 跨店铺隔离、失败保留：实际仓储依照tenant/source隔离，当前专项验证失败不删除历史观察；完整跨店铺SKU场景后续补充。
3. 软删：当前不产生删除/下架；需要独立明确状态证据与后续策略实现。

来源：本仓库native_reads.py和resource-handoff/资源交接与原生适配说明.md；独立D:/codex/wx_shop的sdk/wxshop_api_registry.py、dashboard/collect_data.py有详情路径与示例字段，但示例不当作正式单位/状态/完整SKU证据。官方详情页 https://developers.weixin.qq.com/doc/store/shop/API/channels-shop-product/shop/api_getproduct.html 本轮打开失败，未取得正文。列表来源已在既有交接证据核对。

缺口：正式详情/库存协议、SKU镜像与价格/库存/软删策略、当前完整真实账户映射与scope及E02平台验收、E08具名复核；授权版本变化导致原来源上下文不匹配时明确拒绝，不跨版本继续游标，后续按来源换代任务收口。现有技术纵切不得替代这些验收。

回滚：停用商品ID调用方或关闭应用的该只读接口；保留已有档案和镜像，不执行破坏性迁移。证据见verification.json和development.json。

## 2026-10-28 详情、SKU与规范投影补齐

本轮在用户持续多Agent授权下完成M1-11详情技术链路，真实P验收与BE/FE具名复核继续保留。固定第三方SDK zsmhub/wx-channels-sdk@97497af9d9cd3dfbb30ca71aa8c0bc8be52207c6 的 apis/商品-获取商品.go 提供POST /channels/ec/product/get、product_id请求及product/skus/sku_id/sale_price/stock_num/status响应字段；与独立wx_shop实际采集器路径及stock_num字段交叉核对。官方正文仍未取得，SDK字段不证明金额单位、状态语义、实时性或实际应用权限。

0048_shop_product_details增加不可变商品详情页观察、维护角色独立单位/状态依据，以及tenant×shop×account×native商品/SKU内部UUID映射。原生商品/sku标识精确保留uint64文本、整数数值保留精确原生文本；PII、标题与任意自由字段不采集。缺SKU字段与空列表区别，不根据列表消失删除SKU/商品。

ShopProductDetailSync经共享require_shop_read核对实际应用×scope×native关联×店铺账户×六阶段×授权版本，固定详情路径，使用Governed/档案/SyncStore页意图和事务，页/商品映射/观察/游标同事务。失败保留旧值并显示UPSTREAM_ERROR和最新尝试时间；无明确新鲜度策略保持UNCONFIRMED_NO_POLICY，不杜撰TTL。缺失平台更新时间保持sourceAt=null。

投影默认单位和状态未知；只有当前独立有效的维护依据才能把sale_price归为CNY_MINOR、stock_num归为ITEM_COUNT和原生状态归为ON_SALE/OFF_SALE/RECYCLED。规范金额/库存使用安全整数；超JS范围仍仅保留精确原生文本。回收站仅在明确状态映射依据下软删，撤销/过期/替换依据不回退旧依据。依据注册核对当前具体小店详情绑定、真实账户/店铺关联与有效期；ID不可覆盖复活，新依据在同范围锁内原子停用旧依据。依据均为本地合成技术输入，不冒充真实平台单位或状态证明。

三条DoD的本地技术结果：商品/SKU独立稳定ID及0/未知区别通过；授权第二店同原生商品ID不串读、同步失败保留旧观察和故障新鲜度通过；仅明确依据和原生状态才产生软删、不可见/缺SKU/平台失败不删通过。真实店铺SKU/金额/库存与来源抽样仍待E02。

实际命令/结果：新增协议7项单元通过（0.37秒）；详情5项真实PG/Redis集成最终通过（6.71秒），含当前/失效/替换依据、跨店同ID、缺SKU、故障保留、投影异常原子回滚、普通角色不能改观察；新增范围Ruff/mypy通过。首次专项被另卡0050复合FK阻断，修正后可完整空库迁移至0051；另一次故障测试误断言只读恢复绝不重读，改为允许新意图重读幂等只读且保留旧档案，代码不把只读重读当平台写幂等证明。平台/付费调用0。

下一READY技术卡M3-13a：身份/隔离已有技术输入，现有commerce_products内部UUID和商品范围可作为云文档FK。云稿需F09显式X-Shop-Id接线，真实P与人工签字仍保留，不将该技术推进等同门禁通过。
