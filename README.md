# 易致·驭量

面向直播运营、广告投放、内容生产与经营复盘的协作平台。仓库采用前后端分离结构：前端统一位于 `frontend/`，后端统一位于 `backend/`，项目文档保留在 `docs/`。

## 项目结构

```text
yizhi-yuliang/
├── frontend/          # Next.js 前端、静态资源、测试、脚本与 Node 配置
├── backend/           # Python 后端、迁移、测试、部署配置与工程工具
├── docs/              # 原有文档、契约、验收证据与归档（本次保持不变）
├── .github/           # CI 工作流
├── AGENTS.md          # 项目开发约定
└── README.md          # 项目总入口
```

## 前端开发

技术栈：Next.js 16、React 19、TypeScript；项目 Node 固定为 24.19.0，依赖由 `frontend/package-lock.json` 锁定。

在仓库根目录打开 PowerShell：

```powershell
cd frontend
powershell -File scripts/setup-runtime.ps1
npm run setup
npm run dev
```

开发地址为 `http://127.0.0.1:3000`。前端环境示例在 `frontend/.env.example`，本地配置放 `frontend/.env.local`；默认 API 模式，显式 Mock 仅供演示。端口占用时先确认现有服务。

```powershell
# 以下命令在 frontend/ 下执行
npm run lint
npm run typecheck
npm test
npm run verify:homepage
npm run build
# 服务启动后执行 HTTP 路由检查
npm run test:routes
```

运行时、依赖与构建缓存分别位于 `frontend/.runtime/`、`frontend/node_modules/`、`frontend/.next/`。旧工作目录内的这些忽略文件不会自动迁移；首次切换后按上述命令重新准备。详细页面、演示和环境说明见 [前端 README](frontend/README.md)。

## 后端开发

技术栈：Python 3.13.14、FastAPI、SQLAlchemy/Alembic、Celery；使用 uv 与 `backend/uv.lock` 管理依赖。

```powershell
cd backend
uv python install 3.13.14
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit tests/contract -q
```

环境变量、基础设施、迁移及服务启动步骤见 [后端 README](backend/README.md)。前端服务与后端服务分别启动；真实数据库、授权、平台接口和浏览器联调均需独立验收，Mock 与离线测试通过不代表真实业务成功。

## 契约与文档

文档总索引见 [docs/README.md](docs/README.md)，协作流程见 [docs/git-workflow.md](docs/git-workflow.md)，任务队列见 [细化排期与验收执行方案](docs/backend/architecture/19-细化排期与验收执行方案.md)。

`docs/` 内保留了原始路径和历史工具。目录迁移后的冻结契约检查使用以下工程入口，仍读取原文档中的契约与哈希，不改写文档：

```powershell
# 仓库根目录；先准备前后端依赖
cd backend
uv run python tools/check_contracts.py
cd ../frontend
.runtime/node.exe node_modules/tsx/dist/cli.mjs ../backend/tools/check_frontend_contracts.ts
.runtime/node.exe ../backend/tools/freeze_frontend_surface.mjs
```

历史文档中的根目录 `src/`、`public/`、`scripts/`、`tests/` 与前端 npm 命令，现均对应 `frontend/` 下的位置；后端和文档目录位置不变。Linux/macOS 可用符合版本要求的 `node` 替代 `.runtime/node.exe`。

## 协作与提交

仓库：[xiansheng-Lu/yizhi-yuliang](https://github.com/xiansheng-Lu/yizhi-yuliang)。前端一人、后端一人，结合 coding agent 按任务依赖与验收门禁推进。前端工程归 `frontend/`，后端工程归 `backend/`，跨端配置与契约变更由双方确认。

新需求从最新 `main` 建本地分支，验证后合并到 `main` 并推送；中间分支不推送。提交描述使用中文，只提交当前任务文件，不提交凭据、本地环境、依赖或构建产物。本文的目录归属与中文提交说明补充现有协作文档中的历史约定。
