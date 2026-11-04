# Requirements Document — SQL Server → Aurora MySQL: Database Discovery

## Introduction

Database Discovery is the first phase of the SQL Server → Amazon Aurora MySQL modernisation pathway.
It produces a deterministic inventory of a SQL Server database's code and of the application code that
talks to it, and flags every construct that needs work to run on MySQL 8.0 (Aurora MySQL v3).

It answers the assessment questions a migration team asks before choosing a conversion approach:
how many objects there are, where the business logic lives (stored procedures or application code),
which constructs have no MySQL equivalent, and which objects are called from the application.

AWS Transform's full-stack Windows modernisation converts SQL Server to Aurora **PostgreSQL**, so the
MySQL target needs its own pathway. This phase is analysis only. Conversion (Bedrock agent),
data validation, and AWS DMS Schema Conversion integration are later phases and are listed under
"Out of scope".

The reference test input is Microsoft's **WideWorldImporters** SSDT project
(`microsoft/sql-server-samples`, `samples/databases/wide-world-importers/wwi-ssdt`, MIT licence),
plus a small .NET Framework fixture app in `samples/wwi-legacy-dotnet/` that calls it.

## Glossary

| Term | Definition |
|---|---|
| Discovery | One run of the analyzer over one source tree; identified by a discovery ID |
| Batch | A T-SQL batch: the text between `GO` separators |
| Object | A database object found in the scripts (table, view, procedure, function, trigger, sequence, type, index, …) |
| Rule | A deterministic check that flags one SQL Server construct needing work on MySQL |
| Finding | One rule matching one object or one application file, with its occurrence lines |
| Embedded SQL | A SQL statement held in a C# string literal |
| Severity | `blocker` (no MySQL equivalent, redesign needed), `high` (manual rewrite), `medium` (known rewrite pattern), `low` (mechanical change, normally automated by schema conversion), `info` |

## Requirements

### Requirement 1: Source intake

**User Story:** As a migration engineer, I want to point discovery at a ZIP or a Git repository
(optionally a sub-folder of it), so that I can assess a database project without a live connection.

1. WHEN a ZIP is uploaded to `POST /api/db-discovery/upload` THEN the system SHALL accept only `.zip`
   files, extract them with zip-slip and zip-bomb protection, and start discovery in the background.
2. WHEN a repository URL is posted to `POST /api/db-discovery/github` THEN the system SHALL clone it
   with the existing SSRF protections; IF `subpath` is given THEN the system SHALL sparse-check-out
   and analyze only that path.
3. IF `subpath` is absolute, contains `..`, or contains characters outside `[A-Za-z0-9_./ -]` THEN the
   system SHALL reject the request with HTTP 400.
4. The system SHALL return `{"discovery_id", "status", "message"}` immediately and expose progress at
   `GET /api/db-discovery/{id}/status`.
5. Discovery results SHALL be stored separately from code analyses, so they never appear in
   `GET /api/analyses`.

### Requirement 2: T-SQL object inventory

**User Story:** As a migration engineer, I want every database object listed with its location and
size, so that I can size the conversion.

1. The system SHALL read `.sql` files (UTF-8, UTF-8 BOM, or UTF-16), split them into batches on `GO`
   lines, and ignore `GO` inside comments or string literals.
2. The system SHALL identify tables, views, procedures, functions (scalar, inline table-valued,
   multi-statement table-valued), triggers (DML and DDL), sequences, user-defined types (alias and
   table types), schemas, indexes, partition functions, partition schemes, synonyms, security
   principals, security policies, assemblies, Service Broker objects, full-text objects, filegroups,
   and SQL Agent jobs created through `msdb.dbo.sp_add_job`.
3. For each object the system SHALL record schema, name, type, subtype, file, start line, and lines
   of code.
4. For each table the system SHALL record its columns with SQL Server data type, nullability, identity,
   computed-column flag, and a suggested MySQL 8.0 data type with a note where the mapping loses
   behaviour.
5. The system SHALL record object references (FROM, JOIN, INTO, UPDATE, MERGE, EXEC, REFERENCES)
   resolved against the inventory, and the reverse `referenced_by` list.
6. The system SHALL record per batch whether `sqlglot` (T-SQL dialect) parsed it into an AST, and
   report the parse coverage. Discovery SHALL NOT depend on that parse succeeding.

