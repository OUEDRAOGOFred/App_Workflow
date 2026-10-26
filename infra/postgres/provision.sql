-- Runs both on fresh clusters and on phase-1 volumes without deleting data.
-- Auth uses the cluster owner; business roles must not connect to its database.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database())
\gexec
SELECT format('CREATE ROLE workflow_agents LOGIN PASSWORD %L', :'agent_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'workflow_agents')
\gexec
SELECT format('CREATE ROLE workflow_workflows LOGIN PASSWORD %L', :'workflow_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'workflow_workflows')
\gexec
SELECT 'CREATE DATABASE workflow_agents OWNER workflow_agents'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'workflow_agents')
\gexec
SELECT 'CREATE DATABASE workflow_workflows OWNER workflow_workflows'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'workflow_workflows')
\gexec
REVOKE ALL ON DATABASE workflow_agents FROM PUBLIC;
REVOKE ALL ON DATABASE workflow_workflows FROM PUBLIC;
GRANT CONNECT ON DATABASE workflow_agents TO workflow_agents;
GRANT CONNECT ON DATABASE workflow_workflows TO workflow_workflows;
SELECT format('CREATE ROLE workflow_integrations LOGIN PASSWORD %L', :'integration_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'workflow_integrations')
\gexec
SELECT 'CREATE DATABASE workflow_integrations OWNER workflow_integrations'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'workflow_integrations')
\gexec
REVOKE ALL ON DATABASE workflow_integrations FROM PUBLIC;
GRANT CONNECT ON DATABASE workflow_integrations TO workflow_integrations;

SELECT format('CREATE ROLE workflow_executions LOGIN PASSWORD %L', :'execution_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'workflow_executions')
\gexec
SELECT 'CREATE DATABASE workflow_executions OWNER workflow_executions'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'workflow_executions')
\gexec
REVOKE ALL ON DATABASE workflow_executions FROM PUBLIC;
GRANT CONNECT ON DATABASE workflow_executions TO workflow_executions;
