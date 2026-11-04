"""C# scanner: literal lexing, SQL classification, API rules, connection strings, packages."""

from __future__ import annotations

from services.db_discovery import csharp_sql_scanner as cs


def _texts(src: str) -> list[str]:
    lits, _ = cs.lex_csharp(src)
    return [lit.text for lit in cs.merge_concatenations(src, lits)]


def test_lexer_handles_all_literal_forms_and_comments():
    src = '''
// var x = "commented out";
/* "also commented" */
var a = "plain \\"quoted\\" text";
var b = @"verbatim ""quoted""
second line";
var c = $"interp {id} and {{braces}}";
var d = $@"both {x}";
var e = """raw "string" here""";
var ch = '"';
'''
    texts = _texts(src)
    assert 'plain "quoted" text' in texts
    assert 'verbatim "quoted"\nsecond line' in texts
    assert "interp {?} and {braces}" in texts
    assert "both {?}" in texts
    assert 'raw "string" here' in texts
    assert not any("commented" in t for t in texts)


def test_concatenation_merges_and_flags_dynamic():
    src = 'var s = "SELECT * " +\n  "FROM Sales.Orders " + "WHERE id = " + id;'
    lits, _ = cs.lex_csharp(src)
    merged = cs.merge_concatenations(src, lits)
    assert len(merged) == 1
    assert merged[0].text == "SELECT * FROM Sales.Orders WHERE id = "
    assert merged[0].concatenated and merged[0].dynamic


def test_static_concatenation_is_not_dynamic():
    src = 'var s = "SELECT a " + "FROM t";'
    lits, _ = cs.lex_csharp(src)
    (lit,) = cs.merge_concatenations(src, lits)
    assert lit.concatenated and not lit.dynamic


def test_looks_like_sql():
    assert cs.looks_like_sql("SELECT a FROM t")
    assert cs.looks_like_sql("  INSERT INTO t (a) VALUES (1)")
    assert cs.looks_like_sql("UPDATE dbo.T SET a = 1")
    assert cs.looks_like_sql("EXEC Website.DoIt @a = 1")
    assert cs.looks_like_sql(";WITH c AS (SELECT 1 AS a) SELECT * FROM c")
    assert not cs.looks_like_sql("Select an option from the list")
    assert not cs.looks_like_sql("Update")
    assert not cs.looks_like_sql("WideWorldImporters")


def test_scan_csharp_finds_apis_sql_and_procedure_candidates():
    src = """
using System.Data;
using System.Data.SqlClient;
using Dapper;
class R {
  void A() {
    using (var cmd = new SqlCommand("Website.SearchForCustomers", conn)) {
      cmd.CommandType = CommandType.StoredProcedure;
      p.SqlDbType = SqlDbType.Structured;
    }
    conn.Execute("EXEC [Website].[RecordVehicleTemperature] @x = 1");
    var rows = conn.Query("SELECT TOP 5 * FROM Sales.Orders WITH (NOLOCK)");
    using (var bulk = new SqlBulkCopy(conn)) { }
    try { } catch (SqlException ex) when (ex.Number == 2627) { }
  }
}
"""
    scan = cs.scan_csharp(src, "R.cs")
    rule_ids = {f.rule_id for f in scan.findings}
    assert {
        "APP_SQLCLIENT",
        "APP_SQLCLIENT_TYPES",
        "APP_TVP",
        "APP_SQLBULKCOPY",
        "APP_SQLEXCEPTION",
    } <= rule_ids
    assert "Dapper" in scan.technologies
    assert "ADO.NET (System.Data.SqlClient)" in scan.technologies
    vias = {(".".join(c.parts), c.via) for c in scan.proc_candidates}
    assert ("Website.SearchForCustomers", "command_type") in vias
    assert ("Website.RecordVehicleTemperature", "exec") in vias
    assert any("NOLOCK" in e.text for e in scan.embedded_sql)


def test_connection_strings_flags_and_redaction():
    config = """<configuration><connectionStrings>
  <add name="Main" connectionString="Data Source=SRV\\PROD;Initial Catalog=WWI;Integrated Security=SSPI;MultipleActiveResultSets=true" providerName="System.Data.SqlClient" />
  <add name="Report" connectionString="Server=tcp:x,1433;Database=WWI;User ID=u;Password=s3cr3t!;Encrypt=True" providerName="System.Data.SqlClient" />
  <add name="Ef" connectionString="metadata=res://*/M.csdl;provider=System.Data.SqlClient;provider connection string=&quot;data source=SRV;initial catalog=WWI;integrated security=True&quot;" providerName="System.Data.EntityClient" />
</connectionStrings></configuration>"""
    scan = cs.scan_config(config, "Web.config")
    by_name = {c.name: c for c in scan.connection_strings}
    assert set(by_name) == {"Main", "Report", "Ef"}
    assert by_name["Main"].windows_auth and by_name["Main"].mars
    assert by_name["Main"].database == "WWI" and by_name["Main"].server == "SRV\\PROD"
    assert by_name["Report"].password_present
    assert (
        "s3cr3t" not in by_name["Report"].redacted
        and "Password=***" in by_name["Report"].redacted
    )
    assert by_name["Ef"].windows_auth and by_name["Ef"].provider == "sqlserver"
    rule_ids = {
        f.rule_id for c in scan.connection_strings for f in cs.connection_findings(c)
    }
    assert {
        "APP_CONN_PROVIDER",
        "APP_CONN_WINDOWS_AUTH",
        "APP_CONN_MARS",
        "APP_CONN_PASSWORD",
    } == rule_ids


def test_non_sql_connection_like_strings_are_ignored():
    scan = cs.scan_config(
        '<add key="Endpoint" value="Server=api.example.com;Timeout=30" />', "App.config"
    )
    assert scan.connection_strings == []


def test_packages_and_frameworks():
    csproj = """<Project>
  <PropertyGroup><TargetFrameworkVersion>v4.8</TargetFrameworkVersion></PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="3.1.0" />
    <PackageReference Include="Newtonsoft.Json" Version="12.0.3" />
    <Reference Include="EntityFramework, Version=6.0.0.0, Culture=neutral" />
  </ItemGroup></Project>"""
    pkgs, frameworks = cs.find_packages(csproj, "a.csproj")
    names = {p.name: p for p in pkgs}
    assert set(names) == {"Microsoft.EntityFrameworkCore.SqlServer", "EntityFramework"}
    assert "Pomelo" in names["Microsoft.EntityFrameworkCore.SqlServer"].replacement
    assert names["EntityFramework"].source == "assembly_reference"
    assert frameworks == ["v4.8"]
    pkgs2, _ = cs.find_packages(
        '<packages><package id="Dapper" version="2.0.0" /></packages>',
        "packages.config",
    )
    assert [(p.name, p.version) for p in pkgs2] == [("Dapper", "2.0.0")]


def test_is_app_file():
    assert (
        cs.is_app_file("Repo.cs")
        and cs.is_app_file("Web.config")
        and cs.is_app_file("packages.config")
    )
    assert cs.is_app_file("appsettings.Production.json") and cs.is_app_file(
        "Model.edmx"
    )
    assert not cs.is_app_file("package.json") and not cs.is_app_file("readme.md")