### Requirement 3: MySQL incompatibility rules

**User Story:** As a migration engineer, I want each SQL Server construct that needs work on MySQL
flagged with a severity and a recommendation, so that I can see where manual effort goes.

1. Rules SHALL run on text with comments and string-literal contents masked, so that commented-out
   code and string data never produce findings.
2. The rule catalogue SHALL cover at least: `MERGE`; `OUTPUT` clause; recursive CTEs and
   `MAXRECURSION`; `IDENTITY`, `SET IDENTITY_INSERT`, `SCOPE_IDENTITY`; sequences and
   `NEXT VALUE FOR`; data types `DATETIMEOFFSET`, `UNIQUEIDENTIFIER`, `HIERARCHYID`, `SQL_VARIANT`,
   `XML`, `GEOGRAPHY`/`GEOMETRY`, `ROWVERSION`/`TIMESTAMP`, `MONEY`, `(N)VARCHAR(MAX)`, `DATETIME2(7)`;
   collation clauses; cross-database three-part names; linked servers (four-part names,
   `OPENQUERY`, `OPENROWSET`, `OPENDATASOURCE`); CLR assemblies; Service Broker; `TRY/CATCH`,
   `THROW`, `RAISERROR`, error functions; transactions and `XACT_ABORT`; temporary tables and table
   variables; table-valued parameters; dynamic SQL; cursors; `TOP`, `OFFSET/FETCH`; table and query
   hints including `NOLOCK`; `APPLY`; `PIVOT`; JSON and XML functions; temporal tables; memory-optimised
   tables and natively compiled modules; columnstore and filtered indexes; partitioning; row-level
   security; dynamic data masking; `EXECUTE AS`; `WITH ENCRYPTION`; `INSTEAD OF` and DDL triggers;
   `inserted`/`deleted` pseudo-tables; SQL Agent job procedures; `xp_cmdshell`, `sp_OA*`,
   `sp_send_dbmail`; system catalog access; full-text predicates; synonyms; T-SQL built-in functions
   with different MySQL names or semantics.
3. Each finding SHALL carry rule ID, title, category, severity, object, file, first line, occurrence
   count, up to five occurrence lines, a one-line source snippet, and a recommendation.
4. `GET /api/db-discovery/rules` SHALL return the full rule catalogue.

### Requirement 4: Application embedded-SQL scan

**User Story:** As a migration engineer, I want the C# code that talks to SQL Server inventoried,
because many migrations break in application code rather than in the schema.

1. The system SHALL scan `.cs`, `.config`, `.csproj`, `packages.config`, `.edmx`, and
   `appsettings*.json` files.
2. The system SHALL detect data-access technology use: `System.Data.SqlClient`,
   `Microsoft.Data.SqlClient`, Dapper, Entity Framework 6, EF Core SQL Server, and SQL Server-specific
   APIs (`SqlBulkCopy`, `SqlDependency`, `SqlDbType.Structured`, `SqlException.Number`,
   `Microsoft.SqlServer.Types`, `TransactionScope`, EF `SqlFunctions`, `DbGeography`).
3. The system SHALL extract C# string literals (regular, verbatim, interpolated, and `+`-concatenated
   runs), classify the ones that are SQL statements, and run the Requirement 3 rules on them.
4. The system SHALL flag interpolated or concatenated SQL as a SQL-injection risk.
5. The system SHALL resolve stored-procedure names used in application code against the inventory
   and record, per procedure, the code locations that call it.
6. The system SHALL find SQL Server connection strings and flag Windows authentication, MARS, and
   embedded passwords. Passwords SHALL be redacted before the connection string is stored or returned.
7. The system SHALL list data-access NuGet packages and target framework versions, each with a
   suggested MySQL-compatible replacement.

### Requirement 5: Assessment summary and report

**User Story:** As a solutions architect, I want a summary and a downloadable report, so that I can
share the assessment with the customer.

1. The system SHALL compute an effort bucket per object (`low`, `medium`, `high`, `very_high`) with a
   documented formula, and any object with a `blocker` finding SHALL be at least `high`.
2. The summary SHALL include files scanned, object counts by type, finding counts by severity and
   category, top rules, effort distribution, schemas, sqlglot parse coverage, type-mapping table, and
   application totals.
