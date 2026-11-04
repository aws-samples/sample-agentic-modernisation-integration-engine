"""SQL Server → Aurora MySQL database discovery.

Deterministic analysis of T-SQL scripts and of the C# code that talks to them. No model calls are
made here: the output is grounded inventory and rule findings that later phases (conversion agent,
validation, DMS integration) consume as context.
"""
