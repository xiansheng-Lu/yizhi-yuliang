# M3-17a SOP模板与创作线

2026-10-28，BE/F10；技术实现已验证，WAITING_REVIEW。基线main70ab960，本地codex/backend-workflows-import-overview。范围仅模板版本、DAG、Run/Step初始化与实际SOP根文档事务，不包含M3-17b执行/采纳/重做。未调用真实平台或付费能力。

## 实现与边界

0064_workflow_templates_runs接0063_content_derivations，真实临时库已成功升级到包含0065的head。workflow_templates以tenant0/is_system CHECK、owner_tenant_id生成NULL/正租户FK实现系统行，无虚构tenant0。合法正租户能读系统模板，app_login无UPDATE/DELETE授权且模板数据库触发器全角色禁止修改/删除；租户复制和后续修改均INSERT新版本。run.template_tenant_id只允许0/本租户，模板ID/真实版本有复合FK，更新模板不改变已有run。

模板定义100步上限、依赖/输入数量与字符串界限，迭代拓扑排序拒绝重名/不存在依赖/重复依赖/自环/任意子图环。模型构造绕过不能绕过服务重新校验。首次只有无依赖步骤READY，其他PENDING。五条系统目录对应16册策划脚本、图文、视频、智能剪辑、素材审核；前3链按16册具体步骤，后2配置source/edit/review、source/review作为人工初始化标签，不代表音视频探测或执行能力已开放。产品线目录初始为空，未猜测投放产品线值。

创作线只接受commerce_products.id（V9 products稳定内部ID），不能混用旧canonical products.id。服务端验证当前租户/成员/店铺/账户scope及商品本地未移除，不要求平台在售；不根据未知平台状态新设门禁。Cookie/CSRF/X-Shop-Id沿用真实云稿入口。创建键在租户内唯一并由事务级advisory锁保护，请求摘要包含店铺/商品/模板/标题；同键同请求返回同run，同键异请求409。

同一business_transaction内创建planning根文档、run、steps、document.workflow_run_id及既有RUN引用，任一步失败全部回滚。根文档调用validate_content(...purpose='SOP')后调用既有ContentService._save重新校验并保留实际scope/资产守卫。根文档不带素材，因此没有伪造版本证明。文档与run双向FK最后ALTER补齐租户/店铺/商品复合范围；在途RUN引用阻止删除和商品迁移。后续步骤派生必须调用FlowService的kind/version/稳定run-step键，不可用随机内部兼容key。

## 验证

- 本卡8单元通过，0.26秒；缺依赖、自环、断开子图环、重复依赖/步骤、100节点链、越界与UUID格式。
- 本卡真实PG/HTTP6项通过，7.96秒：系统模板只读及复制/追加版本、初始READY/PENDING、版本锁定、历史引用、并发创建键、异payload冲突、事务失败回滚、跨租户/店铺/未知/移除商品、真实main路由Cookie/CSRF、实际purpose=SOP调用、SQL伪系统模板/假版本拒绝。
- 必要回归content-documents5通过7.23秒，content-validation5通过7.66秒。
- Ruff通过；3源码+2测试mypy通过。数据库窗口完成后已明确释放。
- 首跑5通过1失败：负例测试错误地用app_login维护商品deleted_at，被权限正确拒绝。改为既有maintenance fixture SET LOCAL ROLE app_owner构造移除事实，正式6项全过；没有放宽生产权限。

真实装配为main include workflows_routes.router，未在测试手工注册。DB-I04仍需BE具名复核系统/租户模板选用策略；F10真实浏览器接线未验收。8单元/6集成技术结果不是M3-17b执行能力或人工最终验收。M3-16真实SOP“初始化”入口现已接入，其后续步骤推进及GENERATION入口仍需各卡补齐，不能据此将M3-16整卡改只待人工。

## 后续

M3-17b依赖本卡已可本地实现：执行/候选/人工采纳/可选跳过/依赖闭包重做，需逐步接实际供应商与成本门禁，不能用本次READY状态冒充执行成功。当前批次主Agent统一复核、全量、合并与推送；子任务未执行Git。

## 交叉审查后目录完整性修复

模板与创作线继续保留现有数组响应，不引入未约定分页。模板目录探测读取201条：可见总量<=200时完整返回（含最新版本），>200返回INVALID_INPUT/HTTP422，说明当前接口不支持分页并拒绝截断。当前店铺创作线探测101条：<=100完整返回，>100同等明确422；计数针对当前租户RLS及当前店铺范围。没有放宽SQL权限或修改0064/65。

2026-10-28修后专项：`python scripts/test_infrastructure.py --group content-workflows --details` **8 PASS/8.56秒**（原6+真实边界2）。维护角色批量插入合法不可变模板版本及独立root/document history/run/step/RUN引用，验证模板恰好200含最新版本、追加到201后service与真实HTTP422；run恰好100完整、创建101后service与HTTP422。没有以Mock计数替代数据库事实。

同轮 `pytest -q tests/unit/test_content_workflows.py` **8 PASS/0.23秒**；修复2文件Ruff及mypy通过。原content-documents/validation回归仍为上文历史结果，本轮无相关失败，按授权未追加DB回归。本轮workflows修复DB窗口已向root与reports明确释放，源码已冻结。
