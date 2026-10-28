# 原Token服务安全初始化与显式迁移审批方案

2026-10-28（Asia/Shanghai）。状态：**待用户确认，尚未执行本计划的密钥生成、配置写入、缓存迁移或服务启停**。

目标：两个原项目各维持一个本机唯一token托管者，使用yizhi租户身份；恢复已授权的只读核验。不会创建新的平台应用、轮换平台AppSecret、重新发起OAuth、强制刷新token、调用任何广告/订单/资金写接口。后台正常续期可能由唯一托管服务按原策略执行，这与force_refresh区分。

## 已核对入口与当前条件

已阅读两项目 `docs/token-security.md`、`tools/manage_security.py`、`cache/security.py` 和服务启动源码。工具只有`init-key`与`migrate`子命令，**没有dry-run**，不会把实际migrate命令当作试运行。init-key以O_EXCL拒绝覆盖；Windows在写密钥前先限制ACL；migrate先获取指定owner-lock，再读取原文件/写AES-GCM密文/解密校验，不自动备份明文。

WXSHOP当前原配置loopback8099无监听，依赖可导入，缺WX_TOKEN_API_KEY及TOKEN_ENCRYPTION_KEY_FILE/TOKEN_KEY_VERSION/TOKEN_TENANT_ID。8080经进程路径核对为驭量OAuth回调，不能停用或当作微信小店token服务。ADQ是否仍有活动托管者由执行时再次核对进程路径/端口；不输出完整可能含凭据的命令行。

## 审批范围与私有文件

审批对象为：仅本机生成两套独立API key及32字节密钥环；补充两项目私有.env安全配置；将当前已存在的原缓存按明确身份在原路径迁移到AES-GCM；必要时停止已确认的旧token/直连进程、以loopback启动原项目唯一服务；随后执行已授权的最小只读核验。

平台AppID/AppSecret和已有令牌的值不输出、不进入聊天或Git；不重新设置平台Secret。API key是本地新生成的服务调用密钥，与平台Secret区分。生成前检查目标文件不存在，存在即停止并复用/核对既有版本，不覆盖。每项目私有文件拟为`.runtime/token-keys.json`、`.runtime/token-api-key`、`.runtime/security-backup/`；都排除Git，Windows ACL只授当前服务用户，其他服务身份需另明确授权。

## 执行步骤（全部在获准后）

1. 只读确认所有目标路径位于对应项目根、平台AppID引用、ADQ_DATA_DIR和WX_TOKEN_CACHE_FILE最终绝对路径；只列文件计数和格式形状（AES-GCM/旧结构/未知），不输出值。不能因文件存在或历史面板有数据就认定token有效。未知结构停止，不能清空缓存重新抓取覆盖。
2. 核对本机和WSL活动进程的脚本路径/工作目录。只停止确定属于这两个应用的旧token服务和旧直连SDK采集进程，记录PID；不停止OAuth回调或其他服务。若无法确认唯一刷新者，暂停迁移。保留ProcessOwner同缓存路径终身锁；禁止换一个目录绕过锁。
3. 使用原init-key工具各生成一份独立密钥环（精确命令如下）：

```powershell
Set-Location -LiteralPath 'D:/codex/wx_shop'
python tools/manage_security.py init-key .runtime/token-keys.json --version v1
Set-Location -LiteralPath 'D:/codex/tencent-adq'
python tools/manage_security.py init-key .runtime/token-keys.json --version v1
```

本地API key用`secrets.token_urlsafe(32)`独立生成，私有辅助脚本按原init-key同一安全顺序：O_CREAT|O_EXCL创建空文件→Windows icacls移除继承并仅授当前用户→写入随机值。脚本不print密钥，不把秘密放命令行/临时stdout。原manage_security工具不生成API key，因此不虚构该子命令。补写原.env前也先限制私有备份与目标ACL，保留所有现有平台凭据和配置，**不覆盖整个.env**。

