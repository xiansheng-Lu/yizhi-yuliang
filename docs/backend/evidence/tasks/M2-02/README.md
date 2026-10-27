# M2-02 中枢项目、对话与消息

2026-10-27；负责人BE；FE协作F06；代码基线 c8a5af8b5d85e1817dd8b0bc94ce5ea0779f6c4e。
状态WAITING_REVIEW；采用M2-01a/M0-12技术输入，不表示G0或人工/联合门禁已通过。

三条DoD的后端断言通过：冻结六接口与DTO；同键同载荷持久回放/异载荷409/并发只写一turn；双租户、同租户他用户拒绝，独立客户端重新登录读到同记录。
[verification.json](verification.json)归档59项unit/contract、97项真实integration及三类CI敏感性通过；开发库已升级0010。

私有表RLS同时验证租户和actor；两者同首条SQL设置LOCAL，池复用/无效actor均清理。项目/消息/任务归类有复合FK和当前权限/范围复查，写入同事务追加不含正文的审计。
助手当前明确UNRECOGNIZED与能力不可用，已保存scope/解释不伪造模型数据。command_jobs仅建DRAFT任务标识和对象FK底座，应用不可创建或改状态；完整申请与生命周期归M2-05a/b。绑定测试ACTION/DRAFT为受控数据库夹具。

默认身份接口仍关闭，F01/F06真实浏览器接线与适用BE/FE具名复核PENDING，不以双TestClient代替浏览器联合验收。回退应用保留历史事实，不删除用户记录。
用户已提供新的SMTP资源，TLS登录实测通过但尚未发送邮件；下一卡立即回到M0-10做验证码闭环与实际收件验证。
