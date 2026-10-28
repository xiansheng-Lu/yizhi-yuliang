# M1-11 商品、SKU与库存（商品ID同步纵切）

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
