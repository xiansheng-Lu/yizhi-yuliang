# M3-14 派生HTTP契约

POST `/v1/content/documents/{parentId}/flow`；Cookie + X-CSRF-Token + X-Shop-Id，JSON `{kind,version,idempotencyKey}`。kind沿用五种DocumentKind，version为父稿当前正整数版本，key为1—200个ASCII字母/数字及._:-，首字符字母或数字。只接受固定body，query身份/范围覆盖拒绝；body额外字段拒绝。

200 data为普通ContentDocument首版，id/分镜/默认画板/节点为新UUID，parentId为实际源稿。父标题后缀、brief/body/scenes、图文prompt、视频默认三节点、editing规格继承与当前frontend flowDocument一致；默认不复制旧对话、画板图层、节点边、素材剪辑。派生标题过长明确拒绝，不静默截断。

相同tenant×actor×key重复返回同首版子稿；换父ID/父版本/kind复用key为409 IDEMPOTENCY_MISMATCH。新key但旧父版本409 VERSION_CONFLICT附current父稿。父子后续编辑不改旧响应，子稿已归档则RESOURCE_TERMINAL不复活。越权/跨店/移除/无当前商品范围拒绝。引用、历史、派生记录失败整体回滚；私有素材的既有守卫不因flow绕过。

现有ContentService.flow仅内部兼容，调用时产生新key；真实SOP/run-step必须使用FlowService的稳定业务键、父版本，不可用随机兼容key冒充业务幂等。F09云端适配及交互待FE接线，不声称现IndexedDB本地flow已调用此HTTP。
