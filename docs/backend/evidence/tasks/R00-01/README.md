# R00-01 验证记录与复盘

记录日期：2026-10-26（Asia/Shanghai）；基线提交4405ebc；开发分支feat/backend-foundation。

任务：冻结前端请求与接口契约。BE交付/Agent自检已经完成；**总体WAITING_REVIEW，尚无BE/FE人工签字，不记DONE。** 新增CSRF/异步素材接口需F01/F11复核，现有协议无需新增必填字段。

## 交付与范围

- [可阅读的逐接口核对和复盘](../../../contracts/R00-01-接口契约核对.md)
- [请求矩阵与源码散列](../../../contracts/http-contracts.json)
- [实际调用与字段声明](../../../contracts/frontend-surface.json)
- [黄金样例](../../../contracts/golden.json)
- [差异决定](../../../contracts/冻结决定.md)
- 05/14册已回写新契约差异。未改前端源码；未新增不兼容字段；未运行实际平台写入、付费、数据库或浏览器联调。

## 实际验证

工作目录为仓库根目录。以下使用现有Node/TypeScript及Python标准库，不依赖尚未验收的后端或Docker。

| 命令 | 预期 | 实际 |
|---|---|---|
| `python docs/backend/tools/check_plan.py` | 卡片/依赖/顺序/父任务/门禁覆盖完整 | PASS：149卡、7门禁、156节点、101父任务、42项A、14项F |
| `python docs/backend/tools/check_contracts.py` | 冻结版本与路由无重复、关键黄金样例形状正确 | PASS：15份源码、52项路由 |
| `node docs/backend/tools/freeze_frontend_surface.mjs` | 人工矩阵和真实AST逐项一致，类型与源码无漂移 | PASS：54处调用、52路由、133项类型、15份源码；默认只校验不写文件 |
| `node node_modules/tsx/dist/cli.mjs docs/backend/tools/check_frontend_contracts.ts` | 正例可由真实客户端接受，反例被拒绝 | PASS：45项；不发真实网络请求 |
| TypeScript编译器只检查新增契约校验脚本的诊断 | 诊断0 | PASS；发现样例误用materials枚举，已改成实际LookupKind.creatives后复核 |
| `git diff --check` | 无空白错误 | PASS |

校验环境：Node24.18.0、仓库TypeScript5.9.3及tsx4.20.6；Python3.14.6仅运行标准库文档检查。前端完整npm test要求Node>=24.19.0，本卡未运行/未声称完整前端回归通过。新增脚本编译诊断使用仓库typescript配置、noEmit、不产出构建文件。

黄金样例覆盖：Identity合法/非法页面、空会话、十二数组各自反例、API来源/时区、外层版本、204退出反例、V1审批原字段、外部AUTOPILOT和不安全金额、成功必须MATCHED、UNKNOWN仍进行中、V8预检版本/检查项、V9资源/202差异/账户上下文/成功业务ID、分析状态小写与数组列表，以及当前HTTP409只映射CONFLICT。

## 三条DoD逐项结论

| DoD | 技术结论与证据 | 最终验收状态 |
|---|---|---|
| 1. 覆盖V1/V8/V9/分析现有请求，不新增不兼容必填字段 | 52条matrix与AST集合完全一致；133类型保留原字段和可选性；inline请求体另留类型推导 | 后端技术PASS，待F00复核 |
| 2. 页面ID、十二数组、审批大写和退出JSON可由样例验证 | 45项实际ApiClient/领域客户端检查；原V1审批不强制comment | 技术PASS；未宣称后端接口实现或A01–A08整体验收通过 |
| 3. 新内容/公开报告、异步素材差异和查询契约列清 | 逐接口核对C03–C06；现有/materials保持SelectedResource；新增独立任务按原键或ID查，公开阅读统一/p | 交付PASS；F01/F11联合冻结PENDING |

## 待复核与后续条件

| 项 | 负责人 | 需确认的具体内容 | 当前状态 |
|---|---|---|---|
| F00 | FE+BE | 52条现有请求/字段/版本矩阵 | 未取得人工确认 |
| F01/F02 | FE+BE | GET /v1/auth/csrf引导及轮换、HTTP业务错误读取/UNKNOWN展示 | 提案已完整，可审阅，尚未接线 |
| F11 | FE+BE | 独立material-upload-tasks、multipart请求键、原键/ID查询、最终资源 | 提案已完整，可审阅，尚未接线 |
| A01–A08 | 两位负责人 | 未来后端API和端到端联调 | NOT_RUN；本卡仅规格子场景 |

## 复盘与纠偏

第一项应先收口契约，而不是同时宣称底座完成；此前准备的后端代码保留为未验收工作，本轮按用户要求只完成本卡交付。用AST比对取代仅靠手抄接口数，暴露inline字段与条件分支；用真实客户端校验再加编译诊断，发现“运行时可接受但类型不合法”的样例。新草稿中公开报告路径曾误写/reports，已统一回/p；散列按LF归一，避免换行导致假漂移。后续一张主卡完成证据和复核后再推进。

## Git与阻塞

排期文档先前已提交并推送main：4405ebc。本卡及此前准备的后端代码仍在本地开发分支，尚未提交/合并/推送。自动审批服务因额度耗尽无法完成后续提升权限操作的审核；此前被阻止的是提升权限的Python静态检查，并非安全判定。本轮不绕过审批，不把未同步成果称为已上传；Git发布待审批服务恢复后按用户既有授权完成。
