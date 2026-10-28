# M3-20a 素材账号交接意图准备

**PLANNED：仅只读准备，尚未实现、未执行本卡DB/HTTP/浏览器测试。**
当前主Agent独占最终full regression session51881；本准备未改现批源码/配置，未调用DB、平台、付费或Git。正式开发须合并后新本地分支领取，迁移编号另向主Agent协调。

## 已就绪输入

20册M3-20a（2086节起）直接依赖M3-15b、M3-11a，无外部条件。当前task-status两者均WAITING_REVIEW并有technical PASS；不代签BE/F09/F11。

1. `frontend/src/lib/content-factory.ts` ContentAccountLink实际字段：accountId/accountName/nativeId/productLine/businessEntityId/tenantId/shopId/mode/linkedAt/linkedBy。
2. `frontend/src/lib/content-delivery.ts`：accountLinkKey=JSON.stringify([productLine,accountId])；contentDeliveryAccounts要求当前Identity/owner一致，API来源IntegrationContext尚有效，账户AUTHORIZED、主体与店铺一致、当前account-shop link为AUTHORIZED；MOCK分支只供本地。
3. linkContentAccounts对已有键不重复插入，新增时linkedAt=当前时间、linkedBy=owner.userId；removals移除对应键。validateAccountLinks检查重复/字段/owner范围。当前生命周期发生在ContentState本地容器；尚无账号交接云服务调用。
4. M3-13b导入明确不迁入accountLinks；客户端字符串不能成为当前授权证据。本卡需实际持久服务/HTTP，不是把导入元数据直接转成授权。

## 已有真实服务器能力

- `content/assets.py` AssetService.get/list/download读取已CONFIRMED固定object_version素材；prepare_reference(identifier)在当前scope读取后调用固定版本HEAD/HMAC；_lock与_row、_proof、_attested提供事务内同版本重验基线。当前可用类型为已确认图片/UTF8文本，音视频不绕过duration/media守卫。
- reference_in_transaction(...kind='DELIVERY')是**在途引用**守卫，不应仅因本地交接意图存在就伪造在途平台任务。规划单独保存关联意图；不会借此创建GENERATION/DELIVERY任务。是否意图阻止归档需按当前归档规则明确，不擅自把其等同在途任务。
- `content/routes.py` content(request)提供真实Cookie会话、所有写请求CSRF和X-Shop-Id，服务端VerifiedIdentity。计划新路由复用该入口，拒绝未知query身份覆盖。
- `configuration/integration_service.py` IntegrationService(engine,identity).context(productLine)返回当前RR只读IntegrationContext；PAIRS/ACCOUNT_CONTEXTS核对主体、实体多对多shop/account、连接授权到期、授权账户native_id_snapshot及当前snapshot.read或plan.write范围。context有效期是短期预检来源，不能直接充当提交时授权。
- `api/integrations.py`为已有实际上下文HTTP入口；操作七阶段证据另由OperationDeclaration/evaluate_binding核对。保存交接意图不要求或授予平台UPLOAD写能力，目标账户本身必须有真实有效授权及当前范围。

## 平台上传与审核来源现状

已实现0054 content_uploads/content_assets及0059固定引用：这是浏览器到私有对象存储的上传/CONFIRMED，**不是ADQ/其他平台素材上传成功**。
0033 execution_receipts保存命令执行受理/原始留档关系，但当前没有“content_asset+account+productLine+原生素材ID+上传/审核独立回读”的正式规范投影；不得把任意execution_receipt或HTTP200当素材上传证据。
只读扫描现有迁移/内容模块未发现正式平台素材上传/审核投影表；M3-12尚无实际任务技术完成记录。因此本卡不能编造platformAssetId或审核通过。

计划三个独立维度：

- intent：LINKED/CANCELLED及关联历史；由本卡事务产生。
- platform upload：无正式producer时NOT_INTEGRATED并附原因，不能写成已上传，也不能用空证据证明未上传；将来仅消费M3-12正式规范证据。
- review：当前UNKNOWN/NOT_INTEGRATED；将来审核中/通过/拒绝必须来自平台素材ID对应的独立读取证据，关联删除不删除任何平台历史。

