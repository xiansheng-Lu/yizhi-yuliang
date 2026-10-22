# AI直播作战室前端交接

直播作战室继续使用 `/app/monitor`，按主画面、经营指标、趋势、AI指挥台、异常监控、关联计划排列。直播状态、模块请求状态、数据来源独立管理。后端接口、ROI口径和审批执行规则没有改动。

## 数据接入现状

唯一经营读取接口仍为 `GET /v1/frontend/snapshot`。在线人数来自选中场次；广告消耗、支付归因GMV与ROI来自同店铺、场次、平台和统计日的关联计划，沿用 `businessTotals`，包含暂停计划历史消耗。归因窗口不一致时不合计。每个计划保留自己的来源更新时间。

累计观看和成交订单仅在场次返回对应字段且平台范围匹配时显示，否则为 `--`。无视频流时展示明确的空状态；封面标为静态图片。当前未提供时间序列，所以趋势区提供指标和时间切换，并明确提示待接入，不从轮询样本生成曲线。

流量与转化异常缺少时间序列、分母和阈值，显示待数据。ROI只与现有企业经营目标对照，保留来源更新时间及过期提示。展示延迟值不等于确认当前表现正常。

Mock只有显式配置 `NEXT_PUBLIC_DATA_MODE=mock` 才会启用。固定样本持续标为“模拟数据 · 本地演示”，不生成随机增长；API失败不会降级为Mock。

## 刷新和新鲜度

| 模块 | 建议获取间隔 | 默认过期阈值 | 环境配置 |
| --- | --- | --- | --- |
| 直播状态及在线 | 15秒 | 45秒 | `NEXT_PUBLIC_LIVE_MAX_AGE_MS` |
| GMV、订单、消耗及ROI | 30秒 | 120秒 | `NEXT_PUBLIC_COMMERCE_MAX_AGE_MS` |
| 趋势 | 60秒，缺接口时不轮询 | 180秒 | `NEXT_PUBLIC_TRENDS_MAX_AGE_MS` |
| AI任务 | 10秒 | 30秒 | `NEXT_PUBLIC_TASKS_MAX_AGE_MS` |
| 关联计划 | 30秒 | 120秒 | `NEXT_PUBLIC_PLANS_MAX_AGE_MS` |

配置单位为毫秒。陆文龙确认快照接口最小允许间隔后，设置 `NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS` 并重新构建。API模式未配置该值时，只进行首次、筛选变化、业务操作后的同步和手动刷新，不猜测后台限频；界面明确提示“接口限频待确认 · 仅手动刷新”。Mock模式可以直接验证调度。

有效模块间隔取建议值与接口最小间隔的较大值。到期模块共用一次快照请求；同服务同一时间最多一个快照读取。共享读取层还约束工作区初始化、筛选后的读取与手动刷新。实际调度可能为合并请求稍后执行，不会早于限频窗口。

过期阈值按模块独立配置，并至少覆盖两倍接口最小间隔。需结合平台实际发布周期和可接受延迟调整。源时间缺失或明显超前时无法确认新鲜度。任务 `updated_at` 是状态变更时间，因此任务新鲜度以成功同步时间判断。

顶部“最近同步”表示最近成功获取的模块时间；“平台更新”使用当前展示来源中最早的时间，任一相关来源时间未知则显示 `--`。每个模块分别显示平台更新时间或任务状态变更时间，以及最近成功同步时间。成功轮询不会修改平台时间。

首次读取复用刚由工作区取得的快照，避免初始化组件重复请求；进入页面时已有快照较旧，则立即读取。未选择场次时不启动场次读取；全局登录及筛选目录所需快照仍由工作区管理。

页面隐藏时暂停自动调度，恢复时仅刷新到期模块；未开播和已结束场次不轮询。直播转为结束时更新所有模块的最新值再停止。手动按钮在请求和冷却期间禁用，重复点击合并为同一次操作。

失败保留有效值和原来源时间，显示“获取失败”“数据更新延迟”。重试按10、20、40秒递增，封顶5分钟，同时服从接口间隔和429的 `Retry-After`。切换范围先暂停旧控制器，再串行读取新范围；不匹配的范围和来源被拒绝。卸载时清理计时器、取消本视图的读取；如果其他组件仍需要同一请求，不中断它的读取。

## 陆文龙需要补充的只读契约

保持现有成功封装 `data / requestId / schemaVersion: command-job-v1.0` 和现有Cookie权限校验。下列场次字段已经有前端可选读取位置；字段缺失时不会报成0。

| 场次字段 | 类型 | 约定 |
| --- | --- | --- |
| `status` | `NOT_STARTED / LIVE / ENDED / UNKNOWN` | 真实模式不能返回 `MOCK_LIVE` |
| `connectorId` | 现有ConnectorId | 说明场次指标所属的平台范围；未知时不能用于特定平台统计 |
| `startedAt / endedAt` | ISO8601时间或null | 缺失时开播时长显示 `--` |
| `coverUrl / videoUrl` | 获权HTTPS资源或null | 明确浏览器可用的流格式、鉴权及有效期；不提供则保持空状态 |
| `totalViewers / paidOrders` | 非负数或null | 必须说明累计观看去重规则、订单定义与统计日边界 |
| `sourceUpdatedAt` | ISO8601时间或null | 必须是平台数据发布时间；不能写成请求完成时间 |

订单现通过经营模块每30秒应用场次 `paidOrders`；若订单与直播在线来自不同周期，需提供独立指标来源时间。跨日场次应明确“本场累计”和“统计日累计”的覆盖范围，前端不能自行等同。

趋势读取接口尚未约定，前端没有调用猜测的路径。建议先确认以下数据结构及查询语义，再接入现有控制器的趋势模块：

```ts
type LiveTrendQuery = {
  shop_id: string;
  live_session_id: string;
  connector_id: string;
  start_at: string;
  end_at: string;
  timezone: "Asia/Shanghai";
  granularity_seconds: number;
};
type LiveTrendResult = {
  scope: LiveTrendQuery;
  source: "API";
  source_updated_at: string | null;
  attribution_window: string;
  gmv_definition: string;
  interaction_definition: string;
  points: {
    at: string;
    online: number | null;
    paid_gmv_minor: number | null;
    interactions: number | null;
  }[];
};
```

需明确GMV是每桶增量还是累计值、金额币种与最小单位、互动包括哪些事件、空桶语义，以及时间切换后的历史查询限频。流量/转化异常还需同范围比较窗口、观看与订单分母、规则阈值和来源时间。快照与趋势接口分别确认请求频率、上游发布周期、缓存延迟、最终数据标志和429重试间隔。

## 前端文件

主要实现为 `src/features/live-room-page.tsx`、`src/styles/live-room.css`、`src/lib/live-room.ts`、`src/services/live-room-controller.ts`、`src/services/snapshot-reader.ts`。`provider`、`workspace-entry`、`operating-context` 和顶部刷新入口接入共享读取与保留旧数据；`domain`、前端服务契约和API客户端增加可选字段及取消读取能力。配置见 `.env.example`，刷新验收见 `tests/live-room.test.ts`。

原有指令解析、对象确认、风控、任务审批、执行与核验组件继续复用，申请操作只填入指令草稿，不自动提交或执行。回放时间轴、话术和货盘保留在可展开区域；已有路由与账号权限保留。

## 验证边界

自动刷新及请求故障使用可控时钟和假服务验证；浏览器以显式Mock模式验证布局、真实页面筛选与空状态。真实平台联调、趋势、视频流和流量/转化异常等待上述契约及后端数据，不声明已接入。可复核证据位于 `docs/evidence/live-room/`。
