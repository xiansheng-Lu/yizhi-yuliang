# M1-13 小店订单与结算只读

2026-10-28，BE，协作F05/F08，WAITING_REVIEW（技术观察层）。基于M1-07原生小店适配、M1-15b原子镜像/来源历史与本轮持续多agent授权。真实平台E02、BE/FE签字E08及G0保留。

实际深入读取独立wx_shop项目SDK注册表、通用调用器与dashboard/collect_data.py，确认order/get、order_detail.price_info.order_price、create_time/update_time及getbalance的available_amount/pending_amount。官方页直接读取失败，不能据此声明单位与时间语义已由官方证实。本轮未读取凭据。源文件摘要见source-spec.json。

实现：ShopOrderSync复用当前应用/授权版本/原生关联/scope/能力守卫和GovernedPlatformClient出站；固定按已知ID读取订单，不沿用独立项目未完整分页的列表。SyncStore确认页、镜像、来源历史与0050业务观察同事务；只保存订单ID、原生状态/金额/时间，订单收件人、号码、地址、买家标识和商品自由文本从业务及档案剔除。MERCHANT_SETTLEMENT独立于广告钱包；余额缺值不补0、未知单位保持UNCONFIRMED。

0050追加观察表与维护员独立结算单位证明表。内部settlement_view再次核对当前授权、scope和connection.manage，绑定具体账户/店铺/binding与完整run。仅在有效独立CNY单位证明下给出精确且JS安全的minor值，明确标为历史观察，不宣称当前余额、新鲜度或可用广告预算。未获取原生时间单位与退款事实来源前不做订单日汇总、退款累计或迟到业务覆盖；重复run不再读取，重拉追加观察，不制造退款或GMV事实。

三条DoD：最小字段/PII剔除本地验证；订单与退款幂等以观察层及显式缺口交付，完整列表与售后协议已固定SDK补全，真实退款财务含义待E02；资金产品线隔离与独立单位证明技术验证，真实财务口径及P验收未运行。

回滚：前向停用来源/撤销证明，保留观察和来源历史，不执行破坏性downgrade。无真实平台/付费调用。实际测试结果另记verification.json；BE/F05/F08复核PENDING。

## 固定SDK分页和售后补全

进一步核对zsmhub/wx-channels-sdk固定97497af9d9cd3dfbb30ca71aa8c0bc8be52207c6的订单/售后/余额五份源文件，实际实现ORDER_LIST与AFTERSALE_LIST的has_more/next_key分页，严格拒绝缺失完整性标识和非终页无游标；订单按update_time_range查询，售后按begin/end_create_time查询，本地单次最多七天，不宣称平台官方上限。列表保留标识页不冒充已获取详情，详情入口分别使用当前catalog/binding的订单和售后ID。AFTERSALE保留原生退款状态和refund_info.amount、create/update_time，不保存原因/描述/证据图片/联系电话/openid等PII。未证明状态财务含义、单位和来源时间转换前不累计退款或销售额，不重复run、不产生重复财务事实。

实际验证：WSL，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv；uv run --frozen python scripts/test_infrastructure.py --group shop-orders --details：8通过，7.61秒；uv run --frozen pytest tests/unit/test_shop_orders.py -q：10通过，0.64秒；本卡3源码mypy和Ruff均通过。前期合成余额fixture缩进导致2失败，修复后全部通过；无真实平台调用。当前技术交付可WAITING_REVIEW，真实P验收和财务口径E02、人工E08继续保留。

下一可准备卡：M4-02保守覆盖守卫按直接依赖核对；M1-18快照需要各镜像真正规范投影与覆盖数据，不能把本卡未确认单位的观察直接当GMV/退款汇总。
