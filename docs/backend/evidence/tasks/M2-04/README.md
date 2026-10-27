# M2-04 指令安全集与真实通道回归

2026-10-27（Asia/Shanghai）；BE负责，FE协作F06；状态WAITING_REVIEW。基线a604789eef8f8548b12411a1eb5a7b98fcdd8193，M2-03实现84cf284。用户已确认28条解析预期并授权开发需要时直接调用DeepSeek，不代填BE/FE最终复核，也不构成真实广告操作授权。

结论：后端解析组合链技术通过。首轮28次真实请求，模型原始候选25/28匹配，CP-004/008/026未匹配；当时严格守卫拒绝了这3条，没有创建任务或平台写入。模型把CP-004/026候选扩大为A+B，CP-008省略当前场次；已保留[首轮完整失败证据](initial-live-verification.json)，没有改预期或删除失败项。

修复确定性消歧：动作、值和类型必须一致；规则依据原话与授权目录得到的候选集合必须包含于模型返回的授权集合，才允许由服务端收窄明确对象。同名/泛指的真正歧义不能让模型删成单一对象；模型漏掉必需对象、越权、改金额或换动作均拒绝。QUERY可以用服务端唯一当前场次补全模型null，明确错误对象与替歧义选一个仍拒绝。模型自由文本不作为已执行或经营指标事实。

修复后[首轮28条实际响应离线回放](replay-verification.json)匹配28/28，此步骤没有新增调用；另对CP-004/008/026进行[3次真实复验](recheck-live-verification.json)，组合链匹配3/3，模型原始匹配1/3。最终结论针对服务端解析组合链，不能宣称DeepSeek裸输出准确率100%。未改变v1提示词、样例或已确认预期。

三条DoD：1. 固定安全样例中的混合动作、空对象、元/分/豆混用、注入与越权均返回拒绝/澄清；2. 合法动作/值/确定性对象消歧与用户确认预期匹配，原始模型差异及修后规则可复演；3. 真实PG验证每次只新增解析留痕与模型调用账本，command_jobs为0，无审批、执行尝试或平台写入口。实际命令、源码摘要、费用与适用边界见[verification.json](verification.json)。

溯源说明：[规则执行基线](rule-execution.json)分别保留首轮与修后规则源码SHA256。两轮实验留痕的rules_version当时均为command-rules-v1，源码摘要可区分两轮；完成复验后仅将最终版本标识升为command-rules-v2，未再改语义。v2标签的单元/契约与静态检查通过；不把最后的版本标签改名伪称又跑了一轮付费请求。后续live入口会在运行前记录规则版本与源码摘要。

费用与许可：[approval.json](approval.json)记录用户原始授权及Agent内部保护上限。首轮独立许可28次/100分、修后3次/10分；单次保守报价3分，实际总31次、预占93分，不重置历史6次许可或任何本轮已用记录。按当日官方高峰缓存未命中价输入2元/百万、输出8元/百万推算费用见verification；只作推算，实际账单未核对，费用仍UNKNOWN/NULL，不按超时或测试库清理退零。价格依据[DeepSeek官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

测试只使用合成指令/授权对象和UUID临时数据库；请求模型deepseek-v4-flash，响应实际标识deepseek-flash分别保存。没有模型工具、思考、自动修复/重试/切备，没有上传真实业务素材。两个临时测试库已查询确认删除，app_migrate恢复NOLOGIN，见[cleanup-verification.json](cleanup-verification.json)；本地持久许可仍保留已用计数。

复现入口：backend/tests/live/test_command_parse.py默认被pytest排除；live需YULIANG_COMMAND_LIVE_APPROVED=1和独立持久许可，修后定向套件另需YULIANG_COMMAND_LIVE_RUN=recheck。本轮两份许可均已使用，直接重跑会拒绝，不自动清零。复演历史结果可将归档raw按相同案例交给guard_model，无须再请求供应商。

范围与阻塞：HTTP默认仍使用规则，不隐式启用生产付费；广告计划真实目录待M1同步，真实前端F06未联调。L3/BE/FE复核PENDING，G0未放行，后端技术PASS不冒充整链DONE。按149张叶卡依赖审计，当前无其他未启动且全部技术前置就绪的卡；下一主链M1-01/M1-03a等等待F01真实浏览器接线和G0联合复核。回退保留0014留痕与费用未知事实，可回退应用或关闭身份入口，不执行删库降级。
