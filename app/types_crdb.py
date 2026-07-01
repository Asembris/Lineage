"""CockroachDB-specific SQLAlchemy types.

Vector: renders `VECTOR(n)` DDL and marshals list[float] <-> CRDB's '[..]' literal.
We deliberately do NOT use the pgvector package (it targets the Postgres extension and
we don't want to rely on OID/type-registration compatibility with CRDB). A tiny
UserDefinedType is enough and was verified against v25.4.10 (see scripts/probe_crdb.py).
"""

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw) -> str:
        return f"VECTOR({self.dim})"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            # list[float] -> '[1.0,2.0,3.0]' literal that CRDB accepts
            return "[" + ",".join(repr(float(x)) for x in value) + "]"

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            # '[1,2,3]' -> [1.0, 2.0, 3.0]
            return [float(x) for x in value.strip("[]").split(",") if x != ""]

        return process
