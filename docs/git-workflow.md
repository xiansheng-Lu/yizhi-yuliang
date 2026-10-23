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

### 什么改动可以直接推 main

前端满足以下全部条件时，完成自测后可直接提交并推送 `main`：

- 只改 `src/`、`public/` 下的前端文件
- 不涉及接口契约、不新增依赖、不改构建配置
- 不碰共享文件（见第 3 节）
- 本机 lint、类型检查、构建通过

以下情况即使是前端改动也必须走分支 + PR：

- 跨模块重构、目录结构调整
- 新增页面体系或新业务域
- 修改接口契约、`package.json`、构建与 lint 配置
- 多人同时在改同一目录
- 一次改动超过约 300 行

后端改动一律走分支 + PR，不直接推 `main`。

## 2. 目录归属

| 归属 | 目录或文件 | 修改人 |
|---|---|---|
| 前端 | `src/`、`public/` | 前端 |
| 后端 | `backend/`、`docs/backend/` | 后端 |
| 共享 | `package.json`、`package-lock.json`、`.env.example`、`.gitignore`、`README.md`、`AGENTS.md`、`docs/` 根目录、`scripts/`、`tests/` | 改动前先沟通，一次只一人动 |

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

### 4.1 前端直推 main

```powershell
git checkout main
git pull --ff-only origin main
# 动手改代码
git add src/具体文件路径
git commit -m "fix(frontend): correct budget slider overflow on mobile"
git push origin main
```

推送被拒说明远程已有新提交，先拉取再推：

```powershell
git pull --rebase origin main
git push origin main
```

### 4.2 后端分支 + PR

```powershell
git checkout main
git pull --ff-only origin main
git checkout -b feat/backend-report-queue

# 开发过程中，每天至少同步一次主干
git fetch origin
git rebase origin/main

# 提交并推送
git add backend/具体文件路径
git commit -m "feat(backend): add report generation queue"
git push -u origin feat/backend-report-queue
```

然后在 GitHub 上开 PR 合并进 `main`，合并方式选 Squash 或 Merge commit，打开 PR 时写清：改了什么、影响范围、怎么自测、是否涉及共享文件。合并完成后删除远程分支：

```powershell
git push origin --delete feat/backend-report-queue
```

### 4.3 每日节奏

- 每天开工前先 `git pull --ff-only origin main`
- 开发中的分支每天至少 rebase 一次 `origin/main`
- 功能做完当天合并，不积压，避免出现多个并行分支最后一次性合并

## 5. 冲突处理

- 谁改动谁解决冲突，不把冲突丢给对方
- 解决冲突逐行看，禁止"全部接受当前分支"或"全部接受对方分支"
- 冲突涉及对方负责的目录时，先沟通再动手，必要时一起看
- 自己分支 rebase 后需要推送，用 `git push --force-with-lease`；`main` 上永远禁止强推

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
- 前端改动已跑过 lint、类型检查、构建；后端改动已完成对应自测
- 推送后确认远程状态（`git status -sb` 显示已同步）
