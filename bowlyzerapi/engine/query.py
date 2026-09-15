"""SELECT / WITH compiler over typed expressions."""

from __future__ import annotations

from typing import Any, Union

from bowlyzerapi.engine.expr import (
    Expr,
    Literal,
    OrderTerm,
    Unnest,
    quote_ident,
    to_expr,
    to_order,
)
from bowlyzerapi.engine.schema import Table


FromItem = Union[Table, Unnest, "Query"]


class Query:
    def __init__(self) -> None:
        self._ctes: list[tuple[str, Query]] = []
        self._from: list[FromItem] = []
        self._select: list[Expr] = []
        self._where: list[Expr] = []
        self._group: list[Expr | Literal] = []
        self._joins: list[tuple[str, FromItem, Expr]] = []
        self._order: list[OrderTerm] = []
        self._distinct = False
        self._limit: int | None = None
        self._alias: str | None = None

    @classmethod
    def with_ctes(cls, **ctes: Query) -> Query:
        return cls().with_(**ctes)

    def from_(self, *items: FromItem) -> Query:
        self._from.extend(items)
        return self

    def with_(self, **ctes: Query) -> Query:
        for name, query in ctes.items():
            quote_ident(name)
            self._ctes.append((name, query))
        return self

    def select(self, *exprs: Any) -> Query:
        self._select.extend(to_expr(e) if not isinstance(e, Expr) else e for e in exprs)
        return self

    def distinct(self) -> Query:
        self._distinct = True
        return self

    def where(self, *preds: Expr) -> Query:
        self._where.extend(preds)
        return self

    def group_by(self, *items: Any) -> Query:
        for item in items:
            if isinstance(item, int):
                self._group.append(Literal(str(item)))
            elif isinstance(item, Expr):
                self._group.append(item)
            else:
                self._group.append(to_expr(item))
        return self

    def left_join(self, item: FromItem, on: Expr) -> Query:
        self._joins.append(("LEFT JOIN", item, on))
        return self

    def join(self, item: FromItem, on: Expr) -> Query:
        self._joins.append(("JOIN", item, on))
        return self

    def cross_join(self, item: FromItem) -> Query:
        self._joins.append(("CROSS JOIN", item, Literal("TRUE")))
        return self

    def order_by(self, *items: Expr | OrderTerm | str | int) -> Query:
        self._order.extend(to_order(item) for item in items)
        return self

    def limit(self, n: int) -> Query:
        if n < 0:
            raise ValueError("limit must be >= 0")
        self._limit = n
        return self

    def as_(self, alias: str) -> Query:
        quote_ident(alias)
        self._alias = alias
        return self

    def compile(self, params: list[Any] | None = None) -> tuple[str, list[Any]]:
        params = [] if params is None else params
        sql = self._compile_body(params, include_ctes=True)
        return sql, params

    def __str__(self) -> str:
        sql, params = self.compile()
        return f"{sql}\n-- params ({len(params)}): {params!r}"

    def compile_from(self, params: list[Any]) -> str:
        sql = self._compile_body(params, include_ctes=True)
        alias = self._alias or "sub"
        return f"({sql}) AS {quote_ident(alias)}"

    def _compile_body(self, params: list[Any], *, include_ctes: bool) -> str:
        parts: list[str] = []
        if include_ctes and self._ctes:
            cte_sql = []
            for name, query in self._ctes:
                inner = query._compile_body(params, include_ctes=True)
                cte_sql.append(f"{quote_ident(name)} AS (\n{inner}\n)")
            parts.append("WITH " + ",\n".join(cte_sql))
        if not self._from and not self._ctes:
            raise ValueError("Query has no FROM")
        select_kw = "SELECT DISTINCT" if self._distinct else "SELECT"
        if self._select:
            select_list = ",\n    ".join(_select_item(e, params) for e in self._select)
        else:
            select_list = "*"
        parts.append(f"{select_kw}\n    {select_list}")
        if self._from:
            from_sql = ", ".join(_compile_from_item(item, params) for item in self._from)
            parts.append(f"FROM {from_sql}")
        for kind, item, on in self._joins:
            target = _compile_from_item(item, params)
            if kind == "CROSS JOIN":
                parts.append(f"CROSS JOIN {target}")
            else:
                parts.append(f"{kind} {target} ON {on.compile(params)}")
        if self._where:
            pred = self._where[0]
            for extra in self._where[1:]:
                pred = pred & extra
            parts.append(f"WHERE {pred.compile(params)}")
        if self._group:
            parts.append(
                "GROUP BY " + ", ".join(g.compile(params) for g in self._group)
            )
        if self._order:
            parts.append("ORDER BY " + ", ".join(o.compile(params) for o in self._order))
        if self._limit is not None:
            parts.append(f"LIMIT {int(self._limit)}")
        return "\n".join(parts)


def _select_item(expr: Expr, params: list[Any]) -> str:
    return expr.compile(params)


def _compile_from_item(item: FromItem, params: list[Any]) -> str:
    if isinstance(item, (Table, Unnest, Query)):
        return item.compile_from(params)
    raise TypeError(f"Unsupported FROM item: {type(item)!r}")


def execute(con: Any, query: Query) -> Any:
    sql, params = query.compile()
    try:
        return con.execute(sql, params)
    except Exception as exc:
        raise RuntimeError(f"Query failed:\n{sql}\nparams={params!r}\n{exc}") from exc


def fetch_rows(con: Any, query: Query) -> list[tuple[Any, ...]]:
    return execute(con, query).fetchall()


def fetch_dicts(con: Any, query: Query) -> list[dict[str, Any]]:
    cur = execute(con, query)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def fetch_one(con: Any, query: Query) -> tuple[Any, ...] | None:
    return execute(con, query).fetchone()


def fetch_scalar(con: Any, query: Query) -> Any:
    row = fetch_one(con, query)
    return None if row is None else row[0]
