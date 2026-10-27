# 浏览器权限拒绝诊断材料（待用户提交）

日期：2026-10-27，Asia/Shanghai。本文件是反馈草稿，尚未向支持或任何第三方发送。

桌面应用26.1021.3705.0。Browser Use实际使用内置CLI 0.164.0-alpha.11.1，独立CLI为0.163.0。目标为本机临时测试后端`http://127.0.0.1:18000/healthz`，前端`http://127.0.0.1:3000`。

用户已授权两处本地服务，设置界面显示浏览权限“始终允许”。用户配置当前也明确允许这两个HTTP origin。本轮通过实际内置CLI的新诊断进程调用官方`config/read(includeLayers=true,cwd=项目)`，两条Allow来源均为用户配置，系统层为空；`configRequirements/read`返回null。该诊断进程没有读取活动聊天覆盖或浏览器当前保存的审批，因此不能据此排除活动进程的额外规则。

用户侧能够手动打开后端健康页，桌面日志出现dom-ready；但Agent通过原内置浏览器读取该已打开页，仍被拒绝：

> A saved user permission setting blocks this action. A saved permission or managed policy denies access.

拒绝阶段为“could not acquire authority to observe page content”。没有通过换端口、代理、其他浏览器或CDP重试受限目标，也没有更改安全开关或权限存储。当天桌面日志未提供对应拒绝规则来源。旧的data:text/html错误页拒绝已与本次HTTP origin拒绝区分。

18:59本机检查：前端/login HTTP200；后端/readyz HTTP200、ready=true、degraded=false，业务/认证PostgreSQL、Redis、RabbitMQ均UP。测试服务使用隔离临时数据库和合成身份，不外发邮件或操作真实广告账户。浏览器认证、写入、退出尚未执行。

请协助检查本聊天的origin approval gate实际拒绝来源：是否为保存的站点拒绝、聊天/进程旧状态、未在上述诊断进程返回的受管规则，或权限匹配/加载异常；如能定位，请提供受支持的定向修复或重新加载方式。

用户提交时可通过应用`/feedback`附当前聊天ID和本材料。无需附完整config.toml、auth.json、Cookie、数据库连接串、口令或原始日志。结构化脱敏结果见[permission-diagnosis.json](permission-diagnosis.json)。