3. `GET /api/db-discovery/{id}` SHALL return the full report; `GET /api/db-discovery/{id}/report.md`
   SHALL return a Markdown report; `GET /api/db-discoveries` SHALL list discoveries;
   `DELETE /api/db-discovery/{id}` SHALL delete one.

### Requirement 6: User interface

**User Story:** As a migration engineer, I want a Database Discovery section in the app.

1. The navigation SHALL have a "Database Discovery" section with a "SQL Server → Aurora MySQL" entry.
2. The start page SHALL offer GitHub (URL, branch, sub-path, optional PAT) and ZIP inputs, show
   progress, and list previous discoveries.
3. The results page SHALL show Overview, Objects, Findings, Application SQL, and Type Mapping tabs,
   with filters for object type, severity, category, and free-text search, an object detail drawer,
   and Markdown/JSON download.
4. The results page is the read-only discovery report. Conversion, validation and DMS artifacts SHALL be
   on a separate migration page (`/database-discovery/{id}/migration`, tabs Conversion, Validation,
   DMS) reached from a "Continue to migration" button, so that running a discovery never looks like it
   converts anything.

### Requirement 7: Reference test input

1. `samples/wwi-legacy-dotnet/` SHALL contain a small .NET Framework 4.8 fixture app that calls
   WideWorldImporters through ADO.NET, Dapper, and EF6, includes a batch job, a SQL Agent job script,
   and a `Web.config` with SQL Server connection strings.
2. A script SHALL assemble WideWorldImporters SSDT plus the fixture into one demo ZIP.
   WideWorldImporters SHALL be downloaded, never committed.

## Phase 2: conversion, validation, DMS artifacts

Phase 2 builds on the discovery report. Its requirements follow.

### Requirement 8: Target schema DDL

**User Story:** As a migration engineer, I want MySQL table DDL generated from the inventory, so that
converted code can be compiled against real tables and the schema can be reviewed.

1. `GET /api/db-discovery/{id}/schema.sql` SHALL return MySQL 8.0 DDL for every table: one
   `CREATE DATABASE IF NOT EXISTS` per SQL Server schema (a SQL Server schema becomes a MySQL database
   of the same name), and one `CREATE TABLE` per table using the Requirement 2.4 type mapping,
   nullability, `AUTO_INCREMENT`, and the primary key.
2. Columns that cannot be mapped mechanically (computed columns, `sql_variant`, unresolved types)
   SHALL be emitted as nullable `LONGTEXT` with a `-- TODO` comment naming the original definition.
3. The generator is deterministic; no model call is made.

### Requirement 9: Conversion agent (Maker → Checker → Reviewer)

**User Story:** As a migration engineer, I want procedures, functions, views and triggers converted
to MySQL by an agent whose output is checked before I see it, so that I review working code.

1. `POST /api/db-discovery/{id}/conversions` with a list of object IDs (procedure, function, view,
   trigger; at most 25 per request) SHALL start a background conversion run.
2. **Maker:** Bedrock (`BEDROCK_MODEL_ID`, Claude Opus 5.5) SHALL receive the object's T-SQL, its
   discovery findings and recommendations, and the MySQL DDL of the tables it references, and SHALL
   return exactly one MySQL `CREATE` statement with no `DELIMITER` lines.
3. **Checker, deterministic:** the statement SHALL be executed against a MySQL 8.0 sandbox in which
   the generated schema DDL (Requirement 8) is loaded. A MySQL error is a failed check.
4. **Checker, model:** a second Bedrock call SHALL score the conversion from 1 to 5 on semantic
   equivalence, completeness, error handling, transaction semantics and MySQL idiom, and return a
   verdict and a list of issues. A conversion passes when it compiles, the verdict is `pass`, and no
   score is below 4.
