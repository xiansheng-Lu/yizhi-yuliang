import { connectorNames, type FrontendSnapshot } from "../lib/domain";
import { percentChange, shiftDate } from "../lib/reporting-date";
import { analysisMetricDefinitions, defaultAnalysisRules, diagnoseAnalysis, dimensionNames, pointValue, previousRange, rangeDays, sumPoints, type AnalysisDimension, type AnalysisPoint, type AnalysisQuery, type AnalysisResult, type AnalysisRow, type AnalysisSection, type MetricStatus } from "../lib/business-analysis";

export type AnalysisScenario = "normal" | "partial" | "empty" | "failure";
export const scenarioNames: Record<AnalysisScenario, string> = { normal: "完整样本", partial: "部分数据", empty: "无数据", failure: "执行失败" };
// Checked-in deterministic fixtures: daily counts and CNY integer minor amounts.
// Fixed coverage 2026-09-01..2026-10-21. No Math.random, no shifting fixtures to the query.
const sales = [1800000, 1920000, 1740000, 2050000, 2200000, 2300000, 2100000, 2400000, 2250000, 2600000, 2450000, 2700000, 2800000, 2650000, 2500000, 2400000, 2250000, 2100000, 1900000, 1750000, 1600000];
const spend = [180000, 190000, 170000, 200000, 210000, 220000, 200000, 230000, 220000, 240000, 230000, 250000, 255000, 240000, 250000, 270000, 290000, 310000, 450000, 470000, 480000];
const views = [8000, 8500, 7800, 9000, 9200, 9400, 8700, 9500, 10000, 10500, 9800, 11000, 10800, 11200, 14000, 14500, 15000, 15500, 16000, 17000, 18000];
const septemberSales = [1500000,1600000,1550000,1700000,1800000,1750000,1650000,1900000,1850000,1950000,2000000,2100000,2050000,2150000,2200000,2250000,2100000,2300000,2350000,2400000,2250000,2450000,2500000,2400000,2600000,2550000,2650000,2700000,2500000,2750000];
const platformFactors = { all: 100, wxshop: 100, adq: 72, wxpromo: 64, beans: 38, qianchuan: 82, douyinshop: 90, juguang: 56, redshop: 60 };
const topics: Record<AnalysisDimension, string[]> = {
  shop: ["店铺成交表现", "商品结构分析", "客单价与转化分析", "退款与售后表现", "商品增长机会"],
  live: ["直播场次对比", "流量获取分析", "观看至成交转化漏斗", "直播成交贡献", "低效直播场次识别"],
  ads: ["广告消耗结构", "广告归因成交趋势", "ROI表现", "广告计划效果排序", "高消耗低产出计划", "预算优化机会"],
  video: ["内容播放趋势", "内容引流效率", "商品点击转化", "带货视频排名", "高潜力视频识别"],
};
function allFixturePoints(query: AnalysisQuery, scenario: AnalysisScenario): AnalysisPoint[] {
  const factor = platformFactors[query.platformId] / 100 * (query.shopId === "shop-mountain" ? .75 : 1);
  const records = [
    ...septemberSales.map((gmv, index) => ({ date: shiftDate("2026-09-01", index), gmv, index, adSpend: Math.round(gmv / 10), videoViews: 7500 + index * 100 })),
    ...sales.map((gmv, index) => ({ date: shiftDate("2026-10-01", index), gmv, index, adSpend: spend[index], videoViews: views[index] })),
  ];
  return records.map(({ date, gmv, index, adSpend, videoViews }) => {
    const scale = (n: number) => Math.round(n * factor);
    const values: Record<string, number | null> = {
      "shop.gmv": scale(gmv), "shop.orders": scale(90 + index * 2), "shop.refund": scale(50000 + index * 2200),
      "live.sessions": 1, "live.hours": 3 + index % 3, "live.viewers": scale(6000 + index * 120), "live.buyers": scale(index < 14 ? 100 : 60), "live.gmv": scale(gmv * .6),
      "ads.spend": scale(adSpend), "ads.gmv": scale(gmv * .7), "ads.clicks": scale(1900 + index * 25), "ads.impressions": scale(100000 + index * 2000), "ads.orders": scale(70 + index),
      "video.published": index % 3 === 0 ? 2 : 1, "video.views": scale(videoViews), "video.clicks": scale(400 + index * 8), "video.orders": scale(index < 14 ? 32 : 17), "video.gmv": scale(gmv * (index < 14 ? .25 : .18)),
    };
    Object.keys(values).forEach(key => {
      if (!query.dimensions.includes(key.split(".")[0] as AnalysisDimension)) delete values[key];
      else if (scenario === "partial" && (key === "video.gmv" || key === "shop.refund" || key === "ads.impressions")) values[key] = null;
      else if (query.platformId === "beans" && key.startsWith("video.")) values[key] = null;
    });
    return { date, values };
  });
}
function entityRows(dimension: AnalysisDimension, points: AnalysisPoint[], snapshot: FrontendSnapshot, query: AnalysisQuery): AnalysisRow[] {
  if (!points.length) return [];
  const totals = sumPoints(points);
  const products = snapshot.products.filter(product => product.shopId === query.shopId);
  const plans = snapshot.plans.filter(plan => plan.shopId === query.shopId && (query.platformId === "all" || snapshot.adAccounts.some(account => account.id === plan.adAccountId && account.connectorId === query.platformId)));
  const names = dimension === "shop" ? [products[0]?.name ?? "秋茶礼盒", products[1]?.name ?? "节日组合", "日常口粮茶"] : dimension === "live" ? ["午间品茶专场", "晚间秋茶专场", "周末礼盒专场"] : dimension === "ads" ? [plans[0]?.name ?? "秋茶转化计划", "礼盒扩量计划", "新品探索计划"] : ["秋茶冲泡指南", "礼盒开箱实测", "办公室的一杯茶"];
  const weights = dimension === "ads" ? [.55, .3, .15] : [.5, .3, .2];
  return names.map((name, index) => ({
    id: `fixture-${dimension}-${index}`, name, date: points.at(-1)!.date,
    relatedProduct: products[index % Math.max(1, products.length)]?.id ?? `fixture-product-${index}`,
    relatedLive: dimension === "ads" || dimension === "live" ? `fixture-live-${index}` : undefined,
    values: Object.fromEntries(Object.entries(totals).filter(([key]) => key.startsWith(`${dimension}.`)).map(([key, value]) => {
      if (value === null) return [key, null];
      // Integer partition sums exactly; the first ad plan intentionally has low ROI.
      const parts = [Math.floor(value * weights[0]), Math.floor(value * weights[1])];
      const partition = index < 2 ? parts[index] : value - parts[0] - parts[1];
      if (dimension === "ads" && key === "ads.gmv") {
        const adSpend = totals["ads.spend"];
        if (adSpend === null || adSpend === undefined) return [key, null];
        const first = Math.min(value, Math.floor(adSpend * .55) * 2);
        return [key, index === 0 ? first : index === 1 ? Math.floor((value - first) * .7) : value - first - Math.floor((value - first) * .7)];
      }
      return [key, key === "live.hours" ? Math.round(value * weights[index] * 10) / 10 : partition];
    })),
  }));
}
export function buildMockAnalysis(snapshot: FrontendSnapshot, query: AnalysisQuery, id: string, taskId: string, scenario: AnalysisScenario): AnalysisResult {
  const fixture = scenario === "empty" || !["shop-tea", "shop-mountain"].includes(query.shopId) ? [] : allFixturePoints(query, scenario);
  const points = fixture.filter(point => point.date >= query.startDate && point.date <= query.endDate);
  const prior = previousRange(query);
  const previousPoints = fixture.filter(point => point.date >= prior.startDate && point.date <= prior.endDate);
  const completeDates = points.length === rangeDays(query);
  const compare = completeDates && previousPoints.length === rangeDays(query);
  const totals = sumPoints(points), previous = compare ? sumPoints(previousPoints) : {};
  const metrics = analysisMetricDefinitions.filter(metric => query.dimensions.includes(metric.dimension)).map(definition => {
    let status: MetricStatus = query.platformId === "beans" && definition.dimension === "video" ? "unsupported" : "ready";
    const value = completeDates ? pointValue(totals, definition) : null;
    if (status === "ready" && value === null) status = scenario === "partial" && points.length ? "not_integrated" : "empty";
    return { ...definition, value: status === "ready" ? value : null, previous: compare ? pointValue(previous, definition) : null, yearAgo: null, status,
      reason: status === "unsupported" ? "演示能力矩阵：微信豆样本不支持短视频带货统计" : status === "not_integrated" ? "该字段未包含在部分数据测试样本中" : status === "empty" ? "固定样本未覆盖完整所选周期，无法给出全周期汇总" : undefined };
  });
  const sections: AnalysisSection[] = query.dimensions.map(dimension => {
    const rows = entityRows(dimension, points, snapshot, query);
    const key = dimension === "ads" ? "ads.spend" : `${dimension}.gmv`;
    const metric = metrics.find(metric => metric.id === key);
    const change = metric ? percentChange(metric.value, metric.previous) : null;
    const format = (value: number) => (value / 100).toLocaleString("zh-CN", { style: "currency", currency: "CNY" });
    return { id: `section-${dimension}`, dimension, title: dimensionNames[dimension], topics: topics[dimension],
      metricIds: metrics.filter(metric => metric.dimension === dimension).map(metric => metric.id), rows,
      conclusion: metric?.value === null || metric?.value === undefined ? "该维度缺少完整数据，暂不判断增长或下降。" : `本期${metric.name}${format(metric.value)}${change === null ? "，缺少完整对比周期，不判断增长或下降" : `，较上一等长周期${change >= 0 ? "上升" : "下降"}${Math.abs(change * 100).toFixed(1)}%`}。`,
      advice: metric?.status === "ready" && rows.length ? `依据${rows.length}个业务对象的同周期明细，优先核对${rows[0].name}的${dimension === "ads" ? "ROI与预算效率" : "成交贡献与承接效率"}；进行单项测试，使用下一等长周期验证。` : "补齐所选周期数据后重新执行分析，不根据缺失指标生成优化操作。",
      limitation: dimension === "live" ? "流量来源结构未接入；观看至成交漏斗仅有观看人次、支付人数两级，不补造中间环节。" : dimension === "shop" ? "店铺访客UV未接入，无法计算访客成交转化率；退款与支付按发生周期统计。" : dimension === "video" ? "内容与商品关联为固定演示关系；视频GMV可能包含付费归因成交。" : "演示归因口径：当日支付归因；不包含利润和自然成交推算。" };
  });
  for (let a = 0; a < query.dimensions.length; a++) for (let b = a + 1; b < query.dimensions.length; b++) {
    const left = query.dimensions[a], right = query.dimensions[b];
    const pair = new Set([left, right]);
    const title = pair.has("shop") && pair.has("ads") ? "广告消耗与店铺成交" : pair.has("live") && pair.has("ads") ? "直播与投放效率" : pair.has("video") && pair.has("shop") ? "短视频与商品成交" : `${dimensionNames[left].replace("分析", "")} × ${dimensionNames[right].replace("分析", "")}`;
    sections.push({ id: `cross-${left}-${right}`, dimension: "cross", title,
      topics: ["同周期趋势对照", "关联业务明细", "口径与证据边界"],
      metricIds: [left === "ads" ? "ads.spend" : `${left}.gmv`, right === "ads" ? "ads.spend" : `${right}.gmv`],
      rows: [], conclusion: points.length ? "展示同一店铺、同一周期的趋势对照。时间同步变化只能作为相关性线索。" : "缺少同周期数据，无法开展关联分析。",
      advice: "进入专项明细核对商品或场次；需后端提供订单、内容、场次与广告的可验证关联后，才可计算归因贡献。",
      limitation: "跨业务订单去重、直播期间消耗和付费/自然流量拆分未接入；不合计跨业务GMV，不声明因果，不用整日广告消耗冒充直播期间消耗。" });
  }
  if (query.dimensions.length === 4) sections.push({ id: "cross-overview", dimension: "cross", title: "经营结果与四业务总览", topics: ["经营异常汇总", "关键增长机会", "流量结构"], metricIds: ["shop.gmv", "live.gmv", "ads.gmv", "video.gmv"], rows: [], conclusion: "四类成交指标分别呈现，可能存在订单重叠，禁止直接相加。", advice: "结合下方诊断验证高效商品、场次与计划；付费和自然流量结构待同口径数据接入。", limitation: "未取得订单级去重与自然流量来源，无法生成无重复的业务成交占比。" });
  const result: AnalysisResult = {
    schemaVersion: "business-analysis-v1", id, taskId, source: "MOCK", query: structuredClone(query),
    shopName: snapshot.shops.find(shop => shop.id === query.shopId)?.name ?? "演示店铺",
    platformName: query.platformId === "all" ? "全部关联平台" : connectorNames[query.platformId],
    createdAt: new Date().toISOString(), updatedAt: points.length ? `${points.at(-1)!.date}T23:00:00+08:00` : null,
    completeness: !points.length ? "empty" : completeDates && metrics.every(metric => metric.status === "ready") ? "complete" : "partial",
    coverage: `固定测试样本：2026-09-01 至 2026-10-21；本期覆盖 ${points.length}/${rangeDays(query)} 天。${!completeDates ? "汇总缺失；趋势仅展示实际覆盖日期。" : "仅用于演示，不代表平台真实业务。"}`,
    attribution: "演示：店铺按订单支付去重；广告为当日支付归因；直播与视频为渠道归因。跨业务可能重叠，禁止求和或推算自然成交。",
    comparison: { previous: compare ? prior : null, yearAgo: null },
    metrics, points, previousPoints: compare ? previousPoints : [], yearAgoPoints: [], sections, insights: [], insightSource: "rules", rules: { ...defaultAnalysisRules },
  };
  result.insights = diagnoseAnalysis(result, result.rules);
  return result;
}
