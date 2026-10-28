ALTER TABLE resource_lookup_contexts ADD COLUMN kind varchar(20) NOT NULL DEFAULT 'audiences';
ALTER TABLE resource_lookup_contexts ADD CONSTRAINT lookup_kind CHECK(kind IN ('audiences','targets'));
DROP INDEX resource_lookup_current;
CREATE UNIQUE INDEX resource_lookup_current ON resource_lookup_contexts(tenant_id,kind,context_digest) WHERE active;
