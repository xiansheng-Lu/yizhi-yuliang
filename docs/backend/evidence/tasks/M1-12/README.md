# M1-12 罗盘经营事实：来源调研检查点

2026-10-28；BE；仅调研，未实现/未验收。直接依赖M1-07/M1-15b技术基线存在；真实E02与具名复核E08保留。

本地目录 `docs/shared/reference/platform-api-reference/微信小店API全量接口图谱.html` 的RAW数据包含罗盘商家版10项，来源自述sdk/wxshop_api_registry.py，docVersion 2026-10-21。目录仅证明本地索引，不证明接口适用、请求/响应协议及账户授权。

M1-12候选来源为POST `/channels/ec/compass/shop/overall/get`（电商数据概览）；索引官方文档：https://developers.weixin.qq.com/doc/store/shop/API/compass/api_getshopoverall 。相关商品来源为 `/channels/ec/compass/shop/product/data/get`，直播列表为 `/channels/ec/compass/shop/live/list/get`。达人overall不是全店overall，不相互替代。

本次尝试web open上述官方overall文档，返回non-retryable error，正文未取得。故请求字段、日期边界/时区、响应指标名称/单位、退款及去重范围、平台更新时间、分页和权限范围均未确认。没有据路径猜测字段，没有将订单列表求和冒充罗盘经营事实，没有以ADQ归因GMV替代全店GMV。

后续先取得可核对的官方正文或固定SDK实现，再设计只读协议、规范事实和真实本地PG测试。最终三条DoD仍需：正式来源抽样一致且明确口径；经营与广告归因独立、缺项保留；真实0/空数据/未接入/上游错误可解释。当前全部PENDING，真实平台调用0/付费0。

解除资源缺口可由业务方提供该应用可用的官方罗盘接口资料/权限截图及脱敏响应示例，或研发通过官方公开文档取回协议；实际E02账户映射与只读许可单列。调研不阻塞其他任务。
