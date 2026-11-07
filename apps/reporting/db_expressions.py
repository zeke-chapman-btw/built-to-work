"""Portable schema predicates usable by raw SQLite backup/recovery tools."""
from django.db import models
from django.db.utils import NotSupportedError


class IsMonday(models.Func):
    """Keep the Monday constraint without Django-only SQLite SQL functions."""
    arity = 1
    output_field = models.BooleanField()

    def as_sqlite(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        # The schema editor / SQLite parameter adapter unescapes the percent sign.
        return "COALESCE(strftime('%%w', " + sql + ") = '1', 0)", params

    def as_postgresql(self, compiler, connection, **extra_context):
        sql, params = compiler.compile(self.source_expressions[0])
        return "(EXTRACT(ISODOW FROM " + sql + ") = 1)", params

    def as_sql(self, compiler, connection, **extra_context):
        raise NotSupportedError("Reporting supports PostgreSQL and SQLite.")
