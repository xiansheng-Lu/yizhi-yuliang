# M1-14 微信豆合法能力摸底

2026-10-28；负责人BE，协作F03/F11；状态BLOCKED（E07）。本轮仅完成已有实现核查及本地安全回归，未获得官方渠道结论、未执行真实平台请求。

基线ef40e99。直接依赖M1-07与M1-02技术实现已存在，真实平台验收及BE/FE具名复核仍保留。允许范围仅独立测试与本卡证据，无迁移、无新执行入口。

## 来源及结论边界

- `docs/backend/evidence/tasks/M1-01/README.md`：目录资料关联为UNCONFIRMED/referenceOnly，微信豆无API来源绑定。此为仓库核查记录，不是官方不存在接口的证明。
- `docs/shared/contracts/platform-v9-api-contract.md`：微信豆合法专属写接口及授权证明待补；金额为独立BEAN整数，不换算人民币，不合并ADQ钱包或小店结算。
- `backend/src/yuliang/modules/capability/models.py`：现有aggregate对beans固定NOT_INTEGRATED；即便合成的六阶段证据齐全，也不产生operations或灰度validationReady。

正式接口URL、scope、商户适用范围、合法渠道授权和实调结果均为NOT_PROVIDED/NOT_RUN。本轮未访问官方站点，不把本地目录核查描述成官方检索完成，不下“接口不存在”结论。不能由其他腾讯产品的接口或scope推定微信豆可接入。

## DoD与验证

1. 正式接口/scope/实调结论：BLOCKED_E07，缺失项逐项列明。
2. 无写证据时关闭：PASS_LOCAL_UNIT；BEAN金额规则沿用已有契约，此轮没有新增钱包、金额投影或单位转换，未验证真实资金。
3. 不伪造成功：保留NOT_INTEGRATED与BLOCKED，尚不能以“官方无接口”关闭整卡。

实际命令（WSL backend目录）：`/home/lenovo/.cache/yuliang-venv/bin/pytest tests/unit/test_beans_capability_boundary.py -q`。
实际结果：4 passed，0.19秒，1条既有Starlette弃用warning；覆盖READ/WRITE各自无证据及完整合成证据两种情况。真实平台调用0，付费调用0。

交付文件：独立回归测试和本说明。测试复用既有能力测试的合成target/proofs构造，实际调用生产aggregate。没有新增运行时代码，回滚仅撤销测试/说明。负责人及F03/F11复核PENDING。

E07解除条件：业务方提供官方渠道/专属接口文档及书面可接入结论（包括允许scope、商户/账户和读写范围）；如正式答复不可接入，归档原始依据后可形成明确不可接入结论。无依据期间继续保持关闭，不阻塞其他后端开发。
