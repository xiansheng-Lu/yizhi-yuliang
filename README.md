# 易致·驭量前端 · V12 R1

自主投放，让每一份增长都有据可依。

独立 Next.js 16.2.6 / React 19.2.6 / TypeScript 前端。V12 R1 增加蓝白应用外壳、七组导航及默认 `/app/command` 指挥台，保留 21 个旧路由，合计 22 个业务入口。官网仅按用户授权替换 Logo，其他文案、布局、三层卡包以及原 CSS/JS 冻结。G1-A/G1-B1 任务、审批与 Mock/API 边界保留；R2—R5 尚未实施。没有数据库、Next.js 业务 Route Handler、平台适配器、广告执行或 AI 策略引擎。详见 `docs/v12/R1-验收记录.md`。

## Windows 本地运行

```powershell
cd D:\易致-驭量
powershell -File scripts/setup-runtime.ps1
npm run setup
npm run dev
```

固定开发地址：`http://127.0.0.1:3000`。若端口被占用，先确认占用服务，不自动换端口或终止其他项目。

项目级 Node 固定 `24.19.0`，位于被 Git 忽略的 `.runtime/node.exe`。启动器显式使用该文件，启动、测试和构建不依赖系统默认 Node；不修改全局 Node 或系统 PATH。首次准备脚本从 Node 官方发行目录下载并检查 SHA-256；本次使用本机 Codex 随附的同版本运行时副本。`npm run setup` 也使用项目 Node 执行 npm ci。没有 npm 时可使用准备脚本解压后的 Node 发行包所附 npm。

## 命令

| 命令 | 作用 |
|---|---|
| `npm run dev` | Next.js 开发服务，127.0.0.1:3000 |
| `npm run typecheck` | TypeScript 检查 |
| `npm run lint` | ESLint，零 warning |
| `npm test` | 路由、哈希、任务类型、共享状态、权限与 API 错误测试 |
| `npm run build` | Next.js 生产构建，非部署 |
| `npm start` | 启动已构建本地服务；先停止同端口开发服务 |
| `npm run test:routes` | 检查根路径、21 旧路由＋新指挥台、404 与历史入口 |
| `npm run verify:homepage` | 校验当前获批官网及品牌资源哈希；保留并校验原型源文件哈希 |

## 入口与演示

- `/`：通过 Next beforeFiles rewrite 提供官网；仅 Logo 例外，其他内容与原始 HTML 一致。
- `/login`：Mock 身份登录，演示账号 `demo@yuliang.local`，密码 `demo123`，支持一键填入。不要输入真实凭据。
- `/app/command`：登录默认进入 AI运营指挥台；自然语言入口复用既有 Mock Service。
- `/app/[pageId]`：21 个旧业务页面继续保留；overview 为经营总览，仅三项顶层 KPI。
- `/mobile`：AI 指挥、直播数据、执行流水、直播复盘。
- 历史 `/login.html`、`/mobile.html`、`/app.html#/reports` 均有兼容。`campaigns` 归一为 `plans`。默认及非法 next 参数进入 `/app/command`，不会外跳；显式 `/app/overview` 深链仍进入经营总览。

PC 和手机在同源、同浏览器配置中使用同一 Mock 存储、`command_job`、统计范围和任务详情组件。**localStorage 不提供真实跨设备同步。** 手机创建后确认对象、差异与风险，进入待审批；PC 批准后依次点击“开始模拟执行”“接收模拟执行回执”“运行模拟核验”。只有模拟回读匹配后才更新演示计划参数，始终不产生真实平台回执，也不控制推流。

手机支持明确标注的 Mock 规则解析，可输入“看看现在直播间情况”“把千川A计划日预算调到5000”“把千川A计划ROI目标调到6”“暂停千川A计划广告”。“把预算调到5000”存在多个计划时必须先选择。真实 AI 解析由后端提供。预算与出价按 CNY 整数分，ROI 使用独立比值字段。暂停计划不会清空历史消耗。

任务创建前可选 Mock 请求失败、超时、核验失败分支；PC 可驳回或模拟过期。经营目标支持企业预算上限、ROI目标、变化幅度、冷却期及工作模式；全部是前端演示配置，正式风控和白名单授权由后端决定。Mock 默认有效期 15 分钟，在读数据时检查；默认成功动作冷却 5 分钟。验收中为连续演示临时改成 0 分钟，结束后已恢复 5 分钟，不代表正式策略。

新存储 `yuliang:command-jobs:v1` 会只读迁移 G1-A 数据，保留旧存储备份并要求重新确认旧申请；不继承旧审批。正式模式不会静默回退 Mock，也不能从前端推进执行或手工过期。

## Mock / API 切换

默认 `NEXT_PUBLIC_DATA_MODE=mock`。未来接入后，在未提交的 `.env.local` 中设置：

```env
NEXT_PUBLIC_DATA_MODE=api
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:<后端端口>
```

变更后重启开发服务；NEXT_PUBLIC 变量在构建时注入，生产模式须重新构建。API 地址和接口均需陆文龙确认。客户端失败会显示错误，不回退 Mock；正式会话、服务端权限、CSRF 和数据协议尚未完成联调，不能上线使用。

## 目录与交接

- [后端技术架构与实施设计 V1.1（Python）](docs/backend/后端技术架构与实施设计V1.0.md)：内部服务商与运营优先，采用 FastAPI、SQLAlchemy/Alembic、Celery/RabbitMQ、多模型适配；当前为设计文档，尚未创建后端工程。
- `public/`：官网原 CSS/JS 冻结；Logo 替换已授权，原始及获批版本哈希均保留。
- `src/app/(frontend)/`：前端路由、加载和错误边界；没有业务 Route Handler。
- `src/features/`：页面骨架与手机 UI。
- `src/components/`：导航、状态、共享任务表单和审批组件。
- `src/lib/domain.ts`：两端统一业务实体、金额口径。
- `src/lib/command-job.ts`：统一任务、动作、状态、值类型与差额格式化；`reporting.ts`：共享统计范围。
- `src/services/`：Service 契约、Mock 身份/数据、待接 API Client。
- `docs/command_job前后端契约V1.0.md`：本阶段给陆文龙确认的任务、接口、风控和核验草案。
- `docs/G1-B1验收记录.md`：实际运行、10 类核心场景、截图、日志、修改范围和未完成事项。
- `docs/API契约需求初稿.md`、`docs/G1-A验收记录.md`：保留的 G1-A 历史记录。

仓库：https://github.com/xiansheng-Lu/yizhi-yuliang 。本阶段按用户指令仅本地提交，不 push、不部署；R1 完成后停止，等待验收。后续上传也不得强推、覆盖他人提交或提交密钥、依赖、运行时及构建产物。
