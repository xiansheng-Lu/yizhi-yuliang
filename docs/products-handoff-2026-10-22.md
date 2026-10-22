# 商品与货盘前端交接 · 2026-10-22

本次仅改前端，后端负责人陆文龙。保留 `/app/products`、左侧导航、现有权限和审批规则，未新增 Route Handler、数据库或商品写入接口。

## 已实现

- 店铺 / 场次本页筛选，严格限制当前身份的租户与店铺授权。当前快照契约返回所有已授权实体，因此本页筛选不额外写入全局统计范围。统计日沿用快照，明确显示 Asia/Shanghai。
- 备播、直播中、直播后视角；不同模式展示相应操作提示，复盘模式默认实际讲品片段。视角不改变真实直播状态。
- 商品图片、SKU、规格、售价、库存、分类、搜索、筛选及排序；图片只使用接口的 HTTPS 图片地址，缺图 / 加载失败使用明确占位。
- 店铺商品库与货盘草稿分开展示；多选添加 / 移除、按钮与拖放排序、开始分钟 / 讲解时长 / 要点编辑、重叠检查、连续排期。
- 草稿命名、本地保存、重新进入恢复、JSON 导出；按 API/Mock、租户、用户、店铺、场次、统计日隔离。保存失败、格式损坏、另一个窗口已保存均明确报告，不覆盖冲突草稿。
- 本场四项核心指标、独立店铺当日 / 单场商品指标、分时成交趋势、成交贡献排行、库存风险、讲品计划及实际回放片段。
- 当前商品的本地规则建议；正式模式通过现有指令解析接口请求只读分析，不创建任务。非 QUERY 返回不执行，换商品 / 口径 / 问题 / 数据后不展示旧分析结果。
- 加载骨架、未接入、暂无数据、请求失败保留上次快照、来源过期及来源时间未知状态；正式来源超过 5 分钟提示过期，刷新时间不会替代来源时间。无高频自动轮询。

## 当前真实接入状态

| 数据 / 能力 | 复用入口 | 本次情况 |
| --- | --- | --- |
| 登录、工作区与店铺授权 | 现有身份 / `canAccessPage` | 沿用，不授予额外权限 |
| 商品、SKU、售价、库存 | `GET /v1/frontend/snapshot` 的 products | 已复用读取与筛选；真实店铺未联调 |
| 店铺及直播场次 | 同快照 shops / liveSessions | 已复用；场次列表只显示当前已授权店铺 |
| 实际讲品片段 | 同快照 replaySegments | 按场次与真实 productId 关联；视频缺失时不伪造回放 |
| 广告数据 | 同快照 plans | 沿用其他页面入口，不把计划整体 ROI 分摊到 SKU |
| 商品只读分析 | `POST /v1/command-interpretations`，现有 `interpretCommand` | 携带商品、日期、口径、库存与可用经营指标；产品分析语义与响应质量待后端确认 |
| 货盘编辑 | 无正式接口 | 只保存浏览器本地草稿 / 导出，明确未同步 |

没有将旧版 `Product.clicks/paidOrders` 默认解释为店铺整体或直播场次指标。当前旧契约没有统计范围与日期，这些字段不会填入新指标。实付 GMV 绝不使用商品售价乘订单数推算。

Mock 模式由已有 `NEXT_PUBLIC_DATA_MODE=mock` 明确开启。商品页的 7 个示例 SKU、6 项演示货盘、示例摄影图片、分时图与模拟分析独立放在商品模块；API 模式不会补入这些数据。演示图片为说明性图片，不是真实商家商品图。没有实际接口数据时，正式页面显示未接入。

## 待陆文龙确认 / 补充的接口能力

目前仍只读取现有快照，以下为前端可选读取字段提案，**不是已经部署或保证可用的后端契约**。后端可以继续通过快照提供，或双方确认独立查询接口后再替换适配器。没有调用猜测出来的接口。

### 1. 商品详情与库存来源

每条 `products` 是一个明确的 `productId + skuId`。在原字段上可附加：

