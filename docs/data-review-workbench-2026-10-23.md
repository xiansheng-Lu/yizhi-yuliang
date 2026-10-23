# 数据复盘工作台 · 前端交接与验收

## 入口及工作流

左侧「数据复盘」现在是一个直接入口，没有二级菜单。唯一工作台为 `/app/reports`。

1. 选择平台、关联店铺、日期和分析维度，点击「开始经营分析」。
2. 工作台进入任务进度，成功后进入分析结果视图。
3. 在顶部或底部点击「生成复盘报告」，选择日报、周报、月报。
4. 同一工作台进入模块化编辑器，修改标题、摘要、模块结论、人工建议与行动计划。
5. 保存草稿，预览，发布网页版本或导出独立 HTML。
6. 右上角「历史报告」在工作台内打开，支持记录类型、周期、店铺、统计日期筛选，以及继续编辑、条件查看、复制正式发布链接、创建副本。

内部地址分别采用 `?task=ID`、`?result=ID`、`?draft=ID`、`?preview=ID`、`?published=ID`；没有新增侧边导航。`/app/analytics` 通过307跳转到 `/app/reports`，完整保留查询参数（包括重复参数）；旧的分析结果及草稿ID不变。登录回跳保留工作台记录参数；兼容旧 `analytics` 功能授权。服务端仍需逐接口鉴权。

## 重要数据边界

- 分析结果、编辑草稿、发布快照分别保存。编辑文字、隐藏图表、重新排序和复制草稿不修改原始分析。
- 日报必须为1天，周报为连续7天，月报为完整自然月（含闰年处理）。周期不一致时禁止生成，可按建议周期重新分析，原报告保留。
- 指标包含统计口径、单位、有效性、环比/同比值。人民币使用整数分；比例和ROI使用独立数值。
- 周趋势重新汇总分子、分母计算ROI、客单价和转化率，不平均每日比率。缺失数据不补零，不用更新时间生成历史曲线。
- 店铺支付GMV、直播GMV、视频GMV、广告归因GMV分别展示，不直接相加。关联图仅用于同周期趋势对照；没有订单级关联证据时不计算去重贡献或声明因果。
- 当前仅提供「基础规则分析」，可修改下降比例、消耗上升、最低消耗、ROI和最低播放量阈值。比较基准明确为上一完整等长周期；每日异常为前一日。没有有效对比、指标或样本时不生成对应诊断。
- 流量来源、自然/付费拆分、跨业务订单去重、直播期间消耗和细分归因未接入时明确提示。

## 前端实现文件

| 文件 | 作用 |
|---|---|
| `src/lib/reporting-date.ts` | 复用现有日期校验，提供统一报表日期偏移与变化率 |
| `src/lib/business-analysis.ts` | AnalysisQuery/Task/Result/Metric/Section/Insight、指标口径、关联过滤、周聚合与规则诊断 |
| `src/lib/analysis-draft.ts` | ReportDraft、模板周期校验、模块顺序、独立HTML导出 |
| `src/services/analysis-service.ts` | 统一适配器、API实现、演示存储、幂等任务/发布、版本冲突检查 |
| `src/services/analysis-mock.ts` | 固定且可解释的经营测试样本 |
| `src/features/analysis/workspace.tsx` | 工作台、筛选、维度选择、任务轮询及结果/编辑状态 |
| `src/features/analysis/flow.tsx` | 工作流上下文、查询与记录恢复 |
| `src/features/analysis/report.tsx` | 动态KPI、趋势、专项、关联和诊断；生成报告设置 |
| `src/features/analysis/charts.tsx` | 实际SVG趋势、日/周切换、悬停/键盘时间点、排序搜索分页明细 |
| `src/features/analysis/editor.tsx` | 统一编辑器、未保存内容缓冲、保存、预览、发布、导出 |
| `src/features/analysis/history.tsx` | 历史筛选、打开记录、草稿副本、发布链接 |
| `src/features/analysis/shared.tsx` | 原生可访问对话框、错误状态和查询链接 |
| `src/styles/business-analysis.css` | 复用V12蓝白色令牌，PC与390px手机布局 |
| `src/features/workspace-entry.tsx` | 接入原页面；阅读视图隐藏后台外壳 |
| `src/components/v12-shell.tsx` | 工作台拥有自己的标题与筛选上下文 |
| `src/components/provider.tsx` | 工作台深层链接登录回跳 |
| `src/lib/routes.ts` / `src/lib/access.ts` | 单入口导航、旧授权和登录目标兼容 |
| `src/app/(frontend)/app/[pageId]/page.tsx` | 旧经营分析路由迁移 |
| `src/services/api-client.ts` | 复用Cookie鉴权、超时和响应封装的命名空间传输 |
| `tests/business-analysis.test.ts` | 新增业务逻辑测试 |
| `tests/v12-navigation.test.ts` / `scripts/check-routes.mjs` | 单入口、深链及旧路由回归 |

## Mock与真实API切换

沿用 `NEXT_PUBLIC_DATA_MODE=mock|api` 和 `NEXT_PUBLIC_API_BASE_URL`，没有新增独立项目、后端或业务Route Handler。API模式不会失败后回退Mock。

演示数据有醒目标识，覆盖 **2026-09-01—2026-10-21**。9月完整月用于月报验收，10月样本用于周报与日报告警。交易额、消耗和播放量由代码中固定数组及明确的分配规则生成；9月演示消耗为成交额10%，播放量为7500起每日增加100；商品/场次/计划用固定权重分配，第一广告计划保留低ROI样本。店铺山海好物使用固定0.75系数，平台使用固定系数。这些规则仅说明测试数据来源，不代表真实平台规律。

