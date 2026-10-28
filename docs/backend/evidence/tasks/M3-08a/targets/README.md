# M3-08a 推广资产targets切片

2026-10-28；负责人BE，F11复核PENDING。基于原audiences切片的实际技术验证；E01真实推广资产账户/scope及完整上下文适用依据未取得，平台调用0，不登记整卡DONE。

已核对固定官方TencentAd SDK revision 81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9。GET /v3.0/marketing_target_assets/get必传marketing_target_type，可选account_id/filtering/page/page_size/organization_id/fields，返回MarketingAssetStruct及PageInfo。本实现只支持明确账户模式：account_id必传，不发送organization_id/filtering，不从广告计划、微信小店商品或用户关键词生成资产。精确来源与摘要见source-spec.json。

## 实现

复用既有受控同步完整分页、当前应用/授权/原生账户/六阶段守卫和不可变分页事务，通过TargetLookupSync专用spec/query/parser读取实际marketing_asset_id/name/type/is_deleted。properties及其他未列字段完全不请求、不保存、不作为适用依据。平台模型没有返回account_id，本实现不会伪造响应账户字段；来源归属依靠明确请求账户、当前授权范围及独立上下文依据。

LookupSourceProof新增kind=targets、固定SDK已列的marketing_target_type及marketing_asset_types允许集合；目标类型和资产类型之间的适用关系由维护方独立核对，未默认推断。当前只实现SDK小集合，未实现或未知类型不自动开放。只有精确上下文依据仍有效、原生is_deleted严格为JSON false且资产类型在核对集合中，才eligible=true。true/缺失/null或类型未知均不可选；数字0及文本false不能冒充布尔false。

0056_lookup_target_assets依赖0055_analysis_execution，将来源当前索引改为tenant_id+kind+context_digest。audiences和targets在同一上下文的维护依据互不覆盖；旧依据缺少新可选字段时通过模型规范化保留幂等兼容。当前来源读取还核对payload.kind与实际列一致，来源绑定版本及授权版本变化立即拒绝。

LookupService复用冻结LookupPage和HMAC游标，reader.kind区分来源、快照及输出。搜索只过滤完整真实来源，任意q不生成选项；未接入/失败不返回假空集合。parentId/category没有固定协议依据时明确拒绝。root负责runtime target_lookups、sync_lookup_targets和HTTP options按kind装配，本切片HTTP测试实际运行该runtime选择器及既有登录/权限链。

## 验证与边界

- 协议单元：`pytest tests/unit/test_lookup_protocol.py tests/unit/test_lookup_targets.py -q`：18 passed，0.79秒。
- 真实PG/Redis与HTTP：`python scripts/test_infrastructure.py --group lookup-targets --details`：4 passed，7.31秒。覆盖同context不同kind不覆盖、完整分页及幂等、删除/未知删除/错误类型不可选、properties不落库、任意关键词不制造资源、授权版本变化/上游失败关闭、真实runtime kind dispatch返回冻结HTTP契约。
- audiences必要回归：`python scripts/test_infrastructure.py --group resource-lookups --details`：7 passed，10.57秒。
- `mypy src tests/unit/test_lookup_targets.py tests/integration/test_lookup_targets.py`：188文件通过；Ruff通过。

以上实际使用本地PG/Redis/会话HTTP，应用身份、scope、适用依据和上游返回仍为合成，不是P验收。此前原项目SDK安全迁移后的真实只读成功属于另一范围，不用于代替本资产接口的实际scope或适用性证明。

回滚为停用该应用targets只读入口/撤销对应kind维护依据，保留历史；不做破坏性降级。audiences保留独立来源。其他LookupKind仍待来源与实现，platformTemplates前端表示真实定向包，不能用创意模板接口冒充；整M3-08a父卡仍未全部完成。
