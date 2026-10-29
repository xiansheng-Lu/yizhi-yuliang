-- 通知模块 M2-05b：通用通知持久化表。
-- 幂等键在 (tenant_id, idempotency_key) 上做唯一约束，同业务事件只入队一次。
--
-- 设计说明：
--   - 本迁移只建表，不绑定 outbox 或 SMTP 实现；后续 M2-20 接站内通知消费，
--     M2-21 接 SMTP/SMS/PUSH 渠道分发。
--   - payload 是 jsonb，模板渲染参数纯 KV，不含 SMTP 凭据。
--   - channel / status 用 TEXT + CHECK 约束，避免 enum 类型的跨 schema 管理复杂度。

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    recipient TEXT NOT NULL CHECK (char_length(recipient) BETWEEN 1 AND 512),
    channel TEXT NOT NULL CHECK (channel IN ('EMAIL','SMS','PUSH','IN_APP')),
    template TEXT NOT NULL CHECK (char_length(template) BETWEEN 1 AND 128),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','SENT','FAILED','SKIPPED')),
    idempotency_key TEXT NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    last_error TEXT,
    CONSTRAINT uq_notifications_tenant_key UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_notifications_status_created
    ON notifications (status, created_at);
CREATE INDEX IF NOT EXISTS ix_notifications_tenant_status
    ON notifications (tenant_id, status);
