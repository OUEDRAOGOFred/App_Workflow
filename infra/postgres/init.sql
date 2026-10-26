-- Only auth-service owns this database. Business databases and roles are
-- provisioned separately by provision.sql, including on existing volumes.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
