# 后端下一批任务领取检查点

2026-10-28，BE负责，按用户持续多Agent授权；当前台账与实际证据优先于本文。技术验收通过立即领取下一卡，不等待人工签字或不相关外部条件；正式门禁和真实平台许可继续单列。

| 任务 | 实际技术输入 | 下一实现范围 | 验证与约束 |
|---|---|---|---|
| M3-15a 私有素材上传计划 | M3-13a云文档CRUD及M0-04本地MinIO已技术验证 | 独立内容域上传计划与完成确认，绑定tenant/shop/product/doc、类型、大小、SHA256 | 使用官方S3 SDK及锁定依赖；实际MinIO验证签名、大小、MIME、摘要、过期和幂等；未确认对象不可引用 |
| M4-01b 分析计算与真实阶段 | M4-02生产事实覆盖与Worker复检已技术验证 | 确定性计算器、阶段持久化、并发fence、失败恢复与受控结果写入 | ROI/CTR/CPC由合计分子分母重算；缺订单时CPA空；当前期与可选比较期覆盖分开；不伪造直播/视频或利润；M4-03正式叙述另卡 |
| M3-08a targets切片 | 自定义人群切片与V9完整上下文/来源证明已有技术证据 | 固定SDK marketing_target_assets/get推广商品/对象选项 | 明确marketing_target_type与精确上下文依据；is_deleted不明确为false不可选；不从名字猜类型；其他选项类别保持未接入 |
| M1-18 完整快照 | M1-08～13只读链、M1-16/17技术输入已有证据，真实业务口径仍有缺口 | 复核各实际规范投影与十二集合契约，持久scope与完整范围 | 未确认单位/归属不能映射成GMV或退款；超规模明确拒绝；不把原生观察当规范业务事实 |

## 已完成的只读准备

M3-15a：当前无boto3/botocore/minio依赖，下一分支应引入官方boto3并更新uv.lock，避免手写SigV4。预签名PUT在有效期内可重复覆盖同key，HEAD确认后不能默认不可变；计划使用私有bucket版本管理，持久精确VersionId，完成时核对实际大小/MIME/SHA256，后续按版本读取。运行时不自行修改生产bucket；本地部署初始化与运行身份分离。非空assetId直到M3-15b实际资产校验前保持拒绝。上传者自报音视频时长不作剪辑边界依据。

M4-01b：冻结AnalysisResult包含schemaVersion/source/query/店铺平台/两时间/completeness/coverage/attribution/comparison/metrics/points/previousPoints/yearAgoPoints/sections/insights/insightSource/rules。非ready指标必须null+原因；金额用整数minor和Decimal。需新增受控阶段/结果写入函数，不直接给app_login开放analysis_results写或analysis_tasks任意UPDATE。计算阶段必须反映实际工作，结果与succeeded同事务；重投需fence/幂等。已有M4-02失败函数不能替代完整执行阶段。

M3-08a：固定SDK v3 marketing_target_assets/get必填marketing_target_type，支持account_id/filtering/page/page_size/organization_id/fields，list内marketing_asset_id/name/type/is_deleted/properties。仅保存必要已确认字段，未知properties不落库。不同kind不能沿用仅context唯一的来源索引互相覆盖，需要新迁移调整为tenant+kind+context。前端platformTemplates是平台定向包，不是创意模板，不能用creative_template/get替代。

## 资源与现场

用户确认两个独立项目属于yizhi租户、店铺不同；已有token服务和开放平台接口实现。实际批准scope、数据库店铺/账户映射、金额/库存单位及经营退款口径按资源清单继续核对。本次没有真实平台/付费调用，不将合成HTTP的真实PG测试当平台P验收。06/12两份既有他人未提交文档仍保留。

自动续接已更新为当前聊天的ACTIVE heartbeat（automation），五分钟仅用于中断/空闲恢复；运行中的卡之间直接连续推进。原每小时只读汇报设置未修改。
