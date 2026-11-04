"""AWS DMS artifact generation."""

from __future__ import annotations

import io
import json
import zipfile

import yaml

from services.db_dms import artifacts


def _col(name, sql_type, computed=False):
    return {
        "name": name,
        "sql_type": sql_type,
        "mysql_type": "X",
        "computed": computed,
        "nullable": True,
        "identity": False,
    }


REPORT = {
    "discovery_id": "dbupload_20261103_000000",
    "objects": [
        {
            "id": "table:sales.orders",
            "schema": "Sales",
            "name": "Orders",
            "type": "table",
            "subtype": "temporal",
            "file": "a.sql",
            "line": 1,
            "primary_key": ["OrderID"],
            "columns": [
                _col("OrderID", "int"),
                _col("Notes", "nvarchar(max)"),
                _col("Calc", "computed", True),
            ],
        },
        {
            "id": "table:warehouse.cold",
            "schema": "Warehouse",
            "name": "Cold",
            "type": "table",
            "subtype": "memory_optimized",
            "file": "b.sql",
            "line": 1,
            "primary_key": ["Id"],
            "columns": [_col("Id", "bigint")],
        },
        {
            "id": "table:dbo.log",
            "schema": "dbo",
            "name": "Log",
            "type": "table",
            "subtype": "standard",
            "file": "c.sql",
            "line": 1,
            "primary_key": [],
            "columns": [_col("Loc", "geography")],
        },
    ],
}


def test_table_mappings_include_each_schema_and_exclude_memory_optimised():
    rules = artifacts.table_mappings(REPORT)["rules"]
    includes = {
        r["object-locator"]["schema-name"]
        for r in rules
        if r["rule-action"] == "include"
    }
    assert includes == {"Sales", "Warehouse", "dbo"}
    excludes = [r["object-locator"] for r in rules if r["rule-action"] == "exclude"]
    assert excludes == [{"schema-name": "Warehouse", "table-name": "Cold"}]
    assert len({r["rule-id"] for r in rules}) == len(rules)


def test_task_settings_lob_mode_follows_types():
    s = artifacts.task_settings(REPORT)
    assert s["FullLoadSettings"]["TargetTablePrepMode"] == "DO_NOTHING"
    assert (
        s["TargetMetadata"]["SupportLobs"] and s["TargetMetadata"]["LimitedSizeLobMode"]
    )
    no_lob = {**REPORT, "objects": [REPORT["objects"][1]]}
    assert not artifacts.task_settings(no_lob)["TargetMetadata"]["SupportLobs"]
    assert s["ValidationSettings"]["EnableValidation"]


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node)}
    return {suffix: loader.construct_mapping(node)}


_CfnLoader.add_multi_constructor("!", _tag)


def test_cloudformation_template_structure():
    doc = yaml.load(artifacts.cloudformation_template(REPORT), Loader=_CfnLoader)  # noqa: S506 - SafeLoader subclass
    res = doc["Resources"]
    assert res["ReplicationTask"]["Properties"]["MigrationType"] == "full-load-and-cdc"
    assert res["SourceEndpoint"]["Properties"]["EngineName"] == "sqlserver"
    assert res["TargetEndpoint"]["Properties"]["EngineName"] == "aurora"
    assert (
        "SecretsManagerSecretId" in res["TargetEndpoint"]["Properties"]["MySqlSettings"]
    )
    assert json.loads(
        res["ReplicationTask"]["Properties"]["TableMappings"]
    ) == artifacts.table_mappings(REPORT)
    for key in (
        "SchemaConversionInstanceProfile",
        "SourceDataProvider",
        "TargetDataProvider",
        "MigrationProject",
    ):
        assert res[key]["Condition"] == "CreateSchemaConversion"
    assert "Password" not in json.dumps(doc)


def test_readme_lists_prerequisites():
    text = artifacts.readme(REPORT)
    assert (
        "`dbo.Log`" in text.split("## Tables without a primary key")[1].split("##")[0]
    )
    assert (
        "`Warehouse.Cold`" in text.split("## Memory-optimised tables")[1].split("##")[0]
    )
    assert "`dbo.Log.Loc (geography)`" in text
    assert "`Sales.Orders.Calc`" in text


def test_zip_contents():
    z = zipfile.ZipFile(io.BytesIO(artifacts.build_zip(REPORT)))
    assert sorted(z.namelist()) == sorted(
        [
            "README.md",
            "schema.sql",
            "table-mappings.json",
            "task-settings.json",
            "cloudformation-dms.yaml",
        ]
    )
    assert "CREATE DATABASE IF NOT EXISTS `Sales`;" in z.read("schema.sql").decode()
