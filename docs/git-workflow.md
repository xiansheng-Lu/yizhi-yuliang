# Git 协作规范

适用场景：两人协作开发（后端 1 人、前端 1 人），仓库为 `https://github.com/xiansheng-Lu/yizhi-yuliang.git`，远程名 `origin`。

核心原则：`main` 随时可运行、可部署；改动范围越小、合并越频繁，冲突越少。

## 1. 分支模型

长期存在的分支只有 `main`，其余都是短命分支，存活时间不超过 3 天。

| 分支 | 用途 | 命名示例 |
|---|---|---|
| `main` | 唯一主干，集成与部署来源 | - |
| `feat/` | 新功能 | `feat/backend-report-queue`、`feat/frontend-live-room` |
| `fix/` | 缺陷修复 | `fix/frontend-budget-overflow` |
| `docs/` | 纯文档改动 | `docs/api-contract-v2` |
| `chore/` | 配置、依赖、脚本 | `chore/upgrade-eslint` |

命名规则：`<类型>/<端>-<简述>`，端用 `frontend` 或 `backend`，简述用英文小写短横线连接，不加日期后缀。

### 开发流程：本地分支开发，完成即合并

前后端所有新需求、功能改动和缺陷修复都按同一套流程走：

1. 从最新的 `main` 在本地新建分支
2. 在分支上开发和提交，分支只保留在本地，不推送到远程
3. 完成后把分支合并回 `main`，再推送 `main`

只有纯文档修正、依赖锁文件同步这类不改变代码行为的小改动，才允许直接提交到 `main`。

## 2. 目录归属

| 归属 | 目录或文件 | 修改人 |
|---|---|---|
| 前端 | `src/`、`public/`、`docs/frontend/` | 前端 |
| 后端 | `backend/`、`docs/backend/` | 后端 |
| 共享 | `package.json`、`package-lock.json`、`.env.example`、`.gitignore`、`README.md`、`AGENTS.md`、`docs/README.md`、`docs/git-workflow.md`、`docs/shared/`、`docs/archive/`、`scripts/`、`tests/` | 改动前先沟通，一次只一人动 |

文档目录的具体分层见 `docs/README.md`：纯前端内容放 `docs/frontend/`，纯后端内容放 `docs/backend/`，两端共读的契约、平台资料与规划放 `docs/shared/`，已结束版本的历史交付物放 `docs/archive/`。

共享文件的冲突几乎无法靠 git 自动合并，必须靠"预约"避免：动手前在群里说一句要改哪个文件、改什么，改完立刻提交推送，不加塞其他改动。

## 3. 提交信息规范

格式：

```
<type>(<scope>): <subject>

<可选正文，中文说明改动原因和影响范围>
```

- `type` 取值：`feat`、`fix`、`refactor`、`docs`、`style`、`chore`、`test`、`build`
- `scope` 取值：`frontend`、`backend`、`docs`、`deploy`
- `subject` 用英文，祈使句、小写开头，不超过 50 字符，句尾不加句号

示例：

```
feat(backend): add report generation queue
fix(frontend): correct budget slider overflow on mobile
docs(backend): document deployment topology
```

要求：

- 一个提交只做一件事，功能改动和格式化改动分开提交
- 禁止 `update`、`修改`、`fix bug`、`提交代码` 这类无信息量描述
- 正文只在"为什么改"不明显时才写，写清背景和影响，不复述代码

## 4. 日常工作流

前后端共用一套流程，只是改动目录不同。以新增一个前端需求为例：

### 4.1 建分支开发

```powershell
git checkout main
git pull --ff-only origin main
git checkout -b feat/frontend-live-room

# 开发过程中每天至少同步一次主干
git fetch origin
git rebase origin/main

# 提交（只加自己改的文件）
git add src/具体文件路径
git commit -m "feat(frontend): add live room entry"
```

分支只留在本地，不执行 `git push`。

### 4.2 合并回 main 并推送

```powershell
# 合并前再同步一次，避免 main 已前进
git checkout main
git pull --ff-only origin main
git merge --no-ff feat/frontend-live-room
git push origin main

# 确认合并结果后删除本地分支
git branch -d feat/frontend-live-room
```

合并冲突在 `main` 上解决完后，必须跑一遍检查再提交合并结果。

### 4.3 每日节奏

- 每天开工前先 `git pull --ff-only origin main`
- 开发中的分支每天至少 rebase 一次 `origin/main`
- 功能做完当天合并进 `main` 并推送，不积压，避免多个并行分支最后一次性合并

## 5. 冲突处理

- 谁改动谁解决冲突，不把冲突丢给对方
- 解决冲突逐行看，禁止"全部接受当前分支"或"全部接受对方分支"
- 冲突涉及对方负责的目录时，先沟通再动手，必要时一起看
- 分支只保留在本地，rebase 后不需要强推；`main` 上永远禁止 `git push --force`

## 6. 回滚与发布

- `main` 出问题用反向提交回滚，不改写历史：

```powershell
git revert <坏提交的commit>
```

- 禁止在 `main` 上执行 `git reset --hard`、`git push --force`
- 可交付里程碑从 `main` 打 tag，遵循语义化版本：`v0.1.0`、`v0.2.0`
- 只从 `main` 打 tag，不从未合并分支发版

## 7. 绝对不提交的内容

- `.env`、真实密钥、账号密码、数据库连接串等任何凭据
- `node_modules/`、构建产物、本地缓存、日志
- 仅为本地调试临时生成的脚本、数据、截图，用完即删
- 他人未完成的改动（不要顺手提交对方工作区里的文件）

需要新增环境变量时，只更新 `.env.example` 里的示例项，不提交真实值。

## 8. 提交前检查清单

- 只 `git add` 了自己本次改动的具体文件，没有用 `git add .`
- 提交信息符合第 3 节格式
- 没有把共享文件的其他改动夹带进来
- 新需求在独立分支上完成，已合并进 `main` 并删除本地分支
- 前端改动已跑过 lint、类型检查、构建；后端改动已完成对应自测
- 推送后确认远程状态（`git status -sb` 显示已同步）
