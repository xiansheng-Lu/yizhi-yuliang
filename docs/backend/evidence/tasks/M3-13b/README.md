# M3-13b 本地草稿迁移与断点导入

状态：后端技术链路已验证；整卡仍 IN_PROGRESS，真实浏览器IndexedDB读取与F09交互待联调。

## 来源与边界

- `frontend/src/services/content-drafts.ts`：IndexedDB `yuliang-content-v1` / `drafts`、`files`；owner key为content-v1/mode/tenant/user/shop，容器revision独立并发锁。
- `frontend/src/lib/content-factory.ts`：ContentState schema=1/revision/documents/assets；Document.version不是ContentState.revision。
- `20-后端逐任务开发与验收卡.md` M3-13b与16册第6节：预览不写云端、稳定ID映射/幂等/断点、所有者与商品服务器校验。
- canonical `products.id`（当前domain商品）与V9 `commerce_products.id`（云内容商品）不是同一ID。
  本切片要求显式productMap，服务器同时核对未删除、同店、同native_id及目标账户当前content.write范围；不按名称猜。

## 实现

新增 `content/imports_models.py`、`imports_service.py`、`imports_routes.py`；0065迁移接0064_workflow_templates_runs。

- POST `/v1/content/imports/preview`：完整元数据预览/冲突/引用/文件映射清单，只读，无imports/document/reference写；取消即不apply。
- POST `/v1/content/imports/apply/{local_id}`：每次一条，使用真实actor+tenant+shop+importKey事务咨询锁，正文/素材固定版本引用/ID映射回执/断点同事务。
- POST `/v1/content/imports/progress`：原清单digest重附恢复已完成/待处理ID，不重建文档或素材。
- 三入口复用既有Cookie、CSRF与X-Shop-Id；source owner只是来源证据，不授予真实身份。不接受query覆盖身份。
- ImportBundle为importKey、owner、state、productMap、assetMap、decisions。只接受API同所有者来源，MOCK所有者/跨租户拒绝。
- UUID5命名空间包含真实tenant/actor/shop/key/localId；固定清单digest绑定本地revision、正文、映射和COPY决策；同key内容变化返回冲突。
- 原本地ID已有云稿时要求显式COPY；稳定目标ID若已有无本导入回执的云稿则拒绝覆盖。父稿须先导入，保留稳定父子映射；环/缺父预检拒绝。
- 云端新文档version固定1，由现有ContentService后续版本控制；本地version与容器revision只作来源，绝不当作云锁。
- JSON仅元数据，不读客户端文件路径、不上传文件、不信客户端mime/size/duration/blob版本。assetMap只能指向本店已CONFIRMED且未归档真实素材。
  素材预检使用AssetService.prepare_document固定版本HEAD/实例HMAC；事务内ContentService._save再次锁内重验服务器AssetProof。
- 只重写确定的nodes/boards/clips/messages素材引用；本地账户accountLinks不迁入权限，也不是平台上传回执。
- content_imports/entries按真实tenant+actor强制RLS，仅SELECT/INSERT，禁止普通角色改写历史回执。回执保留local→cloud文档/商品/素材映射及实际固定assetVersion。

## 实际验证

- unit `pytest -q tests/unit/test_content_imports.py`：7 PASS，0.36秒。
- integration `python scripts/test_infrastructure.py --group content-imports --details`：11 PASS，11.55秒。
  真实PostgreSQL/MinIO：预览零写、真实显式映射、固定文件证明、幂等与并发恢复、失败后正文/引用/进度全回滚、owner/商品/素材/纯元数据拒绝、显式COPY、清单变更、父子恢复、未知目标不覆盖。
  真实FastAPI Cookie/CSRF HTTP：preview→apply→progress，缺CSRF403、query覆盖422。
- 首次专项7失败4通过：不可变表的SELECT FOR UPDATE要求UPDATE权限，已改为原actor+key咨询事务锁下普通SELECT，不扩UPDATE权限、不改冻结DDL；重跑11通过。
- Ruff PASS；mypy本切片5文件PASS。
- 本轮70ab960 imports DB窗口已明确向root与reports释放；此后未新增DB测试。

## 未完成验收

真实浏览器读取IndexedDB、文件补传/导入预览交互、F09适配器及跨设备云稿UI联调尚未执行，不能因HTTP/合成元数据PASS宣称UI已接。
音视频素材仍遵循现有固定文件/媒体验证守卫，未确认类型/时长不绕过；本导入不补造媒体证明。
真实平台调用0、业务平台写0、原项目token配置变更0。未修改ContentService/BlobStore；main/runner由主Agent装配；Git由主Agent统一处理。

## 交叉审查修复 review-fixes

主Agent授权reports修复两处边界，限imports_service.py与test_content_imports.py，无DDL变更：

1. 导入存在性检查原在ContentService文档锁之前，普通编辑器可在窗口内创建同目标ID，随后导入误走version=1的更新分支覆盖正文。现在先取得与普通保存相同的文档advisory事务锁，再检查目标不存在，再原子保存。新增真实双线程barrier专项，普通编辑器先提交时导入409、原正文保留、无导入/引用/断点残留。
2. 已完成条目原在查回执前重新读取映射和执行素材HEAD，存储暂时离线会失去稳定重放。现在在当前owner/店铺权限及manifest一致验证后优先原回执，返回前重验实际云文档当前shop/product写权限；不重跑历史素材HEAD，不覆写正文。未完成条目仍保留两次映射、权限、固定版本素材证明及最终事务验证。新增HEAD离线重放与权限撤销拒绝专项。

实际验证：`UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv uv run --frozen python scripts/test_infrastructure.py --group content-imports --details`，13 passed/12.52s；原单元7 passed/0.52s；两改动文件Ruff、imports_service.py的mypy PASS。专项后明确释放DB并冻结。此为本地真实PG/MinIO与合成上游/身份测试，浏览器IndexedDB与F09人工验收缺口保持。
