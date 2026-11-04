"""Environment-configured connections. Descriptions never include passwords."""

from __future__ import annotations

from dataclasses import dataclass

import pymysql

from config import settings


class ConnectionNotConfiguredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    def describe(self) -> dict:
        return {
            "engine": "sqlserver",
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database": self.database,
        }


@dataclass(frozen=True)
class TargetConfig:
    host: str
    port: int
    user: str
    password: str
    sandbox: bool

    def describe(self) -> dict:
        return {
            "engine": "mysql",
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "sandbox": self.sandbox,
        }


def source_config() -> SourceConfig | None:
    if not settings.VALIDATION_SQLSERVER_HOST:
        return None
    return SourceConfig(
        settings.VALIDATION_SQLSERVER_HOST,
        settings.VALIDATION_SQLSERVER_PORT,
        settings.VALIDATION_SQLSERVER_USER,
        settings.VALIDATION_SQLSERVER_PASSWORD,
        settings.VALIDATION_SQLSERVER_DATABASE,
    )


def target_config() -> TargetConfig | None:
    if settings.VALIDATION_MYSQL_HOST:
        return TargetConfig(
            settings.VALIDATION_MYSQL_HOST,
            settings.VALIDATION_MYSQL_PORT or 3306,
            settings.VALIDATION_MYSQL_USER,
            settings.VALIDATION_MYSQL_PASSWORD,
            False,
        )
    if settings.MYSQL_SANDBOX_HOST:
        return TargetConfig(
            settings.MYSQL_SANDBOX_HOST,
            settings.MYSQL_SANDBOX_PORT,
            settings.MYSQL_SANDBOX_USER,
            settings.MYSQL_SANDBOX_PASSWORD,
            True,
        )
    return None


def connect_source():
    cfg = source_config()
    if cfg is None:
        raise ConnectionNotConfiguredError(
            "SQL Server source is not configured: set VALIDATION_SQLSERVER_HOST/USER/PASSWORD in .env "
            "(see infra/terraform/rds-sqlserver-test)."
        )
    import pymssql

    return pymssql.connect(
        server=cfg.host,
        port=str(cfg.port),
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        login_timeout=15,
        timeout=600,
        charset="UTF-8",
        as_dict=False,
    )


def connect_target():
    cfg = target_config()
    if cfg is None:
        raise ConnectionNotConfiguredError(
            "MySQL target is not configured (VALIDATION_MYSQL_HOST or the sandbox)."
        )
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        connect_timeout=10,
        read_timeout=600,
        write_timeout=600,
        charset="utf8mb4",
        autocommit=True,
    )


def _probe(connect, version_sql: str) -> dict:
    try:
        conn = connect()
    except ConnectionNotConfiguredError as exc:
        return {"configured": False, "reachable": False, "error": str(exc)}
    except Exception as exc:  # driver-specific errors; message only, never the config
        return {"configured": True, "reachable": False, "error": _safe_error(exc)}
    try:
        cur = conn.cursor()
        cur.execute(version_sql)
        version = str(cur.fetchone()[0]).splitlines()[0]
        return {"configured": True, "reachable": True, "version": version}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": _safe_error(exc)}
    finally:
        conn.close()


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    for secret in (
        settings.VALIDATION_SQLSERVER_PASSWORD,
        settings.VALIDATION_MYSQL_PASSWORD,
        settings.MYSQL_SANDBOX_PASSWORD,
    ):
        if secret:
            message = message.replace(secret, "***")
    return message[:500]


def status() -> dict:
    src, tgt = source_config(), target_config()
    return {
        "source": {
            **(src.describe() if src else {"engine": "sqlserver"}),
            **_probe(connect_source, "SELECT @@VERSION"),
        },
        "target": {
            **(tgt.describe() if tgt else {"engine": "mysql"}),
            **_probe(connect_target, "SELECT VERSION()"),
        },
        "copy_enabled": settings.VALIDATION_ALLOW_COPY,
    }


safe_error = _safe_error
