# ADGROUP 真实 HTTP 捕获的前端消费者核验

状态：技术消费核验 PASS；浏览器/F12 与人工签字未执行。独立复核仅读取 beans 的真实本地 PG/HTTP 合成业务输出，未访问数据库、真实平台或付费模型。

执行命令（仓库根目录）：

```powershell
node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json .runtime/check-adgroup-query-frontend.mts .runtime/adgroup-query-http-capture.json docs/backend/evidence/tasks/M1-19/adgroups/frontend
```

使用现有 `PlatformService.context/query`、`queryBlock`、响应 validator 与 `integrationScopeKey`，通过注入 fetch 消费捕获文件。最终运行退出码 0；实际源码 SHA256 和无凭据结果见 `consumer-verification.json`。捕获文件与私有脚本只留 `.runtime`，不归档 cookie、CSRF、会话或 headers 原值。

- 3 组响应共消费 8 行（重复筛选/证明场景，3 个不同原生广告组），包含 5 行未知预算、5 行未知源时间与 3 行真实零预算。未知值保留 null/“源更新时间未提供”，不变成零或同步时间。
- 真实父筛选 `nativeParentId=10` 返回原生广告组 11、12；全部 `version=null`，保持只读。没有把原生 ID 声称为内部 PLAN ID，也没有把 target ROI 作为表现 ROI。
- 6 种注入的非法响应被实际前端 validator 拒绝；这是客户端负例，并非服务器负例。缺路径证明、CAMPAIGN 和未实现 creatives 下钻在 HTTP 前被真实 queryBlock 拒绝。
- 全部成功页 `nextCursor=null`，当前没有加载更多。UI 原生 ID 下钻与 nextCursor 条件显示仅作源代码核对，未执行浏览器交互。

范围边界：`query_plans.py` 对超过 1000 个匹配广告组明确返回 422 并要求收窄，不返回截断集合。既有 PG 测试使用 1001 个来源广告组，验证超限拒绝与父筛选后 1000 行成功；本次文件消费没有捕获/消费超限错误，也没有验证 5000 行展示或分页。完整分页仍为 M1-19 剩余工作。当前页面是完整最新配置观察，选择的日期范围不代表历史配置查询；计划历史/完整 V8 workspace 不计完成。