```ts
{
  imageUrl?: string | null; // HTTPS 商家商品图
  specification?: string;
  category?: string;
  sourceUpdatedAt?: string; // 商品 / 可售库存来源时间
  stockWarningThreshold?: number; // 可售件数预警线
  performance?: ProductPerformance[];
}
```

未提供库存预警线时，本页只使用 30 件作为观察阈值并明确标注，不写回库存规则。未提供 `sourceUpdatedAt` 时显示“未提供”，不能判断其新鲜度。

### 2. 店铺 / 场次商品经营指标与趋势

```ts
interface ProductPerformance {
  scope: "SHOP" | "LIVE_SESSION";
  shopId: string;
  liveSessionId?: string; // 场次口径必填；店铺口径不填
  skuId: string;
  date: string; // YYYY-MM-DD 的中国统计日
  timezone: "Asia/Shanghai";
  currency: "CNY";
  clicks: number | null;
  paidOrders: number | null;
  paidUnits: number | null; // 实际售出件数，不等同订单数
  paidGmvMinor: number | null; // 实付 GMV，整数分
  sourceUpdatedAt: string;
  trend?: {
    at: string;
    paidGmvMinor: number; // 每时间桶实付额，非累计值
    paidUnits: number;
  }[];
  adAttribution?: {
    skuId: string;
    spendMinor: number;
    paidGmvMinor: number; // 同 SKU、同父级统计口径、同归因窗口
    attributionWindow: string;
    sourceUpdatedAt: string;
  };
}
```

相同范围出现多条冲突指标时前端不自行选择；null 与真实 0 分开。所有 SKU 指标都齐全且正式货盘完整时才计算场次汇总；部分返回时只展示可用排行，不计算整体贡献率。点击支付率使用同范围支付订单数 / 点击数；0 分母或订单超过点击时不计算。ROI 仅在有同 SKU 真实归因、正数消耗和归因窗口时显示。

### 3. 本场完整货盘 / 讲解计划

快照可选字段 `productLineups?: ProductLineup[]`：

```ts
interface ProductLineup {
  id: string;
  shopId: string;
  liveSessionId: string;
  sourceUpdatedAt: string;
  items: {
    productId: string;
    skuId: string;
    startMinute: number; // 相对开播的计划分钟，0..1440
    durationMinutes: number; // 1..180
    note: string; // 最多 300 字符
  }[]; // 完整货盘，最多 500 个 SKU，同 SKU 不重复
}
```

同场次多份冲突正式货盘、缺失 SKU、跨店铺引用、重复 SKU、非法计划均不作为完整货盘。正式货盘缺失时，本场商品数 / GMV / 成交商品数 / 库存预警显示 `--`，不把店铺全量商品或本地草稿充当正式货盘。

未来货盘草稿提交、版本冲突、讲解计划保存、商品上下架 / 改价 / 改库存均需要后端提供权限、审批、幂等、来源版本和执行核验契约；本次没有创建通用写入或绕开现有 command-job 机制。

### 4. 商品专用分析

现有解析服务是否支持商品只读分析需确认；理想响应应提供只读建议、指标证据、范围、来源时间与建议生成时间。当前前端只显示 QUERY.text，并沿用当前解析接口要求的 task.create 权限；无权限时仍可查看本地规则建议。商品写操作不在现有 CommandAction 枚举中，因此本页不提供伪装成已提交审批的按钮。

## 修改文件

- `src/features/products-page.tsx`：工作区与交互。
- `src/styles/products.css`：局部样式与响应式布局，不改其他页面。
- `src/lib/products-workspace.ts`：商品口径、SKU 归因、草稿及排期校验。
- `src/services/products-adapter.ts`：正式快照适配及授权 / 完整性检查。
- `src/services/products-demo.ts`：独立演示夹具。
- `src/features/workspace-entry.tsx`：商品页入口与加载状态。
- `tests/products.test.ts`：范围隔离、缺失 / 零值、授权、归因、草稿及排期测试。
- 本交接文档和 `docs/evidence/products/` 验收截图 / 记录。

## 验证

测试与最终构建结果记录在 `docs/evidence/products/validation.json`。共享目录中的其他模块未提交修改不属于本次交付；发布从最新 main 独立整合，避免把它们一同提交。
