# Implementation Plan — SQL Server → Aurora MySQL: Database Discovery

Tasks run in order, one at a time (see `#task-execution`). Backend verification is
`cd backend && make lint && make test`; frontend verification is `cd frontend && npm run lint && npm run test`.

## Tasks

- [x] 1. Add `sqlglot` to `backend/pyproject.toml`, refresh `uv.lock`, regenerate `requirements.txt`
  - _Requirements: 2.6_

- [x] 2. T-SQL scanner — `backend/services/db_discovery/tsql_scanner.py`
  - Masking, batch split, object detection, table columns, type mapping, references, sqlglot parse flag
  - Tests: `backend/tests/test_db_discovery_tsql.py`
  - _Requirements: 2.1–2.6_

- [x] 3. Rule catalogue — `backend/services/db_discovery/mysql_compat_rules.py`
  - Tests: `backend/tests/test_db_discovery_rules.py`
  - _Requirements: 3.1–3.3_

- [x] 4. C# embedded-SQL scanner — `backend/services/db_discovery/csharp_sql_scanner.py`
  - Tests: `backend/tests/test_db_discovery_csharp.py`
  - _Requirements: 4.1–4.7_

- [x] 5. Discovery service — `backend/services/db_discovery/discovery_service.py`
  - Cross-reference, effort, summary, Markdown report
  - _Requirements: 5.1–5.2_

- [x] 6. Sparse clone — optional `sparse_paths` on `GitHubHandler.clone`
  - _Requirements: 1.2_

- [x] 7. Routes — `backend/routes/db_discovery.py`, registered in `main.py`; `app_state.db_discovery_storage`; `contracts.md` updated
  - Tests: `backend/tests/test_db_discovery_routes.py`
  - _Requirements: 1.1–1.5, 3.4, 5.3_

- [x] 8. Reference fixture — `samples/wwi-legacy-dotnet/` and `samples/wwi-legacy-dotnet/build-demo-zip.sh`
  - End-to-end service test over the fixture: `backend/tests/test_db_discovery_service.py`
  - _Requirements: 7.1–7.2_

- [x] 9. Frontend — `types/dbDiscovery.ts`, API client, `ProgressTracker` status-source props,
  `pages/DatabaseDiscoveryPage.tsx`, `pages/DatabaseDiscoveryResults.tsx`, navigation section, routes
  - Tests: co-located `.test.tsx`
  - _Requirements: 6.1–6.3_

- [x] 10. Deploy and accept — `docker compose up -d --build`; run GitHub sub-path discovery of
  WideWorldImporters and ZIP upload of the demo archive through `http://localhost:3000`; verify every
  endpoint and UI tab
  - _Requirements: all_

## Task Dependency Graph

```
1 → 2 → 3 → 4 → 5 → 7 → 8 → 9 → 10
          6 ──────────┘
```

Task 5 depends on 3 because effort scoring reads rule severities; task 7 depends on 6 because the
GitHub route passes `sparse_paths`.

## Phase 2 Tasks

- [x] 11. Scanner records `primary_key` per table; `schema_ddl.py` + `GET …/schema.sql`
  - _Requirements: 8_
- [x] 12. MySQL sandbox: `mysql` compose service, settings, `mysql_sandbox.py`, PyMySQL dependency
  - _Requirements: 9.3_
- [x] 13. Conversion agent, prompts, store, routes (`/conversions`, review, `conversions.sql`)
  - Tests mock Bedrock and the sandbox; one integration test compiles against the real sandbox when reachable
  - _Requirements: 9_
- [x] 14. Validation harness: connections, normalisation, row count/checksum, procedure tests, copy, routes; pymssql dependency
  - _Requirements: 10_
- [x] 15. DMS artifacts generator + `GET …/dms-artifacts.zip`
  - _Requirements: 12_
- [x] 16. Terraform `infra/terraform/rds-sqlserver-test/` + `restore-wwi.sh`; `terraform validate`
  - _Requirements: 11_
- [x] 17. Frontend: Conversion, Validation and DMS tabs on the results page
  - _Requirements: 9.6, 10, 12_
- [x] 18. Deploy and accept: convert real WWI procedures with Opus 5.5 through the UI, approve/edit/reject,
  export; download schema and DMS artifacts; validation status and (if a source is configured) a run
  - _Requirements: 8–12_
- [x] 19. Split the UI: discovery report page keeps the five analysis tabs; `pages/DatabaseMigrationPage.tsx`
  at `/database-discovery/:id/migration` holds Conversion, Validation and DMS
  - _Requirements: 6.4_
- [x] 20. Checker 3 (behaviour tests): `services/db_conversion/behaviour_test.py`, prompt `sql-conversion-tests.md`,
  loop integration, `PUT …/tests`, review UI; provision test RDS with `infra/terraform/rds-sqlserver-test`
  - _Requirements: 9.4a_
