# M1-09 ADQ资金与操作记录只读

负责人BE，协作F07；WAITING_REVIEW。基线662a5fc84cc3bbd8d63cb4c15d117b08aef5b45b，实现5da50660be2a72db50eecafd160290e4ae3be34f；M1-08/M1-15b已有实际本地技术证据，按持续授权继续工程实现，G0/E08与E01真实平台P验收保留。复用M1-02/03b/04/16/17的实际本地技术输入。

范围：固定ADQ资金/实时消耗与操作日志读取、逐页档案与镜像、钱包口径和单位依据；不混入微信小店结算或微信豆，不开放转账或广告写操作。三条DoD：四口径按实际来源分别映射且余额不作为可用预算；分页/单位转换可核对并有requestId关联；其他钱包产品线隔离且缺值不补0。

已核对固定官方SDK：v1.3 funds/get只接account_id/fields，无分页参数，返回data.list；已存在原生适配复用了分页Schema和total_page解析，须先修正这个技术依赖。v3 operation_log_list/get有account_id、operation_object_type、日期和数字分页；对象类型保持ADGROUP等原生层级，不能借用M1-08旧版CAMPAIGN的ID。日志SDK没有原生日志ID，不把重拉的观察行当作稳定平台事件ID。

SDK只确认balance/realtime_cost为int64及资金类型，不证明金额单位、在途/未结算/授信口径。将保留精确原生值和UNCONFIRMED来源状态；只有独立有效的单位/口径依据才允许相应归一化，未提供的口径返回缺失原因，不能拼金额或默认为0。真实账户、店铺、scope与只读许可仍按既有资源清单对接，不重复申请既有研发授权。

## 实现

0046保存逐页资金/日志来源、资金观察、操作观察和独立口径依据。所有页面沿用当前应用×授权版本×scope×原生账户快照×店铺/账户权限×六阶段×固定路径、Redis准入、PG档案、租约游标和页事务；实际SDK单页funds/get不发送page/page_size/pagination_mode，不要求不存在的total_page。资金类型只作原生代码保存，未知/负余额保留精确int64字符串，不把原生代码名称当财务分类依据。

四类钱包口径和实时消耗分别有独立来源状态：默认值null、UNCONFIRMED。维护身份可录入当前具体来源、资金类型集合、原生字段、CNY单位、合并规则、资料指纹和有效期的依据。视图按最新依据逐项核对，过期/撤销/列与载荷不一致不回退旧依据；必须有实际完整批次和已配置的新鲜度策略，缺资金类型/字段不补0，金额超过JS安全整数不归一化。明确为整数元的独立依据才乘100，明确为分才保持原值；来源中的raw单位状态不被改写。`budgetUsable=false`始终保留；此视图不是预算账本输入。

操作日志请求只使用SDK已列对象层级、明确日期窗口和数字分页。1至31天是本地单批边界，不宣称为平台最大窗口。原生对象ID、动作、平台代码、原生创建时间分别保存；时间单位未知，不猜epoch/timezone。SDK没有日志ID，以独立内部observation_id保存每行，两个字段完全相同的日志观察也不丢弃；不生成native_log_id，不把每次拉取当稳定平台事件。联系人、操作人、对象名称和自由文本operation_log/operation_info_list不请求、不进业务观察或档案。返回记录可由实际request/outbound/run/page关联核对。

同一页的确认游标、金融页、原生镜像和观察同事务。失败不删旧资金观察，日志明确PARTIAL并按原run恢复；重复已完成run不重复HTTP。当前只装配PlatformRuntime受控服务端入口和内部只读视图；公共资源HTTP/FrontendSnapshot/定时调度按各自任务接线，未创建替代申请或资金写入口。空scope仍可完成原有授权流程，镜像/资金读取器保持关闭。

## 验证与交接

已通过：Ruff 292文件、mypy 237源码、670单元/契约；16项实际PG/Redis金融专项、12项资产回归和8项原生授权交接。最终全量733项通过、1项生命周期默认跳过，473.27秒；见verification.json。保留开发库迁移0046、30类相关记录0，迁移角色NOLOGIN恢复已记录development.json；全量结束后已复核上述恢复状态。

专项覆盖单页/完整日志分页、精确int64与负值、未知和缺失口径、独立单位依据及过期/撤销、无新鲜度策略、超安全整数、明确整数元转分、实际0与缺值区分、相同日志保留独立观察、PII剔除、失败续跑、畸形/串账户资金页保留旧数据、投影后故障整页回滚、依据ID不可覆盖、运行角色不可自授/改写观察、跨产品/租户/店铺及撤权。wallet.json保留实际本地request/outbound/run与依据前后投影；其中身份、上游HTTP和财务口径依据全部合成，不能当作真实金融口径证明。

来源见source-spec.json；[官方SDK资金接口](https://github.com/TencentAd/marketing-api-go-sdk/blob/81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9/pkg/api/api_funds.go)和[操作日志接口](https://github.com/TencentAd/marketing-api-go-sdk/blob/81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9/pkg/api/v3/api_operation_log_list.go)只确认固定协议，不等于真实账户可用。实际币种/单位、财务口径分类、其他口径接口若未开放，以及原生日志时间单位，仍按资源清单核对。未提供的口径保持未知；若实际需要不同接口，须先实现核验该接口，不能仅靠改标签冒充新增数据源。

回滚采用前向停用来源/撤销口径依据，保留档案和观察，不执行破坏性downgrade。原06/12未提交文档各2行不进入本卡提交。BE/F07具名复核PENDING；真实平台/付费调用0，E01/G0/E08保留。

