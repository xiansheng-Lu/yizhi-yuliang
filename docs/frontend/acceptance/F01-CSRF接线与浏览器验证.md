# F01 CSRF接线与浏览器验证

日期：2026-10-27（Asia/Shanghai）；基线a1d67c7cd15acb38da9ef3a56c7954d997eccc4d；FE范围，coding agent实施。状态：代码与自动检查通过，真实浏览器认证闭环BLOCKED，BE/FE最终复核PENDING。

## 接线结果

ApiClient在首次写入前GET /v1/auth/csrf，校验command-job-v1.0信封、token与未来有效期，只在内存缓存并单飞获取。登录/发码/退出及各域非GET/HEAD/OPTIONS请求携带X-CSRF-Token，Cookie仍由浏览器处理，FormData不设置错误Content-Type。并发等待共享引导；单个调用者取消不取消其他等待者；引导与原写共享15秒操作预算。

发码与邮箱码登录复用同一preauth令牌；匿名focus/session=null检查不使挑战失效。登录凭证401保留原挑战，允许用户更正密码/验证码；受保护资源401清认证上下文并通知一次。成功登录、退出、已知会话消失或身份/租户变化清理旧令牌，认证epoch阻止迟到引导/旧401/403影响新身份。CSRF GET的CAS冲突仅允许安全GET有界再取一次；原业务写请求不自动重放。写403只失效令牌并保留错误，邮箱码页面清旧challenge/code，要求用户明确重新取码，不自动发邮件。

WorkspaceProvider在owner变化、空身份、401与退出边界使共享snapshot读取失效，generation丢弃旧结果；登出开始立即隔离旧在途读取，成功后清身份和旧快照，失败重新核对会话并保留原错误。无新增SSE流或虚构实时能力。

## 验证与范围

实际命令、源码摘要、环境和阻塞见[verification.json](evidence/f01-csrf/verification.json)。项目级Node24.19.0按官方SHA256校验，本机全局Node与PATH不变；新依赖为0。

| 验证 | 结果 | 含义 |
|---|---|---|
| node scripts/run.mjs test | 210/210 PASS | 20项CSRF、6项身份、4项provider/读取竞态及既有业务回归；fetch夹具不是后端/浏览器验收 |
| typecheck / lint / build | PASS | Next16.2.6/React19生产构建可生成；临时输出目录不入Git |
| 前端AST与冻结源码 | 53路由/56调用点/133类型/15源PASS | 新增实际CSRF GET，引导已存在的后端接口；业务请求体/版本不改 |
| 黄金契约 | 原45项PASS | 专用CSRF响应与业务请求断言分开，未放松验证器 |
| 真实浏览器前端页面 | PASS | 127.0.0.1:3000/login可渲染，截图仅证明页面与阻塞态 |
| 真实浏览器认证/写入/退出 | BLOCKED | 127.0.0.1:18000仍被保存权限/受管策略拒绝；没有成功登录或业务写证据 |

既有首页样式未改。正式main在9a18d90按用户授权调整标题字距/行距并更新docs/shared/baselines/homepage-baseline.json，但旧测试仍硬编码原型CSS摘要；本卡将断言改为使用正式files基线，保留原型HTML归一化/JS断言。样式当前main与工作区摘要一致，不以更新测试掩盖本轮CSS改动。

## 浏览器阻塞及恢复入口

用户已授权两个本地地址并多次报告调整站点设置。实际工具先拒绝两个地址；3000随后可访问，但对18000的独立导航仍返回“saved user permission setting / managed policy denies access”。没有使用换端口、代理、其他浏览器或CDP绕过限制。参见[策略记录](evidence/f01-csrf/browser-policy.json)和[实际页面](evidence/f01-csrf/browser-blocked.png)。浏览器认证闭环没有执行，不能由Node fetch桩、页面200或后端既有测试替代。

2026-10-27 17:38（Asia/Shanghai）按用户“继续验证”恢复请求重启临时服务复测：前端/login与后端/healthz本机HTTP均200；同一内置浏览器直接访问18000/healthz仍被origin approval gate以保存权限或受管策略拒绝。此前用户截图中两条浏览权限均为“始终允许”，本次无法确定拒绝来自哪条规则，也未独立确认应用进程是否完整重启。没有浏览器登录提交或业务写入，未生成新的页面截图；原截图仅代表此前页面。临时服务与数据库再次清理，app_migrate为NOLOGIN。该复测不解除F01_BROWSER/G0阻塞，不要求用户重复已经给出的聊天授权或相同设置操作。

测试后端为本轮独立UUID数据库，使用临时OPERATOR/OWNER/AGENCY_OPERATOR/AUDITOR身份，邮件仅CaptureMail端口、无外发、无平台写。未在浏览器提交任何登录凭据；测试库及本轮服务已清理，app_migrate恢复NOLOGIN，见[cleanup.json](evidence/f01-csrf/cleanup.json)。实际配置、测试口令、Cookie和令牌不入Git。

解除条件：应用/受管环境真正允许浏览器访问127.0.0.1:18000后，在同样临时真实PG环境执行密码/邮箱码登录、会话CSRF轮换、昵称/项目写入、刷新与第二身份、缺失/伪造CSRF、权限拒绝和退出失效；PC1440及移动390留证。生产TLS/Cookie、真实邮件收件与平台授权分别引用适用证据，不用本地HTTP冒充正式环境。

M0-11b第三条DoD/F01仍待浏览器证据，G0继续BLOCKED且人工签字不代填；M1-01及后续主链未领取。恢复后按卡继续，不重新请求已给出的开发或DeepSeek许可。回退应用可停止新客户端行为，后端身份开关仍默认关闭，本卡没有放宽服务端CSRF、CORS或权限。
