"use client";
import { useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  ClockCounterClockwise,
  FilmStrip,
  LockKey,
  Plug,
  Robot,
  ShieldCheck,
  Sparkle,
} from "@phosphor-icons/react";
import { connectorNames, money, type FrontendSnapshot } from "@/lib/domain";
import { pageName, type PageId } from "@/lib/routes";
import { ServiceError } from "@/services/contracts";
import { useWorkspace } from "@/components/provider";
import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Panel,
} from "@/components/ui";
import { TaskComposer, TaskList } from "@/components/task-controls";
import { CoreMetrics } from "@/components/operating-context";
import { scopedPlans, scopedEvidence } from "@/lib/reporting";
import { ControlWorkspace } from "./control-workspace";

export { CoreMetrics } from "@/components/operating-context";
export function PlanTable({ snapshot }: { snapshot: FrontendSnapshot }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>广告计划</th>
            <th>独立连接器</th>
            <th>日预算</th>
            <th>出价</th>
            <th>ROI目标</th>
            <th>消耗</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {snapshot.plans.map((plan) => (
            <tr key={plan.id}>
              <td>
                <b>{plan.name}</b>
                <small>{plan.nativePlanId}</small>
              </td>
              <td>
                {
                  connectorNames[
                    snapshot.adAccounts.find(
                      (account) => account.id === plan.adAccountId,
                    )!.connectorId
                  ]
                }
              </td>
              <td>{money(plan.budgetMinor)}</td>
              <td>{money(plan.bidMinor)}</td>
              <td>{plan.targetRoi}</td>
              <td>{money(plan.spendMinor)}</td>
              <td>
                <Badge>{plan.enabled ? "演示开启" : "演示暂停"}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!snapshot.plans.length && <EmptyState title="暂无广告计划" />}
    </div>
  );
}
export function Goods({ snapshot }: { snapshot: FrontendSnapshot }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>商品 / SKU</th>
            <th>价格</th>
            <th>库存</th>
            <th>点击支付率</th>
            <th>讲解建议</th>
          </tr>
        </thead>
        <tbody>
          {snapshot.products.map((product) => (
            <tr key={product.id}>
              <td>
                <b>{product.name}</b>
                <small>{product.skuId}</small>
              </td>
              <td>{money(product.priceMinor)}</td>
              <td>{product.stock ?? "数据缺失"}</td>
              <td>
                {product.clicks && product.paidOrders !== null
                  ? `${((product.paidOrders / product.clicks) * 100).toFixed(1)}%`
                  : "样本不足"}
              </td>
              <td className="wrap-cell">{product.speechAdvice}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function Replay({ snapshot }: { snapshot: FrontendSnapshot }) {
  return (
    <div className="timeline">
      {snapshot.replaySegments.map((segment) => (
        <article key={segment.id}>
          <span>
            {new Date(segment.startSeconds * 1000).toISOString().slice(11, 19)}
          </span>
          <div>
            <h3>{segment.transcript}</h3>
            <p>{segment.advice}</p>
            <small>
              {segment.videoUrl ? "已关联视频" : "模拟文本 · 尚无获权录像"}
            </small>
          </div>
          <FilmStrip size={21} />
        </article>
      ))}
    </div>
  );
}
export function Review({ snapshot }: { snapshot: FrontendSnapshot }) {
  const report = snapshot.replayReports[0],
    [tab, setTab] = useState(0);
  if (!report) return <EmptyState title="暂无直播复盘" />;
  return (
    <>
      <div className="tabs" role="tablist" aria-label="直播复盘章节">
        {report.sections.map((section, i) => (
          <button
            key={section.title}
            role="tab"
            aria-selected={tab === i}
            onClick={() => setTab(i)}
          >
            {section.title}
          </button>
        ))}
      </div>
      <div className="review-content" role="tabpanel">
        <h3>{report.sections[tab].title}</h3>
        <p>{report.sections[tab].text}</p>
        {tab === 1 || tab === 3 ? (
          <Replay snapshot={snapshot} />
        ) : tab === 2 ? (
          <Goods snapshot={snapshot} />
        ) : (
          <div className="notice">
            引用证据：{report.sections[tab].evidenceIds.join("、")}。数据源{" "}
            {report.source}，尚未完成真实平台核验。
          </div>
        )}
      </div>
    </>
  );
}
const contentPages: Partial<
  Record<PageId, { subtitle: string; steps: string[]; note: string }>
> = {
  planning: {
    subtitle: "从商品到创意 Brief",
    steps: ["选择商品与人群", "填写渠道与内容目标", "关联脚本任务"],
    note: "创意生成接口由后端提供，本阶段保留任务入口与数据关系。",
  },
  scripts: {
    subtitle: "脚本与讲解版本",
    steps: ["创意 Brief", "脚本编辑与审核", "关联素材版本"],
    note: "脚本保存与模型生成待后续阶段，不以固定模板冒充 AI 结果。",
  },
  videos: {
    subtitle: "视频生产任务",
    steps: ["脚本与参考素材", "生产任务与进度", "成片与版本回收"],
    note: "NEXORA 视频能力将通过 API 对接，未迁移后端，也未提交生产任务。",
  },
  graphics: {
    subtitle: "商品图片与图文",
    steps: ["商品与素材", "生成需求", "审核与资产入库"],
    note: "图片生成与上传接口待后端契约确认。",
  },
  editing: {
    subtitle: "智能剪辑任务",
    steps: ["选择获权素材", "字幕与剪辑需求", "渲染与结果文件"],
    note: "保留 NEXORA 剪辑接入位置，本阶段不运行剪辑或渲染服务。",
  },
  experiments: {
    subtitle: "素材测试与观察",
    steps: ["明确实验假设", "统一预算与归因窗口", "观察样本与结果"],
    note: "缺少真实实验样本时，不生成素材胜负或效果结论。",
  },
  agents: {
    subtitle: "AI 岗位与任务来源",
    steps: ["内容运营", "自主投放", "经营复盘"],
    note: "自主投放与手机指挥使用相同 command_job 和审批定义；策略运行由后端负责。",
  },
};
function StatePreview() {
  const [state, setState] = useState("empty");
  return (
    <>
      <label className="inline-field">
        基础状态预览
        <select value={state} onChange={(e) => setState(e.target.value)}>
          <option value="empty">无数据</option>
          <option value="loading">加载</option>
          <option value="error">错误</option>
          <option value="forbidden">无权限</option>
        </select>
      </label>
      {state === "empty" ? (
        <EmptyState />
      ) : state === "loading" ? (
        <LoadingState />
      ) : (
        <ErrorState
          error={
            new ServiceError(
              state === "forbidden" ? "FORBIDDEN" : "NETWORK_ERROR",
              "前端状态组件演示；不代表后端实际响应。",
            )
          }
        />
      )}
    </>
  );
}
function FullReport({ snapshot }: { snapshot: FrontendSnapshot }) {
  const chapters = [
      "经营摘要",
      "广告平台表现",
      "商品与直播",
      "内容素材与实验",
      "AI决策与执行",
      "风险与异常",
      "成本退款与利润",
      "下一轮行动",
    ],
    [tab, setTab] = useState(0);
  return (
    <>
      <Panel
        title="经营投放完整复盘"
        detail="保留八类经营摘要，不虚构成本、退款与利润。"
      >
        <div className="tabs" role="tablist" aria-label="经营复盘章节">
          {chapters.map((chapter, i) => (
            <button
              key={chapter}
              role="tab"
              aria-selected={tab === i}
              onClick={() => setTab(i)}
            >
              {chapter}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="review-content">
          <h3>{chapters[tab]}</h3>
          <p>
            {tab === 6
              ? "商品成本、退款与履约数据未接入，无法计算真实利润。"
              : "G1-A 已建立章节与实体关系，完整数据和报告生成待后续 API 接入。"}
          </p>
        </div>
      </Panel>
      <Panel title="直播专场深度复盘" detail="回放、货盘、话术与投流联合分析">
        <Review snapshot={snapshot} />
      </Panel>
    </>
  );
}
export function BusinessPage({
  pageId,
  snapshot,
}: {
  pageId: PageId;
  snapshot: FrontendSnapshot;
}) {
  const { service } = useWorkspace();
  if (["overview", "goals", "monitor", "tasks"].includes(pageId))
    return (
      <ControlWorkspace
        pageId={pageId}
        snapshot={snapshot}
        plans={
          <PlanTable snapshot={{ ...snapshot, plans: scopedPlans(snapshot) }} />
        }
        liveEvidence={
          <>
            <Panel title="回放时间轴与主播话术">
              <Replay snapshot={scopedEvidence(snapshot)} />
            </Panel>
            <Panel title="直播货盘诊断">
              <Goods snapshot={scopedEvidence(snapshot)} />
            </Panel>
          </>
        }
      />
    );
  if (pageId === "decisions")
    return (
      <div className="two-columns">
        <Panel
          title="决策候选与依据"
          detail="PC、手机与 AUTOPILOT 共用同一任务列表"
        >
          <TaskList snapshot={snapshot} />
        </Panel>
        <Panel
          title="创建操作申请"
          detail="结构化确认对象，本阶段不解析或运行 AI 策略"
        >
          <TaskComposer snapshot={snapshot} origin="PC" />
        </Panel>
      </div>
    );
  if (pageId === "plans")
    return (
      <>
        <Panel title="广告计划列表" detail="所有金额为演示值；不连接广告写接口">
          <PlanTable snapshot={snapshot} />
        </Panel>
        <Panel title="计划操作申请">
          <TaskComposer snapshot={snapshot} origin="PC" />
        </Panel>
      </>
    );
  if (pageId === "products")
    return (
      <>
        <Panel title="商品与货盘诊断">
          <Goods snapshot={snapshot} />
        </Panel>
        <Panel title="讲品片段与话术建议">
          <Replay snapshot={snapshot} />
        </Panel>
      </>
    );
  if (pageId === "reports") return <FullReport snapshot={snapshot} />;
  if (pageId === "connections")
    return (
      <>
        <div className="notice spaced">
          <LockKey size={17} />8 类连接器独立授权。Mock 卡片不代表真实连接，当前
          Scope 均未核验。
        </div>
        <div className="connector-grid">
          {snapshot.connectors.map((connector) => (
            <Panel
              key={connector.id}
              title={connectorNames[connector.id]}
              action={<Plug size={20} />}
            >
              <code>{connector.id}</code>
              <p>
                授权范围：
                {connector.scopes.length
                  ? connector.scopes.join("、")
                  : "尚未核验"}
              </p>
              <Badge tone="warning">
                {connector.status === "UNKNOWN" ? "待核验" : connector.status}
              </Badge>
            </Panel>
          ))}
        </div>
      </>
    );
  if (pageId === "assets")
    return (
      <Panel title="素材资产与版本">
        <div className="asset-grid">
          {snapshot.materials.map((material) => (
            <article key={material.id} className="asset-item">
              <div className="asset-symbol">
                <FilmStrip size={30} />
              </div>
              <Badge>{material.kind}</Badge>
              <h3>{material.name}</h3>
              <p>
                {material.version} · {material.status} · 关联商品{" "}
                {material.productId}
              </p>
            </article>
          ))}
        </div>
      </Panel>
    );
  if (pageId === "executions")
    return (
      <>
        <Panel
          title="执行回执"
          detail="审批通过、平台受理、回读核验和效果观察各自独立"
        >
          {snapshot.tasks.some((job) => job.execution_receipt) ? (
            snapshot.tasks
              .flatMap((job) =>
                job.execution_receipt ? [job.execution_receipt] : [],
              )
              .map((receipt) => (
                <div key={receipt.receipt_id} className="list-row">
                  <Badge>{receipt.source}</Badge>
                  <span>
                    {receipt.accepted === null
                      ? "未知"
                      : receipt.accepted
                        ? "已受理"
                        : "未受理"}{" "}
                    / {receipt.verification}
                  </span>
                  <code>{receipt.platform_receipt_id ?? "无平台回执"}</code>
                </div>
              ))
          ) : (
            <EmptyState
              title="尚无平台执行回执"
              description="本阶段没有执行引擎，也不会把 Mock 审批当作平台成功。"
            />
          )}
        </Panel>
        <Panel title="共享任务流水">
          <TaskList snapshot={snapshot} />
        </Panel>
      </>
    );
  if (pageId === "budgets")
    return (
      <div className="two-columns">
        <Panel title="经营目标与预算边界">
          <div className="facts">
            <div>
              <span>每日预算硬上限（演示）</span>
              <b>{money(snapshot.riskPolicies[0]?.dailyBudgetMinor ?? null)}</b>
            </div>
            <div>
              <span>运行模式</span>
              <b>{snapshot.riskPolicies[0]?.mode}</b>
            </div>
            <div>
              <span>当前数据源</span>
              <Badge>{snapshot.source}</Badge>
            </div>
          </div>
          <div className="notice">
            预算分配、计划预算、出价与 ROI
            目标互不替代。真正的硬上限由后端校验。
          </div>
        </Panel>
        <Panel title="预算相关申请">
          <TaskComposer snapshot={snapshot} origin="PC" />
        </Panel>
      </div>
    );
  if (pageId === "analytics")
    return (
      <>
        <CoreMetrics snapshot={snapshot} />
        <Panel title="数据来源与归因口径">
          <div className="facts">
            {snapshot.plans.map((plan) => (
              <div key={plan.id}>
                <b>{plan.name}</b>
                <span>{plan.attributionWindow}</span>
                <small>{plan.sourceUpdatedAt}</small>
              </div>
            ))}
          </div>
          <div className="notice">
            广告归因不等于经营利润。缺失成本、退款或订单时不推算利润。
          </div>
        </Panel>
      </>
    );
  if (pageId === "settings")
    return (
      <div className="two-columns">
        <Panel title="企业与权限边界" action={<ShieldCheck size={22} />}>
          <div className="facts">
            <div>
              <span>企业</span>
              <b>{snapshot.tenants[0]?.name}</b>
            </div>
            <div>
              <span>身份适配器</span>
              <Badge>{service?.mode}</Badge>
            </div>
            <div>
              <span>正式鉴权</span>
              <b>待独立后端提供</b>
            </div>
          </div>
          <p>
            Mock
            存储只在同源、同浏览器配置中共享，不能实现真实跨设备同步。正式权限必须由服务端校验。
          </p>
        </Panel>
        <Panel title="页面基础状态">
          <StatePreview />
        </Panel>
      </div>
    );
  const content = contentPages[pageId];
  return (
    <div className="two-columns">
      <Panel
        title={content?.subtitle ?? pageName(pageId)}
        action={
          pageId === "agents" ? <Robot size={22} /> : <Sparkle size={22} />
        }
      >
        <div className="step-list">
          {content?.steps.map((step, i) => (
            <div key={step}>
              <span>0{i + 1}</span>
              <b>{step}</b>
              <Badge>已建页面骨架</Badge>
            </div>
          ))}
        </div>
        <div className="notice">{content?.note}</div>
        <Link href="/app/assets" className="button secondary">
          查看关联素材 <ArrowRight size={15} />
        </Link>
      </Panel>
      <Panel
        title="本阶段交付边界"
        action={<ClockCounterClockwise size={22} />}
      >
        <EmptyState
          title="业务接口待接入"
          description="本页已接统一 Service 与共享实体，完整业务功能将在后续批准的阶段开发。"
        />
        <div className="row">
          <Badge tone="blue">{snapshot.source}</Badge>
          <Badge>契约 command-job-v1.0</Badge>
        </div>
      </Panel>
    </div>
  );
}
