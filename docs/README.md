# 文档目录说明

本目录按"谁产出、谁维护"划分，先分端、再分文档类型。找文档时先想清楚是前端的事、后端的事，还是两端共读的事。

```
docs/
├── frontend/      前端产出物
│   ├── architecture/   前端架构文档与配套数据
│   ├── handoffs/       各模块的交接记录
│   ├── acceptance/     验收记录与交付文件清单
│   └── evidence/       截图、日志与验证结果，按模块分子目录
├── backend/       后端产出物
│   └── architecture/   后端技术架构与实施设计
├── shared/        两端共读
│   ├── contracts/      接口契约、OpenAPI、对接包
│   ├── reference/      第三方平台开放 API 原始资料
│   ├── planning/       产品与业务规划、边界说明
│   └── baselines/      被代码直接读取的基线数据
├── archive/       已结束版本的历史交付物，只读不更新
└── git-workflow.md   Git 协作规范
```

## 收录规则

- 纯前端内容进 `frontend/`，纯后端内容进 `backend/`，两边都要读的进 `shared/`。
- 一份文档只有一个归属，不复制多份。
- 已经结束的版本（如 v12 整套交付）整体进 `archive/`，不再回改。
- `shared/baselines/` 下的文件会被代码读取，改动路径必须同步修改引用它的脚本和测试。

## 旧路径对照

2026-10-23 做了一次目录重整，只移动位置、未改名、未改内容。找不到老文件时按下表换算。

| 旧路径 | 新路径 |
|---|---|
| `docs/frontend-architecture-2026-10-22.md` | `docs/frontend/architecture/frontend-architecture-2026-10-22.md` |
| `docs/frontend-architecture-2026-10-22.html` | `docs/frontend/architecture/frontend-architecture-2026-10-22.html` |
| `docs/frontend-architecture-sources-2026-10-22.json` | `docs/frontend/architecture/frontend-architecture-sources-2026-10-22.json` |
| `docs/frontend-architecture-validation-2026-10-22.json` | `docs/frontend/architecture/frontend-architecture-validation-2026-10-22.json` |
| `docs/前端技术架构与业务流.md` | `docs/frontend/architecture/前端技术架构与业务流.md` |
| `docs/*handoff*.md`、`docs/content-*.md`、`docs/optimization-workspace-*.md`、`docs/data-review-workbench-*.md`、`docs/platform-v9-audit-and-handoff-*.md` | `docs/frontend/handoffs/同名文件` |
| `docs/G1-A验收记录.md`、`docs/G1-A文件清单.txt`、`docs/G1-B1验收记录.md`、`docs/G1-B1文件清单.txt` | `docs/frontend/acceptance/同名文件` |
| `docs/evidence/` | `docs/frontend/evidence/` |
| `docs/backend/后端技术架构与实施设计V1.0.md` | `docs/backend/architecture/后端技术架构与实施设计V1.0.md` |
| `docs/API契约需求初稿.md`、`docs/command_job前后端契约V1.0.md`、`docs/platform-v9-api-contract.md`、`docs/易致·驭量 V8.0 投放管理前后端接口契约.md`、`docs/易致驭量前端API对接包_2026-10-22.zip` | `docs/shared/contracts/同名文件` |
| `docs/api-handoff-2026-10-22/` | `docs/shared/contracts/api-handoff-2026-10-22/` |
| `docs/platform-api-reference/` | `docs/shared/reference/platform-api-reference/` |
| `docs/platform-api-reference/易致驭量V9.0_多平台功能重规划.md` | `docs/shared/planning/易致驭量V9.0_多平台功能重规划.md` |
| `docs/代码复用与边界.md`、`docs/后台页面规划_2026-10-22.md` | `docs/shared/planning/同名文件` |
| `docs/homepage-baseline.json` | `docs/shared/baselines/homepage-baseline.json` |
| `docs/v12/` | `docs/archive/v12/` |
| `docs/v12-logo-integration.patch` | `docs/archive/v12-logo-integration.patch` |

## 代码引用

以下文件读取 `docs/` 内的路径，移动对应文件时必须同步修改：

| 引用方 | 读取的文档 |
|---|---|
| `scripts/import-platform-catalog.mjs` | `docs/shared/reference/platform-api-reference/` |
| `scripts/check-routes.mjs`、`tests/frontend.test.ts` | `docs/shared/baselines/homepage-baseline.json` |
| `scripts/verify-homepage.mjs` | `docs/shared/baselines/homepage-baseline.json` |
| `tests/platform-workspace.test.ts` | `docs/shared/reference/platform-api-reference/` |

历史交接文档里的路径已按新位置更新；`docs/frontend/acceptance/` 下的 `G1-*文件清单.txt` 与 `docs/archive/` 内的清单保留当时的原始记录，不随目录调整改写。
