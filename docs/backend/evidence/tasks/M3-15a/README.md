# M3-15a 私有素材上传计划与完成确认

2026-10-28，负责人BE、前端协作F09，WAITING_REVIEW。基线a58882e，本地分支codex/backend-assets-analysis。直接依赖M3-13a云文档和M0-04本地设施已技术通过，人工复核和适用E02平台门禁继续保留。本卡只验证本地真实PG/MinIO，不调用广告/小店平台或付费供应商。

## 实现和范围

0054_content_uploads增加上传计划、校验租约与完成后的私有素材记录，复合tenant/product/shop与tenant/document FK；从Cookie和显式X-Shop-Id核对店铺、内部商品、文档、请求人。clientRequestKey按tenant×actor唯一，完整元数据摘要不同409；对象key只由服务端tenant/shop/upload UUID组成，不接受客户端key/桶/地址。

上传先持久化计划，再返回短时SigV4 PUT，签名绑定Content-Type、Content-Length、SHA256、upload-id元数据和唯一私有key。URL仅当次响应返回，不写数据库/公共日志。只明确允许的图片（PNG/JPEG/WebP）与UTF8纯文本：图片上限20MiB和16777216像素，文本1MiB；这些是当前本地应用策略，不宣称平台上限。音视频计划明确unsupported，保留待后续媒体探测卡，不把未探测文件登记成可用asset，也未放开ContentDocument.assetId。

完成时在短事务中领取VERIFYING token/90秒租约，事务外检查桶必须Enabled版本且无未复核桶策略，HEAD当前对象的实际size/MIME/upload-id，随后固定实际VersionId流式GET复算完整SHA256并有界临时文件解码：Pillow校验格式/结构/实际像素，文本实际UTF8解码和NUL拒绝。不能只凭用户签名Content-Type或meta摘要认定类型。临时文件结束自动删除。最终重新核对权限/范围/token/租约，素材记录与CONFIRMED状态同事务落库；旧worker不能越token落账。失败回PENDING留安全原因，未完成无content_assets行。重复完成直接返回同一asset，不再次读对象。调用者重试同原键不新增计划。

已确认素材持久实际VersionId，重放PUT覆盖最新版本不改变该已确认版本。图片/文本duration=0且NOT_APPLICABLE；音视频未开放，不伪造duration。BlobStore内部提供固定版本短时GET供后卡复用，本卡无公开下载/列表/删除接口，M3-15b另验。

## 配置与本地部署

blob_enabled默认false；启用需显式固定endpoint/bucket/凭据，非local/test只HTTPS，本地仅回环HTTP。main真实生命周期用BlobStore.from_settings装配/close；from_settings不隐式建桶，运行请求才检查。配置值为SecretStr，无实际值入Git。

`python scripts/init_blob_local.py` 已实际创建/复用本地专用yuliang-private-local、启用版本并核对无桶策略；脚本端点固定本机19000，只允许yuliang-private-前缀，无生产修改。CORS只在显式--origin下尝试且禁止通配；本地MinIO PutBucketCors实际NotImplemented，脚本明确报告，不冒充浏览器直传可用。主Agent另持有本地部署精确服务级来源配置/OPTIONS复验；结果需看其证据，不能从本卡API测试推断F09已接通。

新增官方SDK boto3=1.43.120/botocore=1.43.120及Pillow=12.3.0，uv.lock固定。曾探索PyAV后按任务边界撤回，最终依赖/锁/venv无PyAV，不引入未验证音视频链路。

## 实际验证与DoD

命令 `python scripts/test_infrastructure.py --group content-uploads --details` 最终独占7 passed，10.26秒。新增单元 `pytest tests/unit/test_content_uploads.py -q` 17 passed（含PNG结构损坏转受控类型错误）；新增7文件mypy及Ruff通过。

1. 签名绑定与未确认不可用：实际SigV4 MIME改写403；元数据/请求键变更409；缺对象完成不产asset；先持久计划再签名；原键复用通过。
2. 实际对象校验：SHA不符、大小不符、假PNG、错误UTF8均拒绝且0素材；跨店/伪造跨租户key拒绝；非版本桶拒绝。首轮5项已过后补齐这些负例。
3. 幂等和短链：重复完成原asset且不重verify；VERIFYING并发第二请求423，首请求正常完成；后写新版本不覆盖原VersionId；匿名GET403、短签GET实际过期403；SDK/HTTP库签名日志遮蔽单元通过。

真实create_app配置启用与关闭生命周期、Cookie登录、CSRF/店铺头、计划/直接S3 PUT/完成响应通过；无测试手工include_router或state注入。测试桶为各用例新建独立UUID桶，清理仅该桶版本及对象，无用户对象删除。

有一次7项37.21秒运行与其他卡窗口交叉，只留历史不作为最终验收；待reports明确释放后独占重跑7项10.26秒，使用后者为最终证据。无全量通过声明，主Agent统一全量与Git处理。

## 未完成与回滚

BE/F09具名复核、F09实际浏览器CORS/文件上传接线、生产存储方案、音视频类型/时长探测、素材引用/下载/归档均不由本卡关闭。下一READY M3-15b可基于已确认图片/文本对象版本实现读取/引用保护。回滚关闭blob_enabled或上传入口，保留计划/版本证据，不破坏性downgrade；对象仍私有。


本地CORS前置已补齐：主Agent仅重建MinIO服务并配置精确localhost:3000/127.0.0.1:3000来源，实测OPTIONS204来源精确匹配、不可信来源无ACAO、匿名GET403。证据cors.json；该结果不代替F09完整浏览器UI上传验收，也不改变生产配置。逐桶PutBucketCors仍不支持，初始化脚本保留明确失败行为。
