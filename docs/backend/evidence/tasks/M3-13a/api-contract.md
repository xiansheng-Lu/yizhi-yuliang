# F09 云文档HTTP接线样例

本卡只交付CRUD。现有前端仍使用本地草稿，需F09明确实现云适配；当前新增的店铺范围头不是已完成前端接线证明。

所有请求沿用登录Cookie，显式 `X-Shop-Id: <当前ContentOwner.shopId>`；写请求还需 `X-CSRF-Token`。来源身份和tenant由Cookie确定，服务端不接客户端tenant/user覆盖。CORS允许X-Shop-Id由主Agent统一接入。

- `GET /v1/content/documents?kind=planning&productId=<内部商品UUID>&cursor=<末条文档UUID>`：数组，planning同时含scripts；每页最多100，kind/productId/cursor可省略，按id游标稳定分页。productId空串可查未关联商品稿。
- `GET /v1/content/documents/{id}`：完整ContentDocument。
- `PUT /v1/content/documents/{id}`：完整ContentDocument，无shopId/tenantId/revision额外字段；新建version=1，首次返回1，已有稿以期望version保存并返回version+1。id和path必须一致；updatedAt服务端生成；parentId保留null。
- `DELETE /v1/content/documents/{id}`：JSON `{ "version": 2 }`，成功data=null；在途RUN/GENERATION持久引用423，已软删后404。

成功沿用 `{requestId,data}`。冲突样例：

```json
{"requestId":"...","error":{"code":"VERSION_CONFLICT","message":"文档已更新，请保留本地副本并核对当前版本","details":[],"retryable":false,"current":{"id":"...","kind":"planning","version":3,"parentId":null}}}
```

current实际为完整当前ContentDocument，上面只缩写展示。F09应保留本地副本并展示当前云稿，不自动覆盖重试。

无X-Shop-Id/非法头422；无登录401；CSRF缺失/伪造403；跨店/商品范围403或不存在404；内容revision额外字段422；版本旧409；未接素材引用422。未提供公共flow/review端点，不调用缺幂等和SOP依赖的派生流程。