4a. **Checker, behaviour (Checker 3):** when a SQL Server source is configured, a conversion that compiles
   and passes the model check SHALL be run against SQL Server and against the MySQL sandbox with the
   same inputs on the same data before it reaches the Reviewer:
   - every table the object references SHALL be copied from SQL Server into the sandbox first;
   - test cases SHALL be generated from the object's parameters and sample rows (3–5 per object), and
     reviewers MAY add their own (`PUT …/conversions/{object_id}/tests`); both SHALL be kept across
     re-conversions;
   - result sets SHALL be compared after normalisation; for objects that write, the after-state of each
     referenced table SHALL be compared, and both engines SHALL be returned to their prior state
     (SQL Server rollback, MySQL snapshot restore);
   - a difference SHALL fail the attempt and be fed back to the Maker with the failing inputs and what
     each engine returned (row counts and first rows, or the error), stated as measured fact that
     overrides any model reviewer suggestion; the model checker of the next attempt SHALL receive the
     same measured results so it does not ask to "confirm" a measured case;
   - without a source, or for objects the harness cannot call (triggers, table-valued functions,
     table-valued parameters), the check SHALL be skipped and the record marked as not behaviour-tested.
5. A failed check SHALL be fed back to the Maker with the MySQL error and checker issues, up to
   `CONVERSION_MAX_ATTEMPTS` (default 3). Every attempt SHALL be recorded.
6. **Reviewer:** a human SHALL approve, reject, or edit each conversion. An edit SHALL be re-compiled
   in the sandbox before it is saved. Only approved conversions are exported.
7. `GET /api/db-discovery/{id}/conversions` SHALL list conversion records;
   `GET /api/db-discovery/{id}/conversions.sql` SHALL return the schema DDL followed by every
   approved conversion.
8. A Bedrock or sandbox outage SHALL mark the affected conversions `failed` with the cause, never
   `converted`.

### Requirement 10: Data validation harness

**User Story:** As a migration engineer, I want to compare source and target data and procedure
results, so that I can prove the migration preserved behaviour.

1. Source (SQL Server) and target (MySQL) connections SHALL come from environment variables
   (`VALIDATION_SQLSERVER_*`, `VALIDATION_MYSQL_*`; the target defaults to the sandbox).
   Passwords SHALL never be returned by the API, written to reports, or logged.
2. `POST /api/db-validation/runs` with a list of tables (default: every table in the discovery) and
   optional procedure tests SHALL start a background run.
3. For each table the run SHALL report source and target row counts and an order-independent
   checksum of normalised row values (numbers, dates, booleans, binary and strings normalised so that
   equal data hashes equally on both engines), and a `match`, `mismatch`, `missing` or `error` status.
4. A procedure test (`{procedure, params}`) SHALL call the procedure on both engines and compare the
   normalised result sets, reporting row-count and first-differing-row details.
5. `POST /api/db-validation/copy` SHALL copy up to N rows per table from source to target, for
   test environments only. It SHALL be disabled unless `VALIDATION_ALLOW_COPY=true`.
6. `GET /api/db-validation/status` SHALL report which connections are configured and reachable,
   without credentials.

### Requirement 11: Test SQL Server on Amazon RDS (Terraform)

1. `infra/terraform/rds-sqlserver-test/` SHALL provision, when a human runs it, an RDS for SQL Server
   instance for testing: Express edition by default, a security group open only to `allowed_cidr`,
   an RDS-managed master password in AWS Secrets Manager, and an option group with
   `SQLSERVER_BACKUP_RESTORE` backed by an S3 bucket and IAM role.
2. A script SHALL download the WideWorldImporters `.bak`, upload it to the bucket, and restore it with
   `rds_restore_database`.
3. The configuration SHALL be marked test-only: no deletion protection, no final snapshot, tags
   `Purpose=amie-test`. It SHALL NOT be applied by the application or by CI.

### Requirement 12: AWS DMS artifacts

1. `GET /api/db-discovery/{id}/dms-artifacts.zip` SHALL return:
   - `table-mappings.json` with a selection rule per schema;
   - `task-settings.json` with `TargetTablePrepMode=DO_NOTHING` (the schema is created from
     Requirement 8/9 output), LOB mode chosen from the discovered column types, and validation enabled;
   - `cloudformation-dms.yaml` for a replication subnet group, replication instance, SQL Server source
     and Aurora MySQL target endpoints (credentials from Secrets Manager), a full-load-and-CDC task, and
     DMS Schema Conversion resources (instance profile, data providers, migration project);
   - `README.md` listing prerequisites derived from the discovery: tables without a primary key
     (CDC cannot capture them), memory-optimised tables, and columns DMS cannot migrate as-is.
2. Artifacts are generated, not deployed: the application never creates AWS resources.