可选完整、部分、无数据、执行失败场景。查询超出覆盖日期时不扩展样本；缺少完整周期时不提供全周期合计。演示中的微信豆视频指标为不支持样本，实际支持性必须由真实API返回。

演示分析和草稿按企业/用户保存在localStorage；流程及未保存编辑保存在当前会话sessionStorage。保存时检查版本；同一草稿版本重复发布返回相同发布版本。历史快照独立复制。浏览器存储没有跨设备持久化保证。

**演示发布没有正式客户链接。** 本地阅读地址只用于当前浏览器验收，复制正式链接按钮禁用；独立HTML可以直接交付。后端接入后仅复制发布接口返回的HTTP(S)正式地址。HTML内嵌品牌文字、图表和明细，没有后台导航、编辑控件或脚本；用户文字均转义，预览使用sandbox iframe。

## 待后端提供的API契约

复用现有 `ApiClient`：Cookie会话、`credentials: include`、`X-Yuliang-Client: web`、15秒超时，成功封装为 `{ schemaVersion: "command-job-v1.0", requestId, data }`。内部结果对象版本为 `business-analysis-v1`，草稿为 `business-report-v1`。类型定义以对应TS文件为准。

| 接口 | 请求及响应 |
|---|---|
| `POST /v1/business-analysis/tasks` | `{ query: AnalysisQuery, idempotencyKey }` → AnalysisTask；同幂等键返回同一任务，条件不一致返回409 |
| `GET /v1/business-analysis/tasks/:id` | AnalysisTask，状态queued/running/succeeded/failed、进度0—100，成功含resultId，失败含原因 |
| `GET /v1/business-analysis/results/:id` | 不可变AnalysisResult，必须与原查询、来源及任务匹配 |
| `GET /v1/business-analysis/results` | 当前授权企业/店铺的AnalysisResult数组，用于历史记录 |
| `GET /v1/business-analysis/drafts` | 当前用户可读ReportDraft数组 |
| `GET /v1/business-analysis/drafts/:id` | 完整ReportDraft，含版本号和原分析引用 |
| `PUT /v1/business-analysis/drafts/:id` | ReportDraft；新草稿version=0，编辑提交当前版本，响应版本必须递增，冲突409 |
| `POST /v1/business-analysis/drafts/:id/publications` | `{ version, idempotencyKey: "草稿ID:版本号" }` → PublishedReport；固定已保存版本、幂等发布 |
| `GET /v1/business-analysis/publications` | PublishedReport数组；需返回title、period、query、analysisId、draftId、publishedAt与正式url，供筛选与复制链接 |
| `GET /v1/business-analysis/publications/:id` | 固定发布版本信息、正式url，可附只读snapshot；不能读取最新草稿冒充历史版本 |
| AI诊断预留 | AnalysisResult返回 `insightSource: "ai"` 与有数据依据的AnalysisInsight；当前前端未调用模型。未接入时返回rules并由明确规则生成基础结论 |

AnalysisResult必须返回：原始query、店铺/平台名、数据更新时间、complete/partial/empty、coverage、attribution、指标单位和定义、分子分母字段、日序列、完整有效的对比周期、专项与跨业务模块、明细和诊断。未接入/不支持/无数据必须返回明确status和null，不能用0替代缺失。前端不会将单日旧snapshot的广告归因金额当作真实店铺GMV或历史报表。

现有 `/v1/frontend/snapshot` 继续供给授权店铺/广告账户关联和来源刷新。经营历史、订单去重、退款、商品、场次、内容、广告归因与流量结构需要由后端通过平台接口汇总。前端不调用平台写接口。

正式客户网页的永久地址、版本持久化、服务端访问控制、撤销/有效期与独立客户读取服务由后端实现；本次只接收正式发布回执和预留契约。服务端必须校验企业、用户、店铺、记录归属、模板周期及版本，并用原分析快照核对指标，不信任浏览器传来的经营数值。

## 验收记录（2026-10-23）

- 单入口导航、旧路由307及参数保留；原有官网字节/资源哈希和其他业务路由回归。
- 四个维度单选、多选、全选四维；店铺+投流、直播+投流、店铺+视频对应动态模块与交叉分析。
- 日/周趋势、指标切换、有效环比、时间点明细、专项表格搜索排序。
- 日报1天、周报7天、月报9月完整30天均在 `/app/reports` 内完成生成；7天数据生成月报被拦截，支持重新选择周期。
- 模块新增、编辑、上移、显示/隐藏指标和图表、报告名称及摘要编辑、保存与继续编辑。
- 历史周期筛选、查看原查询；历史打开/关闭保留编辑内容，切换分析/编辑步骤后恢复未保存摘要。
- 客户阅读预览、HTML实际下载、本地发布快照；阅读界面无后台侧栏，演示正式链接复制禁用。
- 部分数据、无数据、失败原因与重新分析；发布幂等、草稿版本冲突、原始数据及发布快照隔离。
- 1440px PC与390px手机验证，没有页面整体水平溢出；浏览器未捕获页面运行错误。

运行命令：`npm test`、`npm run typecheck`、`npm run lint`、`npm run test:routes`、`npm run build`。构建使用忽略目录 `.runtime/analysis-build`，未停止现有开发服务，未部署。具体结果及截图见 `docs/evidence/data-review/`。

最终主分支隔离验证：177 项测试全部通过；共享工作区含其他待交付模块时共186项测试也已通过。TypeScript与ESLint通过，生产构建通过。
