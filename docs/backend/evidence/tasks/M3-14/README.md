# M3-14 原子派生与继承

2026-10-28，BE/F09，WAITING_REVIEW。基线main d0caf14，本地codex/backend-content-plan-readmodels。依赖M3-13a/15b实际证据及M3-16真实SAVE结构/素材守卫切片；未将生成/SOP未实现入口视为已验收。

范围：0063_content_derivations、POST /v1/content/documents/{id}/flow、FlowService与现有内部helper统一。按实际frontend flowDocument继承brief/body/scenes，重建新ID、视频三节点/图文默认画板、editing保留settings。稳定请求键绑定父ID/版本/目标kind；同事务写子稿/首版和只追加派生关系，失败整体回滚。

验收：五类真实PG派生与实际TS黄金对比；同键并发/重试一子稿、父稿改版后原键仍回原结果；错版本/越权/移除/伪造关系拒绝及回滚。实际专项5项通过6.82秒，9项单元通过0.62秒；完整集成891通过/1可选重启跳过（672.28秒）覆盖最终新增负例。五类PG派生结果经实际TS flowDocument/validateContent黄金对比通过，精确范围见verification.json和本批content-plan-verification.json。公开HTTP body为kind/version/idempotencyKey，沿用Cookie、CSRF与X-Shop-Id，F09云UI接线和BE/FE签字仍PENDING。

回滚为前向关闭派生路由，保留不可变版本与请求关系，不删用户正文/素材。平台与供应商调用0，06/12他人修改不夹带。

派生关系记录只追加，数据库复合外键及触发器核对父版本、子首版、目标kind、parentId和商品同一性，伪造关系直接app_login 22023拒绝。子稿/父稿后续编辑不改变原键首版响应；子稿归档后原键终态不复活。真实F09云UI、跨设备页面交互和BE/FE签字仍PENDING，未冒充浏览器验收。
