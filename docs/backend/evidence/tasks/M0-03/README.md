# M0-03 存活、就绪与基础指标

2026-10-27；负责人BE，无FE协作；代码基线 898620fe56ee573b60c6d382418c6d3c192dc40c。
状态WAITING_REVIEW；采用M0-02/04/15技术输入，不代替前置或本卡签字。

三条DoD技术通过：健康依赖真实探测与库失效503；MQ暂停本批并保留申请/读取、Redis回PG会话；指标仅允许配置网段，脱敏且无租户业务明细。
[verification.json](verification.json)记录33项unit/contract及67项integration通过；另1项未启用的重启演练明确SKIP，非平台PASS。静态检查通过。

healthz只表示进程存活；readyz缓存1秒，数据库失效不就绪，MQ/Redis故障保留API并明确degraded。dispatchTransportReady只表示运输连接，Worker为UNOBSERVED，业务执行guard仍关闭。
指标当前只含固定依赖、API/运输就绪与采样时间，后续在途/资金指标不以固定零占位。来源信实际连接IP，不接受客户端转发头。
BE复核反代可信边界、采集网段与降级策略；应用回退不破坏持久业务事实。下一卡立即推进M0-05。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
