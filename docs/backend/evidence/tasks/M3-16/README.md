# M3-16 共用内容校验器（结构与保存入口技术切片）

2026-10-28，负责人BE，协作F09/F10。整卡状态IN_PROGRESS；结构/共用库/实际保存入口切片为TECH_VERIFIED_WAITING_REVIEW。真实GENERATION及SOP执行/采纳入口尚未实现；本批M3-17a已实际接入SOP根文档初始化，整卡仍有开发工作，不是仅待人工复核，不宣称完整三业务入口DoD完成。基线main d0caf14，本地分支codex/backend-content-plan-readmodels。无本卡新迁移，06/12原有未提交内容未动。

## 规则与入口

validation.py作为唯一结构/图/可信媒体边界规则，Document模型after委托同一规则，ContentService.save及private_save均重新解析并校验，不信model_copy/model_construct；review/flow内部路径沿同一save。非法结构在数据库写入之前拒绝，不部分保存文档、版本或引用。

覆盖分镜0.1至600秒、画板整数100至8192/hex色、图层宽高/字号正数、规格枚举/1至600秒、消息日期/素材UUID、有限数值、最多32个唯一素材及既有1MiB/1000项/深度15限制。使用显式栈DFS覆盖自环、任意多节点环与断开子图环，不依赖Python递归深度；1000节点合法链及回环拒绝已有单元证据。

依本轮明确约定兼容真实前端：x/y只要求有限，可为0或负，不擅自钳位；宽高/字号仍为正。16册表“坐标为正”与前端现状差异保留待BE/FE具名规格决议。依16册134行，已有caption片段的text为空或纯空白明确拒绝，定位clips.<index>.text/clipId；空clips集合不受影响。skill仅保存声明标签，不从任意字符串取得模板执行能力；实际模板目录仍归后卡。

validate_content的SAVE/GENERATION/SOP purpose只标签化同一库判断，无供应商调用、业务状态推进或自授权限。保存基线当时仅接入保存及其内部入口；本批M3-17a已真实接入SOP根planning文档初始化（purpose=SOP及同事务_save）。生成及SOP执行/采纳入口仍未完成，不新增空业务路由冒充已集成。实际素材scope/kind/具体版本HEAD/HMAC/历史引用互斥仍复用M3-15b，不改权限为客户端自报。

## 可信时长边界

MediaFact只用于服务端已可信事实协议；纯U层用合成Decimal时长验证sourceStart+duration <= duration+0.01。未验证、缺失、非有限或负时长拒绝，不回退0、不采纳客户端自报时长。真实运行仍未开放音视频上传/引用，实际保存无可信时长即拒绝；合成三purpose一致性不是音视频探测或生成/SOP验收证据。

## 错误与隐私

validation_errors.py统一中文原因、完整document.*字段路径和受控document/board/layer/node/edge/clip等ID。platform/errors.py只对/v1/content/documents校验采用该适配；其他API既有错误信封保持原处理。受控自定义规则ctx仅按已知code/path提取，不返回ctx/input/原异常或不受支持字段名；标准深层错误不再被截到前5段。日志仍只记requestId/code/status。

model_construct复验序列化显式关闭可能携原值的Pydantic serializer warnings，再以正常严格schema重建。HTTP实际PRIVATE正文/密码样例不出现在响应或日志。没有把field/input原值或凭据拷入details。

## 实际验证

串行独占本地DB窗口，真实create_app/Cookie/CSRF/PG与其中图片用例的MinIO：

- `python scripts/test_infrastructure.py --group content-validation --details`：原4项6.77秒；补caption负例后最终5 passed，6.70秒。
- `python scripts/test_infrastructure.py --group content-documents --details`：5 passed，6.62秒。
- `python scripts/test_infrastructure.py --group content-assets --details`：9 passed，10.10秒。
- `pytest tests/unit/test_content_validation.py -q`：最终21 passed，0.53秒。
- 新增/调整范围Ruff通过；共用校验/错误/模型/服务与本卡测试7文件mypy通过。

本卡实际HTTP验证环和深层图层字段含对象ID且无原值回显；private_save对copy/construct伪造结构不修改版本/历史；实际已确认图片引用保留scope/HEAD/HMAC且0/负坐标原值保存；未知媒体时长拒绝、无文档落库；caption空文本422并不落库。所有平台/付费调用0。

## DoD边界与推进

DoD1的本地结构规则与合成可信时长比较已有证据，真实音视频时长未验；DoD2共用库三个purpose结果一致，已实际接入保存及本批M3-17a SOP根文档初始化；生成和后续SOP执行/采纳尚待后卡；DoD3实际商品/素材scope继续由既有真实PG guard验证，账户关联模型/入口尚未由本卡交付，不凭Document额外字段拒绝声称完整账户关联验收。

该技术切片可作为M3-14本地派生实现输入，不能把缺业务入口或BE/FE签字改写为DONE。完整F09/F10浏览器、实际生成与后续SOP执行/采纳接线、音视频探测及规格具名复核继续保留。回滚可停用新入口，保留已存内容，不自动修订/抹除旧文档。

## 本批新增实际业务入口证据

M3-17a真实create_app/Cookie/CSRF及PG开线已验证根planning文档实际调用validate_content(purpose=SOP)，文档、run、steps与引用同事务。修后8项专项通过，详见../M3-17a/verification.json；M3-13b导入同样复用_Save实际守卫，13项专项通过并保留F09浏览器缺口。整卡仍IN_PROGRESS，不将初始化当成步骤执行或供应商生成。

## SOP执行切片新增证据

本批M3-17b真实MANUAL/REVIEW execute→持久冻结候选→人工accept及闭包redo已通过12项PG/HTTP，执行/采纳实际调用purpose=SOP，素材及正文守卫保留。Flow同事务prepared子稿严格创建、原键/版本与外层回滚8项回归通过；954项最终完整集成通过。GENERATION与AI_ASSISTED生产者仍未实现，M3-16整卡IN_PROGRESS；先前“尚待SOP执行”的文字是此前保存/根初始化阶段边界，以本段新增事实为准，不把人工切片当模型或供应商调用。
