# M3-20a 素材账号交接意图

IN_PROGRESS：关联/取消及服务器权限/固定文件证明已实现，本卡真实PG/MinIO/HTTP技术验证已通过；整卡的F09/F11与正式平台上传/审核来源未接入。

本批基线main591447df0a4d4b102d66143a3d5e5e4960a3be8f；本地codex/backend-workflow-execution-links-events。
原readiness.md是此前PLANNED准备快照，不作为完成证据。

## 实现范围

新account_links_models/service/routes.py；0067_content_account_links下接0066_workflow_execution。普通角色历史事件只有SELECT/INSERT；意图状态与事件同事务，稳定唯一键tenant+asset+productLine+account，asset/shop/account复合FK。

实际API：GET/POST `/v1/content/assets/{asset}/account-links`、POST `/{asset}/account-links/{id}/cancel`，复用真实Cookie/CSRF/X-Shop-Id。新增只接受productLine/accountId/expectedVersion，客户端nativeId/linkedBy/mode/文件证明不可提交。

新增先AssetService.prepare_reference固定版本HEAD/HMAC，事务内同资产咨询锁+当前素材店铺/商品权限重验+固定版本证明比较。关联意图不创建DELIVERY在途引用，不授予UPLOAD，不调用token或平台。

access.content_account_link_target为最小SECURITY DEFINER入口：固定pg_catalog,pg_temp search_path，仅当前actor/tenant、active role和双向scope，固定五产品线provider/product/connector映射，当前主体一致、账户AUTHORIZED、native快照对应active未过期OAuth；仅返回账户name/native/entity。必要共享行锁在受控入口内执行，不扩大账号/主体/OAuth UPDATE权限。

同键重复保留原linkedAt/linkedBy；当前快照已变明确冲突。取消/重新关联递增revision并保留事件。取消不HEAD、不需要目标OAuth恢复，但仍验证当前店铺/商品content.write权限；已归档素材可取消，不能新增。取消不删对象或任何平台证据。

返回分层intent=LINKED/CANCELLED，link为实际ContentAccountLink字段；upload=NOT_INTEGRATED/platformAssetId=null；review=UNKNOWN。私有存储CONFIRMED不是平台上传成功，无正式producer不造审核/投放结果。列表当前scope裁剪，1001探测/1000上限显式拒绝截断。

## 当前验证

- 8 unit PASS/0.23秒，拒伪身份/原生ID/文件版本/MOCK/非法UUID/boolean版本；版本边界。
- Ruff与mypy本卡5文件PASS。
- `python scripts/test_infrastructure.py --group content-account-links --details`：14 PASS/13.14秒。真实PostgreSQL/MinIO/实际main路由：固定文件证明/同键幂等/并发、版本冲突、事件失败回滚、取消与归档、目标OAuth撤销或HEAD离线仍可取消、跨tenant/shop/actor、native漂移/主体不符/过期授权/未知账户拒绝、缺固定文件拒绝、Cookie/CSRF/query及伪actor拒绝。
- 目标ADQ授权通过已有OAuthService+SyntheticExchange独立建立，商品fixture原WXSHOP授权未冒充ADQ来源；全部上游业务授权和输入均为合成，非真实平台验收。
- 0067安全锁函数已在实际PG调用通过；未扩大账号/主体/OAuth表UPDATE权限。历史事件普通角色UPDATE被数据库拒绝，意图不插入DELIVERY引用。
- 本轮591447df M3-20a DB窗口已向root/reports明确释放，随后未追加DB；源码/DDL冻结。迁移Python文件只补Ruff格式/导入排序，不改变SQL语义。
- `node .runtime/contract-tools/node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_content_account_links_frontend.mts .runtime/content-account-links-fixture.json`：PASS，实际HTTP测试回执经过冻结前端accountLinkKey/validateAccountLinks，重复与跨tenant两反例拒绝，网络调用0。首次ES named-import在本机tsx/CJS互操作失败；按仓库既有helper改createRequire后通过，不修改前端类型或validator。
- F09/F11真实浏览器UI未接；平台素材上传/审核producer仍NOT_INTEGRATED/UNKNOWN，不把关联意图或私有存储CONFIRMED称为平台上传/投放成功。

无新增平台/付费/业务写调用，未动原token配置或Git。main/runner由主Agent统一装配。