4. 在各项目私有配置设置TOKEN_TENANT_ID=yizhi、TOKEN_KEY_VERSION=v1、TOKEN_ENCRYPTION_KEY_FILE为该项目密钥环绝对路径及对应WX_TOKEN_API_KEY/ADQ_TOKEN_API_KEY。HOST限定127.0.0.1，WXSHOP保留PORT=8099；ADQ保留原配置端口且先确认无冲突。WX_TOKEN_SERVICE_URL显式设为http://127.0.0.1:8099，SDK只走该服务，禁止allow_direct_token=True。ADQ保持已有固定HTTPS回调/path，不开启TOKEN_ALLOW_LOOPBACK_CALLBACK或关闭state校验。
5. **备份后迁移**：在停服且持有对应owner锁的离线辅助步骤内，将每个原文件先写一份AES-GCM加密备份到私有security-backup目录，使用其原身份context；不新增明文备份。密钥环另作访问受限的离线备份，密钥与密文分离保管；只在本地验证可解密和文件摘要，不输出内容。随后释放该辅助步骤的锁，再运行原migrate；不能外层持有相同锁同时启动migrate导致自锁。整个批次期间服务保持停止，禁止中途启动。
6. 原工具迁移命令（APP_ID/ACCOUNT_ID只从原配置/文件索引在内存取得，文档不写真实值）：

```powershell
# WXSHOP：实际缓存若不是默认token_cache.json，下面两路径同步替换为已核对绝对路径。
Set-Location -LiteralPath 'D:/codex/wx_shop'
python tools/manage_security.py migrate token_cache.json --context 'access-token:APP_ID' --owner-lock 'D:/codex/wx_shop/token_cache.json.owner.lock'

# ADQ：实际ADQ_DATA_DIR若不同，下面data路径全部使用已核对绝对路径。
Set-Location -LiteralPath 'D:/codex/tencent-adq'
python tools/manage_security.py migrate data/adq_credentials.json --context 'adq-credentials:APP_ID' --owner-lock data/.token-service.owner.lock
python tools/manage_security.py migrate data/token_ACCOUNT_ID.json --context 'adq-access-token:APP_ID:ACCOUNT_ID' --owner-lock data/.token-service.owner.lock
```

每个实际存在的ADQ账户快照单独迁移；不存在不创建假快照。migrate进程要继承该项目私有安全配置，原工具不会自动加载.env，故经本地包装器读取允许的环境变量后作为子进程env注入，不在shell命令行展开秘密。保持TOKEN_TENANT_ID=yizhi及准确AppID/context；不能猜ID。每次迁移原工具持有同一owner锁，遇活动owner拒绝且不改文件；已有密文只在密钥/context正确时允许重加密。未知版本/错误上下文立即停，不能自动删缓存或重新授权。

7. 全部原路径完成密文认证后，用Windows `Start-Process -WindowStyle Hidden`在各原项目工作目录启动原`services/wx_token_service.py`或`services/adq_token_service.py`；一项目一进程一worker。仅PID与日志私有路径保存到本仓库.runtime；服务stdout/stderr仅留本机受限日志，不进Git/聊天。固定原配置loopback，绝不为可达性改成0.0.0.0。OS owner-lock保留至进程退出，重复实例必须拒绝。
8. 首先GET loopback `/health`只记录HTTP及安全状态；然后经API key读取token到内存并立即调用原SDK最小只读：店铺info、一页商品、最多一个详情、一天罗盘；ADQ按已批准账户/接口最小读取。所有请求禁重定向、关闭SDK自动强刷/直连fallback，禁止POST token/refresh；真实业务失败记录errcode及结构，token/PII不落证据。不将此结果冒充驭量runtime真实绑定或G1已验收。

## 失败、回滚与验收证据

任一步鉴权、密钥/context认证、锁或平台业务失败即停止对应项目，另一项目可独立处理。不重复请求平台掩盖根因。迁移前失败保留原路径；迁移中失败保持服务停止，从已经认证的加密备份恢复原路径并验证，使用匹配密钥/context的安全代码读取，**不恢复旧明文服务对外运行**。若环境配置需要回退，先停本次新增PID，再恢复私有配置备份；保留新密钥环和备份以便解密，不能删除密钥。历史明文副本仅登记位置由负责人按既有制度处理，不在本次自动清理。

审批后的证据只含配置存在性、文件/账户计数、密文形状/摘要、迁移通过/失败、PID与loopback端口、health状态和只读errcode/字段结构。平台Token/Secret、API key、加密密钥、原响应PII及真实金额不提交。当前计划仅完成源码核查和文档，所有真实密钥初始化/迁移/启停状态均NOT_RUN。
密钥丢失风险：若对应密钥版本及其安全备份全部丢失，AES-GCM密文无法恢复；所谓回滚不能消除此风险。初始化与迁移审批须包含密钥备份与保管责任确认。迁移只改变持久化格式，不主动更改已有access_token/refresh_token的值；启动后原唯一托管服务按平台正常续期策略可能取得新token，另如实留痕。
