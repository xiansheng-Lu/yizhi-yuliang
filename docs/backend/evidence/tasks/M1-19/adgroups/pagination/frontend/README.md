# ADGROUP 分页：实际前端消费者验证

状态：捕获 HTTP 的真实 TypeScript 消费技术 PASS；浏览器/F12、人工签字未执行。

输入由本批真实 PG/HTTP 集成测试产生，来源为合成上游，并非真实平台。捕获文件、身份字段与随机 selector 仅留私有 `.runtime`，本目录只归档断言摘要及前端源文件 SHA256，不含 cookie、CSRF、会话、headers 或游标原值。

实际执行（仓库根目录）：

```powershell
node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json .runtime/check-adgroup-pagination-frontend.mts .runtime/adgroup-pagination-http.json docs/backend/evidence/tasks/M1-19/adgroups/pagination/frontend
```

最终退出码 0。初次执行因捕获格式使用 `primary.input/page` 而脚本预期顶层 `input/page` 失败；仅调整私有验证脚本适配真实格式，业务响应未修改。

- 实际调用现有 `PlatformService.context/query`、`queryBlock`、响应 validator 和 `integrationScopeKey`，fetch 只从文件返回捕获数据，不进行真实网络请求。
- 连续消费 12 页共 1101 行，每页不超过 100；按实际 `nextCursor` 请求后页，终页为 null。累积的 1101 个原生 ID 无重复，与捕获中的完整预期源 ID 集合和顺序一致；另独立用 BigInt 核验数值升序，避免字符串字典序。
- 同一实际 cursor 请求的真实重放响应经消费者处理后，与原页完全一致。所有页 `syncedAt` 固定；全部 `version=null`，没有获得写许可。
- 本捕获 1101 行预算均为 null；没有伪造真实 0 的分页用例。未知与真实 0 的基础消费证据见上级 `adgroups/frontend/`，此处不重复声称已在分页捕获验证。
- UI 的加载更多 cursor、readKey 防陈旧追加及原生 ID 下钻仅核对既有源码。累积集合断言由脚本执行，没有运行 React UI 或浏览器。

本次只证明已捕获的 1101 行完整消费，不将总容量上限 10000 声称为已在前端实际消费。服务端过期、撤权、跨身份、批次变化、缓存容量与并发拒绝依赖 owner 的独立 PG 证据，文件消费不能替代这些验证。真实平台调用、数据库调用与付费调用均为 0。
