# M1-12 小店罗盘经营事实

2026-10-28；负责人BE，协作F04/F08；技术实现与本地专项通过，真实E02与BE/FE具名复核E08保留，不登记DONE。

## 输入与来源

直接依赖M1-07/M1-15b已有技术基线。独立项目源码 `D:/codex/wx_shop/dashboard/collect_data.py:321-335` 明确每日POST `/channels/ec/compass/shop/overall/get`，请求ds=YYYYMMDD，响应data内读取pay_gmv/pay_uv/pay_order_cnt/pay_refund_gmv/live_pay_gmv/feed_pay_gmv/product_click_uv。本地固定288项目录提供同路径及官方文档 https://developers.weixin.qq.com/doc/store/shop/API/compass/api_getshopoverall 。旧项目测试包含对应合成响应。源码及测试仅确认已经采用的协议形状，不证明平台金额单位、时区、退款或订单去重语义；未复制真实凭据/业务响应，未调用平台。官方正文此次web open仍失败，因此这些含义由独立口径依据明确补足，默认不推断。

## 实现与交付

0049_shop_compass依赖0048_shop_product_details，增加追加日观察、独立维护口径依据及逐run授权版本固定三表，全部强RLS。运行身份只读口径表，不得自授/改写口径依据或历史观察。

ShopCompassSync使用固定POST读取spec，显式开放接口和scope；复用require_shop_read及既有GovernedClient、脱敏存档、Redis准入、PG页意图/租约/游标。参数仅ds，不接受任意路径或过滤器。每日逻辑来源稳定，原run不能跨授权版本/日期/绑定恢复；轮换后用新run从头读取。请求前和页提交前重核当前授权、原生账户/应用/产品线与能力。观察、镜像、确认页和可生成的事实同事务；重复完成run不出站，失败保留旧观察，投影异常整页回滚。

解析只保留七字段精确非负整数文本或null；空data明确EMPTY_DATA，缺data或错误码不作为空成功。未知/PII字段不进入镜像或业务观察。零为"0"，缺值不补0。sourceUpdatedAt保持null，不将抓取时间冒充平台更新时间。内部observations视图显示NOT_SYNCED/OBSERVED/EMPTY_DATA/UPSTREAM_ERROR和最后抓取时间；未授权/未接入在能力层明确拒绝，不能呈现零；新鲜度策略未接入时显示UNCONFIRMED。

缺独立CompassContract时只保存原生观察和单位/时区/退款/去重/来源时间缺口，不投影规范经营事实。维护者可录入明确店铺归属、单位CNY_MINOR/CNY_YUAN_INTEGER、Asia/Shanghai、退款/订单去重语义、定义键、资料指纹和有效期的依据；过期/撤销或账户绑定不符不生效。只有唯一全店归属依据仍有效时，通过FactWriter生成wxshop产品线的paid_gmv_minor/paid_order_count/refund_gmv_minor，精确单位转换并拒绝超JS安全整数。缺项仍缺失，不创造退款0，不由广告归因GMV相加或推算经营事实。来源事实和实际页/出站ID关联，历史修订沿用原FactWriter。

## 验证

- 单元命令：WSL backend下 `/home/lenovo/.cache/yuliang-venv/bin/pytest tests/unit/test_shop_compass_protocol.py -q`：11 passed/0.54s。
- 真实PG/Redis专项：`python scripts/test_infrastructure.py --group shop-compass --details`：最终7 passed/6.96s。覆盖原生0/缺失/PII、重复run不重发、失败保旧、独立口径下经营事实、运行角色禁自授、授权版本轮换及稳定来源、投影失败整页回滚、状态与撤scope。
- 首轮2项测试预期不符：业务错误由解析ValueError关闭，FactWriter对缺失不建假事实行；调整断言如实核对。扩展故障测试发现stop上下文版本不匹配导致状态未标记，修复为稳定storage_ctx后7项复验通过。
- Ruff及新增五个源码mypy通过。上游身份、scope、口径资料及HTTP均为合成；本地PG/Redis是真实，P验收NOT_RUN，真实平台/付费调用0。

三条DoD：技术层分别覆盖明确口径的规范化和来源关联；经营/广告独立且缺项保留；零/空/未接入/错误可解释。正式来源抽样一致与真实口径仍需E02，不用合成依据代替。父卡最终验收和BE/F04/F08复核PENDING。

回滚：前向停用只读入口/撤销口径依据，保留来源和历史；不降级删表，不触发平台写入。本卡不提供公共业务HTTP或FrontendSnapshot（按对应后续卡接线）；root负责runtime共享接线。未执行Git提交。