## 计划文件与DDL

新content/account_links_models.py、account_links_service.py、account_links_routes.py；对应unit/integration/实际前端字段消费测试与本卡证据。主Agent负责main include与runner。避免改workflows/execute/accept/redo，与M3-17b独立。

需新迁移（编号待主Agent分配）：

- content_asset_account_links：稳定id、tenant/shop/asset、productLine/account、意图revision、active、linked_by/at、取消actor/time、目标native_id/主体快照、服务器确认固定asset version/hash；唯一tenant+asset+productLine+account。真实shop/account与asset复合FK，provider/productLine通过现有明确映射约束；不会凭名称绑定。
- append-only content_asset_account_link_events：关联/取消actor与revision，当前状态和事件同事务；普通角色禁止改历史。只存必要ID/版本/安全快照，不复制token、下载URL或客户端路径。
- 租户RLS+业务scope；同店团队可读授权资产的关联，不把上传原actor个人权限等同团队素材可见。linkedBy由当前服务端actor写入，客户端值不可信。
- 不预造“平台上传成功”空表或假素材镜像。M3-12未来真实producer的读取adapter另有明确来源时再接线。

## 事务、锁与并发计划

1. 当前真实身份通过content.write及店铺、商品、账户范围。owner×shop×account含义是当前actor的服务端owner身份、资产真实shop、目标实体多对多链接，不能只比客户端tenantId。
2. 新增/重连先prepare_reference取得固定版本服务器证明；事务内锁顺序与AssetService一致，先asset咨询锁、再稳定link键/行，重验资产未归档、CONFIRMED、固定版本/哈希、服务器证明与当前实际账户授权/native链接/主体/权限/到期。
3. asset+productLine+account同键新增不复制；不同状态修改带expectedRevision，旧版本409。并发保存/取消/归档只有合法胜方提交，不写平台。
4. 重复关联返回原linkedAt/linkedBy；取消保留历史和revision，不删除素材、对象文件或平台证据。已归档/失授权目标不能新增；取消仍需当前资产店铺管理权限，但不以目标已撤OAuth或文件已丢失阻塞撤销意图（提交时需单独核清理权限，不要求恢复平台授权）。
5. 写事务同时保存意图当前状态+事件；失败无半关联。列表采用明确有界分页/超限错误，禁止本批已修问题再次静默截断。
6. 保存/删除前后无token申请、平台接口或费用动作。平台上传状态只读，不能由客户端PATCH更改。

## 计划验证集

- 真实PG/MinIO：合法图片/文本固定版本、同店团队读取、同键重复/并发不复制、revision旧值409、保存与取消/归档竞争、正文无关、意图/历史同事务回滚。
- 当前scope：跨tenant/shop/product/account/主体拒绝；native快照不符、OAuth撤权/过期、当前角色或mask撤销后新增失败；伪linkedBy/tenant/mode/nativeId不能覆盖服务器字段；MOCK关联不得转权限。
- 文件：未CONFIRMED/未知资产/版本已换/HEAD失败/媒体时长未验证拒绝新增；不得用客户端路径、mime、size、duration替代证明。
- 生命周期：重复保存保留原linkedAt；取消不物理删文件、不删平台证据；撤权/文件丢失后有管理scope仍可取消意图。
- 分层状态：关联不产生platformAssetId、不变更平台审核状态、缺正式来源明确NOT_INTEGRATED/UNKNOWN，不输出假上传/审核成功。
- 真实HTTP Cookie/CSRF/X-Shop-Id、未知query拒绝、固定frontend ContentAccountLink字段及accountLinkKey/validateAccountLinks消费者验证。
- 真实浏览器F09/F11、实际平台素材上传/审核及M3-12验收单列；技术PASS不等于这些完成。
