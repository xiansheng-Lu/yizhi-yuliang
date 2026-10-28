# M3-15b 已确认素材读取、引用与归档

2026-10-28，负责人BE，协作F09。WAITING_REVIEW。后端本地技术与实际PG/MinIO/HTTP专项已通过，F09云UI与BE/FE具名复核仍待完成。基线main739b721，本地分支codex/backend-comparison-assets。依赖M3-15a图片/UTF8文本确认与实际对象版本已有技术证据；人工与生产/F09浏览器门禁保留。

## 实现

素材读取使用团队店铺/商品/账户与角色/data_scope，不沿用上传计划仅actor可读取的规则。只列确认且未归档的图片/文本metadata；CONFIRMED表示既有完成事实，不把metadata当文件当前存在证明。下载实际HEAD固定VersionId核对size/MIME/upload-id及可用SHA证据，成功后才短签同VersionId；缺失/不可读明确502，不生成假可用URL，不回退最新对象。

BlobStore本实例实际HEAD产生60秒HMAC证明，绑定bucket/key/version/size/mime/SHA/asset；最终签名必须提供该实例的有效证明。默认缺守卫、伪造证明、不同实例、变更上下文或过期均拒绝。证明只在服务端内存，不入API/数据库/公共日志。API不接受key/bucket/version覆盖。

Document模型仅校验UUID引用结构与最多32个唯一素材。实际引用统一在ContentService.save的事务外有限并发HEAD预检，再在文档与按资产UUID排序的锁内重验当前权限/范围/状态/不可变版本证明；文档、不可变版本历史和素材版本引用同事务保存。所有内部_save入口缺完整证明默认拒绝，review/flow内部准备函数统一经save，不存在移除模型限制后直接信assetId的捷径。图片层只允许已实际确认image；特定商品素材不能跨商品，同店通用素材按明确规则允许；未探测音视频仍禁。

0059_content_asset_references接0058_analysis_comparisons：文档历史素材引用只追加，不授权runtime修改/删除；外部GENERATION/SOP/DELIVERY引用提供同事务内部入口，但实际生产者未全部实现，不宣称已完成其业务链路。登记active引用也须实际版本短期证明。归档只写deleted_at，不物理删S3版本；历史或active引用存在则拒绝。

数据库触发器让引用插入/激活与归档共享同资产advisory锁，并检查确认/归档/移除商品状态，阻止runtime直接SQL绕过互斥；历史引用无UPDATE/DELETE权限。归档service用FOR NO KEY UPDATE先行以配合FK KeyShare。commerce_products新增本地移除deleted_at，保存/新增引用拒绝已移除商品；该本地字段不根据列表消失或未知平台状态自动推断。

## 待实际验证

拟运行 `python scripts/test_infrastructure.py --group content-assets --details`，9项真实PG/S3/HTTP场景：团队与actor区分、未确认不列表、跨店/租户；固定旧版本不取覆盖新值、缺文件不发签名；文档保存/内部入口/历史引用；错误类型/商品/伪造ID整单拒绝；service归档与引用竞争；真实app HTTP读/下载/文档引用与归档拒绝；raw SQL归档/激活互斥、历史引用不可改删；移除商品新增引用拒绝。

新增3项单元与原文档14项合计17通过；content/blob/本卡测试静态检查通过。实际命令结果以verification.json最终记录为准；当前不将编写测试等同运行通过。

## 资源与回滚

本卡仍仅图片与UTF8文本。F09完整云UI/双浏览器操作未运行，生产私有对象配置未放行，实际SOP/生成/投放引用生产者须后卡真实接线。回滚可关闭资产入口/禁用blob；保留对象版本和引用，不破坏性downgrade，不删除用户对象。


## 最终实际验证

唯一串行DB窗口内依次执行，2026-10-28：

- `python scripts/test_infrastructure.py --group content-assets --details`：9 passed，9.78秒。
- `python scripts/test_infrastructure.py --group content-uploads --details`：7 passed，10.43秒；适用新签名守卫与归档终态回归。
- `python scripts/test_infrastructure.py --group content-documents --details`：5 passed，7.35秒；适用文档统一save/review/flow内部入口与软移除商品回归。
- 本卡3项单元及原文档14项合计17通过；本卡content/blob及相关测试Ruff、mypy通过。

9项专项实际覆盖：同团队另一授权成员可读资产，但不能读取上传者私有计划；未确认不出列表、跨店/跨租户拒绝；覆盖S3最新对象后仍实际下载原VersionId，删除原版本后拒发签名并拒绝新文档保存；图片引用保存与内部review统一重验/历史追加；移出正文后历史仍阻止归档；无store、缺证明、伪造证明及private_save不能绕过；错误kind/商品/随机ID不部分保存；service及runtime直接SQL的引用/归档竞争均只一方成功；runtime不能修改或删除历史引用；新引用拒绝本地已移除商品；真实create_app的metadata/下载/带素材文档PUT/归档423及拒绝客户端VersionId覆盖通过。

所有实际文件为测试自产图片/文本、实际本地MinIO版本对象，平台/付费调用0。无测试手工include_router或绕过main装配。迁移0059在新空库实际应用，历史表/触发器/RLS/权限约束真实执行。不会把内部SOP/GENERATION/DELIVERY引用仓储和故障用例冒充这些生产者或真实供应商已联调。

新增归档边界还修正旧上传完成读取：asset归档后重试原complete明确RESOURCE_TERMINAL，不500、不新建、不复活。BlobStore原始download_url现在也必须携带本实例实际HEAD的短期证明，原上传卡内部下载回归已先check_version后发签，不留下忘记验证即可签任意版本的默认路径。

三条DoD本地技术均有对应实际证据；F09完整浏览器/双设备UI、生产存储权限与实际后续生产者接线继续单列，不记DONE。下一READY技术卡M3-16内容服务端统一校验器，在主Agent完成本批全量/提交切新分支后继续。
