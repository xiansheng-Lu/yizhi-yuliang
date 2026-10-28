# 令牌服务安全接入与迁移

本修复只加固两个独立令牌项目，不声明驭量 M1-06/M1-07 已接线或 E01/E02 已验收。

## 配置与身份

服务必填原平台凭据及 API Key（ADQ_TOKEN_API_KEY / WX_TOKEN_API_KEY），并设置：

- TOKEN_TENANT_ID：该服务实例所属的内部租户标识；一个实例只服务一个租户。
- TOKEN_ENCRYPTION_KEY_FILE：私有 JSON 密钥环绝对路径，内容为版本到 base64 32 字节密钥的映射。
- TOKEN_KEY_VERSION：当前写入版本，密钥环可保留旧版本用于解密和显式重加密。

应用 Secret 继续通过本地私有配置或部署密钥管理提供；不会写入令牌密文文件。
私有密钥文件和 .env 均不提交。生产需备份密钥环；丢失后不能恢复旧密文。
首次生成：`python tools/manage_security.py init-key .runtime/token-keys.json --version v1`。
该命令拒绝覆盖现有文件，POSIX 使用 0600；Windows 在写密钥前移除继承权限并仅授予当前用户。
部署使用其他服务账号时，由管理员在私有渠道明确授予该账号读取权限。

ADQ 授权入口必须经可信后端调用：带 X-API-Key 与 X-Actor-ID（后端从已验证会话取得）。
X-Actor-ID 本身不是登录凭据；本服务不实现驭量用户登录或替代其租户权限校验。
回调必须使用服务端固定 HTTPS 地址，state 绑定租户、应用、发起人及回调地址，单次消费。
不允许客户端指定 state 或其他回调，旧的关闭 state 校验开关无效。
state 仅存内存，服务重启后未完成的授权需要重新发起，拒绝绕过。
TOKEN_ALLOW_LOOPBACK_CALLBACK=1 仅用于隔离的本机 mock，正式部署不设置。

## 先停旧服务再迁移

本次没有读取、迁移、重写真实 .env 或 token 文件，也没有重启真实服务。
旧版本不持有新进程锁，因此迁移前必须明确停止所有旧 token 服务和直连 SDK 实例。
设置上述环境变量后执行以下命令。迁移使用同一 owner-lock；锁被占用时不修改源文件。
只迁移原路径，不自动备份明文；已有备份、日志等历史副本需按原密钥管理制度处理。

### ADQ

使用 ADQ_DATA_DIR 的绝对路径，假定为 `D:/codex/tencent-adq/data`；将 APP_ID、ACCOUNT_ID 替换为实际值。

```powershell
python tools/manage_security.py migrate data/adq_credentials.json --context "adq-credentials:APP_ID" --owner-lock data/.token-service.owner.lock
python tools/manage_security.py migrate data/token_ACCOUNT_ID.json --context "adq-access-token:APP_ID:ACCOUNT_ID" --owner-lock data/.token-service.owner.lock
```

每个已有账户快照分别迁移。固定回调地址的 path 必须与 ADQ_CALLBACK_PATH 一致。
凭据/快照完整性或身份不匹配时拒绝启动，不会静默清空后覆盖；即便密文身份不匹配也不会读取上游重新覆盖。
授权换码和后续刷新写回的 refresh_token 都加密保存；平台返回轮换 refresh_token 时先保存新值。

### 微信小店

默认缓存为项目根 token_cache.json，使用其绝对路径作为锁路径前缀：

```powershell
python tools/manage_security.py migrate token_cache.json --context "access-token:APP_ID" --owner-lock "D:/codex/wx_shop/token_cache.json.owner.lock"
```

SDK、dashboard 采集器及只读探针统一设置 WX_TOKEN_SERVICE_URL、WX_TOKEN_API_KEY。
SDK 默认访问托管入口；不再用 AppID/Secret 隐式创建第二个刷新者。
只有独立工具/mock 显式传 allow_direct_token=True 才可直连，禁止与该应用的托管服务并行。
直连缓存严格使用响应 expires_in（不再写死 7200），短 TTL 提前量不超过半衰期，并发请求复用同一刷新结果。

## 部署边界

每个应用指定唯一数据目录/缓存路径、一个进程、一个 worker，业务方可有多个进程。
进程锁持有至退出，第二个服务进程拒绝启动；进程崩溃后操作系统释放锁。
生产默认监听回环，外部访问通过受控 HTTPS 反代；ServiceTokenProvider 拒绝远程明文 HTTP。
此方案只支持单机本地文件系统；多个主机或不同数据目录不会共享锁，不能部署为多副本。
需要多机时应继续使用唯一托管服务入口，而非给每台业务机部署刷新者。
HTTP raw token、刷新、ADQ 授权管理接口强制鉴权，响应标注 Cache-Control: no-store。

## 本地验证（仅合成凭据与 mock）

```powershell
python -m unittest discover -s tests -p "test_*.py" -q
python tests/smoke_test.py
```

两个项目冒烟测试默认使用相同 mock 端口，按顺序运行。
