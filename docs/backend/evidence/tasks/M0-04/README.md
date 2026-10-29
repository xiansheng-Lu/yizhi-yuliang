# M0-04 WSL真实基础设施验证

日期：2026-10-27（Asia/Shanghai）；代码基线 `c95a4f85ac095f38fea2d28afd4ab4eb3d98f998`；本地分支feat/backend-wsl-foundation。
负责人BE；用户明确继续开发并提供WSL Docker。工程采用R00技术基线，双端签字仍PENDING。
实际环境Python3.13.14 / WSL2 Ubuntu / Docker29.1.3，Linux虚拟环境独立于Windows。
状态WAITING_REVIEW，技术PASS未替代人工复核。机器证据见verification.json（含命令、结果、版本及源码SHA256）。

## 范围、输入与交付

输入M0-01工程技术基线；Docker环境由用户明确提供。交付独立Compose、扩展/角色初始化、Windows/WSL启动入口、随机本地配置生成和真实读写/重启验证。

## 三条DoD与证据

1. 首次创建本项目四个命名卷并启动PG/Redis/RabbitMQ/MinIO，四服务均健康；所有发布端口只监听127.0.0.1。
   初始internal网络虽然容器健康，但实测NetworkSettings.Ports为null，宿主无法访问；改为本项目独立桥接网络并固定回环绑定后实测通过。
2. 真实普通app_login连接确认非超级用户、无BYPASSRLS/CREATEDB/CREATEROLE、不是数据库所有者且无public CREATE；app_auth/app_migrate同样无特权。
   PG17.10、vector0.8.2及pgcrypto1.3真实查询通过。
3. 向UUID独立探针写入PG值、Redis值、持久MQ消息及签名S3对象；连续重放初始化两次，再重启四服务。
   重启后四类值/对象摘要一致、角色清单无新增，探针清理通过。尚无租户模型或种子，本次不伪造租户；租户相关重复初始化验证归M0-06/M0-13。

实际`uv run --frozen python scripts/test_infrastructure.py --lifecycle`：5 PASS，0 FAIL，0 SKIP。
角色/设施正例在真实容器运行，单元夹具不计为此结果。

## 问题处理与运行状态

- 原MinIO标签通过镜像代理返回403，Quay返回401；核对本机缓存同版本输出后固定RepoDigest，不使用latest引用。
- Windows localhost无法访问WSL服务，Windows四项冒烟曾失败；Linux内真实5项已通过，Windows失败不计PASS。开发流程明确在WSL运行设施测试。
- 生成的本地随机密码仅存忽略文件，提交前逐值扫描暂存文件未发现泄露；应用.env不含postgres/迁移凭据。
- 四服务留在WSL运行供后续开发；`bash scripts/infra.sh stop`停止，不删除卷。网络重建时执行过不带-v的本项目down，未删除命名卷。
- 没有业务迁移；初始化脚本的角色/扩展用于本地开发，回滚停止本项目服务并反向提交配置，命名卷数据保留。

E06“缺Docker”已解除，M0-04仍待BE复核；无FE交互范围，不代签BE。R00-01～04人工复核E08继续存在。
真实数据库已可作为M0-06输入，但不代表身份模型、RLS、业务事务或平台权限已验收。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
