# M1-18 计划规范投影切片（进行中）

本切片不修改 frozen frontend/snapshot/V8 workspace，不将 ADGROUP 改名为 CAMPAIGN 层级。

固定来源：TencentAd/marketing-api-go-sdk revision `81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9`，
`pkg/model/model_campaigns_get_list_struct.go` 与 `pkg/model/model_adgroups_get_list_struct.go`。
原项目 `D:/codex/tencent-adq/examples/account_overview.py` 的只读调用佐证请求历史；其 advertiser.daily_budget 不是计划预算来源。
SDK模型只证明字段/类型；没有金额单位、ROI归因倍率、源时间epoch单位或时区的逐字段证明。

实现：固定 `/v1.3/campaigns/get` / `/v1.3/adgroups/get` 全量有界数字分页；保留 int64 文本及精确 Decimal 原始ROI；
只归档固定字段；10000以上显式拒绝；跨页重复、total漂移、平台失败拒绝完整结果。页观察与sync_pages原子提交。
读取每次复核当前应用、授权、账户原生关联、scope与六阶段能力，且只读最新完整SUCCESS run；授权/绑定版本不匹配拒绝。
独立维护PlanMeasureProof逐字段出处、哈希、有效期、版本、币种CNY、预算和出价单位、ROI归因倍率、epoch和UTC、状态映射。
证明不存在/过期/字段缺失、金额超JS安全整数、删除状态未知均保留coverage gaps，不填0。真实0按有效依据保留。

限制：campaign官方模型没有bid_amount，而V8 Campaign.bidMinor必填，因此即使预算等有依据也不能给完整campaign投影。
ADGROUP有bid_amount，本批主Agent已补独立ADQ_ADGROUPS_V1_3_READ source profile（官方API源码sha256 `14c4726671b9d3ecb85c75553d8ad0723325cd1b7e6089bc1ff0500698061765`）；这只确认正式只读路径，仍不能替代实际授权、字段口径或业务验收。
正式平台单位依据、独立实际维护proof、驭量runtime业务映射及G1/E01人工验收均未完成。
合成端口/合成proof验证只证明技术拒绝与转换行为；不证明真实平台口径。

当前16项离线unit通过，Ruff通过；10项真实PG/Redis+合成HTTP集成通过（9.08秒）；5项原只读路径必要回归通过（5.92秒）。首次专项9过1败是测试维护连接的source查询未限定当前account，修复测试后全量10过。数据库窗口已明确释放。

该slice只交完整观察和规范量值，不创建canonical AdPlan实体、不把native ID冒充内部planId，也未接共享snapshot/V8。主Agent另行负责runtime装配。
真实平台新增调用0；业务写0；原项目token服务/配置未修改。
