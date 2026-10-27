# M0-05 CI与空库回归门禁

2026-10-27；负责人BE；FE协作F00；代码基线 371834a574cfd076abeb527b8601a33bb4727bf6。
状态WAITING_REVIEW，不代签前置依赖或BE/FE具名复核。

三条DoD技术通过：GitHub干净Ubuntu运行器完成锁定依赖、ruff/格式/mypy及适用pytest；契约/迁移/隔离故障均被实际测试拒绝；live调用明确NOT_RUN。
[托管CI运行](https://github.com/xiansheng-Lu/yizhi-yuliang/actions/runs/40454908715)全部步骤成功，33项unit/contract、68项真实integration通过，另三类故障注入各先健康0退出、再预期1退出。详细证据见[verification.json](verification.json)。

CI文件由BE维护，固定Action提交、Python与uv，四设施凭据临时随机生成。只输出脱敏状态，末尾清理本次托管运行器资源。
首次运行因MinIO镜像仓库不可匿名拉取失败，未将后续SKIP冒充PASS；修复为从[官方签名发布](https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z)对应固定源码构建测试镜像，保留许可证。该候选不代替M5生产存储验收。

BE复核运行环境、空库/恢复、敏感性断言；FE按F00确认共享CI与契约触发范围。下一张无外部依赖的M2-01a立即推进。
