# M3-13a 云端文档CRUD与文档版本

负责人BE，协作F09；WAITING_REVIEW。本卡后端技术已通过，F09浏览器/双设备接线及BE/FE具名复核待完成。基线main 0328a0e；用户持续多Agent授权，本地分支codex/backend-content-analysis。直接技术输入M0-12、M0-08b和M1-11商品详情/内部UUID映射已有实际本地验证，E02真实平台和E08具名复核继续保留。

范围：五kind完整ContentDocument云保存/读取/列表/软删、单文档revision映射version、版本历史、在途引用保护。0051_cloud_documents依赖0050_shop_orders_settlement，商品FK复合tenant×product×shop，父文档tenant复合FK。当前无真实SOP/生成任务生产者；reference_in_transaction为其后续同事务接入入口，技术专项直接使用真实PG持久引用验证删除阻断。不宣称M3-14/15/16/生成已完成。

服务端绑定当前Cookie身份，写请求CSRF，所有云请求显式X-Shop-Id。body沿用五kind/完整字段，updatedAt由服务端重写，初建version=1，保存根据同一document id的DB revision做并发裁断并返回递增version；ContentState.revision未知字段拒绝。旧版本409回传error.current，供客户端保留副本。服务端不采纳客户端tenant/user/shop字段。跨店/商品/父文档范围拒绝。

无素材云稿可保存；非空assetId在M3-15b可用资产校验未交付前明确422拒绝，不将本地assetId当作云端私有对象授权。内部flow/review为准备函数，无对应公共路由；公共派生需M3-14/M3-16和幂等依赖后另卡验收。F09目前本地草稿未改云适配，不宣称浏览器/双设备联调完成。

三条DoD：完整五kind/fields/version与DB revision一致；同版本并发仅一方成功，409包含当前文档；跨租户/商品范围与持久在途引用删除保护。真实PG/HTTP专项和API样例见后续更新及api-contract.md。

验证入口：`python scripts/test_infrastructure.py --group content-documents --details`；`pytest tests/unit/test_cloud_documents.py -q`。结构单元14项通过；Ruff/mypy新增范围通过。首轮3项PG通过，HTTP因真实app尚未接路由404未通过；主Agent已真实接main和CORS，等待共享测试库窗口复验。未用测试手工include_router掩盖装配缺项。

回滚：关闭云文档入口，保留soft-delete与版本历史，不执行破坏性downgrade。未读/轮换平台凭据，无真实平台/付费调用。


## 最终技术验收

2026-10-28，`python scripts/test_infrastructure.py --group content-documents --details` 最终5 passed，6.06秒。真实create_app装配、真实PG/Redis、实际Cookie登录/CSRF/header/CRUD响应，无测试手工注册路由。空库按当前实际head迁移包含0051；新增14项结构单元通过，新增范围Ruff与6文件mypy通过。

5项专项覆盖：

1. 同版本两编辑线程仅一方保存，另一方得到带当前version=2的冲突；历史表持久化两版；伪造商品失败不改变旧稿。
2. 内部派生准备保持planning/scripts列表规则；真实持久GENERATION引用使删除拒绝，解除后soft-delete，派生稿独立保留。该内部准备不作为M3-14或公共flow上线证明。
3. 显式错误店铺无权读取，父文档与商品范围不一致拒绝。
4. 真实HTTP登录、无CSRF403、无店铺头422、PUT创建/保存、旧版409及error.current完整当前稿、未知容器revision字段422。
5. 跨租户直接SQL看不到文档，普通app角色不能修改历史；RUN引用与删除同时竞争仅一方成功，不存在已删稿同时保留active引用。

具体限制：客户端无素材云稿可保存，图片/音视频素材引用待M3-15a/b可用对象校验后开放；实际生成/SOP生产者尚未装配，须后续在创建/终结事务中使用reference_in_transaction，不从持久引用仓储测试推断真实供应商已集成。前端仍本地草稿，F09未联调；E08与适用上游E02不自动通过。未执行平台/付费调用。下一READY技术卡为M3-15a预签名与完成确认，直接依赖本卡及M0-04本地对象设施。

首轮缺陷已闭合：列表nullable参数缺显式CAST导致PG无法推断类型；测试重复登录遇已有会话CSRF，清理测试Cookie后重登；真实main装配缺项404由主Agent接路由解决。拒绝用测试手工注册路由回避实际装配。
