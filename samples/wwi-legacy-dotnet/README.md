# WWI Legacy .NET — Database Discovery test fixture

A small, deliberately "legacy" .NET Framework 4.8 back-office application for Microsoft's
**WideWorldImporters** sample database. It exists to exercise the Database Discovery feature
(SQL Server → Aurora MySQL): it is scanned, not built or run.

It contains the data-access patterns that break when SQL Server is replaced by MySQL:

| Pattern | Where |
|---|---|
| ADO.NET `SqlClient` calling WWI stored procedures (`CommandType.StoredProcedure`) | `src/WWI.Legacy.Web/DataAccess/CustomerRepository.cs` |
| Inline T-SQL: `TOP`, `NOLOCK`, `ISNULL`, `GETDATE()`, `SCOPE_IDENTITY()`, `OUTPUT inserted` | `CustomerRepository.cs`, `OrderRepository.cs` |
| SQL built by string concatenation and interpolation (injection risk) | `CustomerRepository.cs` |
| Dapper with `MERGE`, `OPENJSON`, `FOR JSON`, and a `FOR SYSTEM_TIME` temporal query | `src/WWI.Legacy.Web/DataAccess/OrderRepository.cs` |
| Table-valued parameter (`SqlDbType.Structured`) into `Website.InsertCustomerOrders` | `OrderRepository.cs` |
| Entity Framework 6 with `HasDefaultSchema`, `Database.SqlQuery`, `DbGeography` | `src/WWI.Legacy.Web/DataAccess/WwiContext.cs` |
| `SqlException.Number` error handling, `TransactionScope` | `OrderRepository.cs` |
| Console batch job with `SqlBulkCopy` and a cursor-style loop | `src/WWI.Legacy.Jobs/NightlyStockSnapshotJob.cs` |
| SQL Server Agent job created through `msdb.dbo.sp_add_job` | `database/jobs/WWI_NightlyJobs.sql` |
| `Web.config` connection strings: Windows authentication, MARS, an embedded password, an EF6 EDMX string | `src/WWI.Legacy.Web/Web.config` |
| `packages.config` and `.csproj` with EF6, Dapper, `Microsoft.SqlServer.Types`, `TargetFrameworkVersion v4.8` | `src/WWI.Legacy.Web/` |

The passwords in `Web.config` are placeholders for the redaction test; they are not real credentials.

## Build the demo archive

`build-demo-zip.sh` downloads the WideWorldImporters SSDT project (MIT licence, from
`microsoft/sql-server-samples`) with a sparse clone and zips it together with this fixture:

```bash
./samples/wwi-legacy-dotnet/build-demo-zip.sh            # writes ./wwi-demo.zip
./samples/wwi-legacy-dotnet/build-demo-zip.sh /tmp/x.zip # custom output path
```

Upload the archive on **Database Discovery → SQL Server → Aurora MySQL → ZIP Upload**.

To analyze WideWorldImporters straight from GitHub instead, use the GitHub tab with:

- Repository URL: `https://github.com/microsoft/sql-server-samples`
- Branch: `master`
- Sub-path: `samples/databases/wide-world-importers/wwi-ssdt`

WideWorldImporters is never committed to this repository.
