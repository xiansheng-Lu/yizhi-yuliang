# 易致·驭量前端 · G1-A

自主投放，让每一份增长都有据可依。

独立 Next.js 16.2.6 / React 19.2.6 / TypeScript 前端。官网冻结迁入，PC 保留四大工作区、21 页骨架，手机保留四标签；统一 Service 提供 Mock 与未来 API 接口。没有数据库、Next.js 业务 Route Handler、平台适配器、广告执行或 AI 策略引擎。

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
| `npm run test:routes` | 对实际运行服务检查根路径、21 路由、404 与历史入口 |
| `npm run verify:homepage` | 校验冻结官网哈希；原型仍存在时同时对照源文件 |

## 入口与演示

- `/`：通过 Next beforeFiles rewrite 提供冻结官网，响应内容与原始 HTML 一致。
- `/login`：Mock 身份登录，演示账号 `demo@yuliang.local`，密码 `demo123`，支持一键填入。不要输入真实凭据。
- `/app/[pageId]`：21 个白名单业务页面。
- `/mobile`：AI 指挥、直播数据、执行流水、直播复盘。
- 历史 `/login.html`、`/mobile.html`、`/app.html#/reports` 均有兼容。`campaigns` 归一为 `plans`。非法 next 参数进入 `/app/overview`，不会外跳。

PC 和手机在同源、同浏览器配置中使用同一 Mock 存储、任务与审批结构。**localStorage 不提供真实跨设备同步。** 示例任务可在 PC 或手机模拟批准，但不会修改计划预算、出价、ROI，不会控制直播推流，也不会产生平台回执。

G1-A 采用结构化操作表单，要求明确计划/场次和目标字段；未实现自然语言解析、语音、策略决策或执行能力。没有把 21 页全部扩展成完整业务。

## Mock / API 切换

默认 `NEXT_PUBLIC_DATA_MODE=mock`。未来接入后，在未提交的 `.env.local` 中设置：

```env
NEXT_PUBLIC_DATA_MODE=api
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:<后端端口>
```

变更后重启开发服务；NEXT_PUBLIC 变量在构建时注入，生产模式须重新构建。API 地址和接口均需陆文龙确认。客户端失败会显示错误，不回退 Mock；正式会话、服务端权限、CSRF 和数据协议尚未完成联调，不能上线使用。

## 目录与交接

- `public/`：冻结官网及原始 CSS、JS，不能修改。
- `src/app/(frontend)/`：前端路由、加载和错误边界；没有业务 Route Handler。
- `src/features/`：页面骨架与手机 UI。
- `src/components/`：导航、状态、共享任务表单和审批组件。
- `src/lib/domain.ts`：两端统一实体、金额口径与标准任务状态。
- `src/services/`：Service 契约、Mock 身份/数据、待接 API Client。
- `docs/API契约需求初稿.md`：给陆文龙的候选接口、数据与权限需求。
- `docs/G1-A验收记录.md`：实际启动、浏览器、截图、检查和未完成事项。

仓库：https://github.com/xiansheng-Lu/yizhi-yuliang 。本阶段按用户指令不 push、不部署；G1-A 完成后停止，等待验收与 G1-B 批准。后续上传也不得强推、覆盖他人提交或提交密钥、依赖、运行时及构建产物。
