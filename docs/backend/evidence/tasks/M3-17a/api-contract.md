# F10 SOP初始化API

所有路径均为/v1/content，Cookie会话、非GET请求X-CSRF-Token和所有请求X-Shop-Id(UUID)必需。productId明确为V9 products返回的commerce_products.id，不是旧本地canonical products.id；旧本地稿商品映射归M3-13b/F09，不隐式映射。

- GET /workflow-templates：最多200条系统/本租户版本，返回id/tenant_id/code/version/name/is_system/definition。
- POST /workflow-templates/{sourceId}/versions：复制任一可读模板或追加本租户代码版本。提交完整{code,name,productLines:[],steps:[{code,name,kind,dependsOn,requiresHumanConfirm,inputs,documentKind}]}；只INSERT新版本，系统版本不变。
- POST /workflow-runs：{templateId,productId,creationKey,title?}；creationKey租户范围稳定键，标题默认商品内容策划。显式模板ID即锁具体版本，不自动替换为最新版本。
- GET /workflow-runs/{id}：返回{id,shopId,productId,templateId,templateVersion,rootDocumentId,status,steps:[{id,code,status,definition}]}。
- GET /workflow-runs：当前店铺最多100条，按创建时间倒序，逐条验证实际商品账户范围。

创建成功返回RUNNING及初始READY/PENDING；表示编排初始化，不代表生成或执行已开始。同键同请求返回相同run；同键改变模板/商品/店铺/标题返回409。缺失模板/不可见run受控404，范围拒绝403，非法DAG/UUID/字段422；不回显他租户对象。模板系统行没有修改/删除路由。没有execute/accept/redo公共接口，调用返回404。

后续SOP产物派生必须提交固定kind/sourceVersion和稳定run-step业务键到FlowService。保存skill标签不产生执行授权；音视频能力仍未开放。F10当前真实UI未接此新API，不宣称现前端兼容完成。


## 数组读取完整性限制（2026-10-28修复）

- GET `/v1/content/workflow-templates`：可见目录最多200条；恰好200仍完整数组返回，包含最新版本。存在第201条时整体HTTP422，`error.code=INVALID_INPUT`，中文原因明确超过200/当前不支持分页/拒绝截断。
- GET `/v1/content/workflow-runs`：当前店铺最多100条；恰好100完整返回，存在第101条时整体HTTP422/INVALID_INPUT并明确100上限。
- 不返回截断数组、不生成虚构cursor；现有单run读取/创建回执不受目录超限影响。后续真正分页需另行确认契约。
