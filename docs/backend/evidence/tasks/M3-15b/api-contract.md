# F09 已确认素材API

新增接口均Cookie登录、显式X-Shop-Id，归档还需X-CSRF-Token。沿用requestId/data信封。素材scope按当前团队权限，不要求上传者本人；上传计划接口仍actor私有。

- GET `/v1/content/assets?productId=&kind=&cursor=`：最多100 metadata数组，kind只image/text，未知kind422；productId空串查本店通用素材，cursor为末条资产UUID。只列已确认未归档素材，metadata无key/bucket/objectVersion。
- GET `/v1/content/assets/{id}`：本范围已确认metadata。
- GET `/v1/content/assets/{id}/download`：实际HEAD固定确认版本后返回 `{assetId,url,expiresAt}`；无客户端key/version/bucket参数。文件缺失或存储拒绝502，不能把返回metadata当文件存在证明。
- DELETE `/v1/content/assets/{id}`：空body，逻辑归档，成功data=null；有文档历史/active引用423。重复归档幂等，不删除S3版本。已归档不再列表/签新下载URL，也不能新增引用。

云文档PUT仍完整ContentDocument/version锁。UUID素材引用现在经服务端当前scope、kind、确认状态、实际对象版本校验；图片图层仅image，音视频未探测仍拒绝。任一引用失败不部分保存文档/版本/引用。前端保留冲突副本，不自动覆盖。无私有存储/缺守卫时非空assetId明确拒绝。

素材即使从新正文移除，历史版本仍引用时也不能归档，以免破坏历史。当前没有公开“释放历史”或SOP/生成/投放引用自授接口；后续生产者须在生命周期事务使用内部guard。已有短签为期限内bearer，不能宣称应用撤权可即时撤回已签URL，后续发签需重新核权。

F09当前云UI仍待接线，以上是可审阅的后端接线契约，不代表浏览器验收已通过。
