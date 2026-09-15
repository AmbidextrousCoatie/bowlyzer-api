"""Composable SQL expressions. Values are bound parameters; names come from schema."""

from __future__ import annotations

from typing import Any, Iterable, Sequence

_IDENT = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SQL_TYPES = frozenset(
    {
        "BOOLEAN",
        "BIGINT",
        "DATE",
        "DOUBLE",
        "FLOAT",
        "INTEGER",
        "VARCHAR",
    }
)


def quote_ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return f'"{name}"'


def qualify(table: str | None, name: str) -> str:
    ident = quote_ident(name)
    return f"{quote_ident(table)}.{ident}" if table else ident


class Expr:
    __slots__ = ()

    def compile(self, params: list[Any]) -> str:
        raise NotImplementedError

    def as_(self, alias: str) -> Alias:
        return Alias(self, alias)

    def desc(self) -> OrderTerm:
        return OrderTerm(self, descending=True)

    def asc(self) -> OrderTerm:
        return OrderTerm(self, descending=False)

    def nulls_last(self) -> OrderTerm:
        return OrderTerm(self, nulls_last=True)

    def is_null(self) -> Expr:
        return Prefix("IS NULL", self, postfix=True)

    def is_not_null(self) -> Expr:
        return Prefix("IS NOT NULL", self, postfix=True)

    def is_true(self) -> Expr:
        return Prefix("IS TRUE", self, postfix=True)

    def is_false(self) -> Expr:
        return Prefix("IS FALSE", self, postfix=True)

    def in_(self, *values: Any) -> Expr:
        if len(values) == 1 and _is_seq(values[0]):
            items = list(values[0])
        else:
            items = list(values)
        if not items:
            raise ValueError("in_() requires at least one value")
        return In(self, [to_expr(v) for v in items])

    def like(self, pattern: Any) -> Expr:
        return Binary("LIKE", self, to_expr(pattern))

    def ilike(self, pattern: Any) -> Expr:
        return Binary("ILIKE", self, to_expr(pattern))

    def filter_where(self, predicate: Expr) -> Expr:
        return FilterWhere(self, to_expr(predicate))

    def over(
        self,
        *,
        partition_by: Sequence[Expr] = (),
        order_by: Sequence[Expr | OrderTerm | str] = (),
        frame: str | None = None,
    ) -> Window:
        return Window(self, partition_by=partition_by, order_by=order_by, frame=frame)

    def __eq__(self, other: object) -> Expr:  # type: ignore[override]
        return Binary("=", self, to_expr(other))

    def __ne__(self, other: object) -> Expr:  # type: ignore[override]
        return Binary("<>", self, to_expr(other))

    def __lt__(self, other: object) -> Expr:
        return Binary("<", self, to_expr(other))

    def __le__(self, other: object) -> Expr:
        return Binary("<=", self, to_expr(other))

    def __gt__(self, other: object) -> Expr:
        return Binary(">", self, to_expr(other))

    def __ge__(self, other: object) -> Expr:
        return Binary(">=", self, to_expr(other))

    def __add__(self, other: object) -> Expr:
        return Binary("+", self, to_expr(other))

    def __radd__(self, other: object) -> Expr:
        return Binary("+", to_expr(other), self)

    def __sub__(self, other: object) -> Expr:
        return Binary("-", self, to_expr(other))

    def __rsub__(self, other: object) -> Expr:
        return Binary("-", to_expr(other), self)

    def __mul__(self, other: object) -> Expr:
        return Binary("*", self, to_expr(other))

    def __rmul__(self, other: object) -> Expr:
        return Binary("*", to_expr(other), self)

    def __truediv__(self, other: object) -> Expr:
        return Binary("/", self, to_expr(other))

    def __rtruediv__(self, other: object) -> Expr:
        return Binary("/", to_expr(other), self)

    def __and__(self, other: object) -> Expr:
        return Binary("AND", self, to_expr(other))

    def __or__(self, other: object) -> Expr:
        return Binary("OR", self, to_expr(other))

    def __invert__(self) -> Expr:
        return Prefix("NOT", self)

    def __bool__(self) -> bool:
        raise TypeError("SQL expressions cannot be used as Python booleans; combine with & | ~")


class OrderTerm:
    __slots__ = ("expr", "descending", "nulls_last")

    def __init__(
        self,
        expr: Expr,
        *,
        descending: bool = False,
        nulls_last: bool = False,
    ) -> None:
        self.expr = expr
        self.descending = descending
        self.nulls_last = nulls_last

    def compile(self, params: list[Any]) -> str:
        sql = self.expr.compile(params)
        if self.descending:
            sql += " DESC"
        if self.nulls_last:
            sql += " NULLS LAST"
        return sql


class Alias(Expr):
    __slots__ = ("expr", "alias")

    def __init__(self, expr: Expr, alias: str) -> None:
        quote_ident(alias)
        self.expr = expr
        self.alias = alias

    def compile(self, params: list[Any]) -> str:
        return f"{self.expr.compile(params)} AS {quote_ident(self.alias)}"

    def as_(self, alias: str) -> Alias:
        return Alias(self.expr, alias)


class Param(Expr):
    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def compile(self, params: list[Any]) -> str:
        params.append(self.value)
        return "?"


class Literal(Expr):
    """SQL fragment that is not a bound value (star, ordinals, allowed type names)."""

    __slots__ = ("sql",)

    def __init__(self, sql: str) -> None:
        self.sql = sql

    def compile(self, params: list[Any]) -> str:
        return self.sql


class Column(Expr):
    __slots__ = ("name", "table")

    def __init__(self, name: str, table: str | None = None) -> None:
        quote_ident(name)
        if table is not None:
            quote_ident(table)
        self.name = name
        self.table = table

    def compile(self, params: list[Any]) -> str:
        return qualify(self.table, self.name)

    def __repr__(self) -> str:
        return f"Column({qualify(self.table, self.name)})"


class Binary(Expr):
    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left: Expr, right: Expr) -> None:
        self.op = op
        self.left = left
        self.right = right

    def compile(self, params: list[Any]) -> str:
        return f"({self.left.compile(params)} {self.op} {self.right.compile(params)})"


class Prefix(Expr):
    __slots__ = ("op", "expr", "postfix")

    def __init__(self, op: str, expr: Expr, *, postfix: bool = False) -> None:
        self.op = op
        self.expr = expr
        self.postfix = postfix

    def compile(self, params: list[Any]) -> str:
        inner = self.expr.compile(params)
        if self.postfix:
            return f"({inner} {self.op})"
        return f"({self.op} {inner})"


class In(Expr):
    __slots__ = ("expr", "values")

    def __init__(self, expr: Expr, values: Sequence[Expr]) -> None:
        self.expr = expr
        self.values = list(values)

    def compile(self, params: list[Any]) -> str:
        inner = ", ".join(v.compile(params) for v in self.values)
        return f"({self.expr.compile(params)} IN ({inner}))"


class Call(Expr):
    __slots__ = ("name", "args", "star")

    def __init__(self, name: str, args: Sequence[Expr] = (), *, star: bool = False) -> None:
        if not _IDENT.match(name):
            raise ValueError(f"Invalid SQL function name: {name!r}")
        self.name = name
        self.args = list(args)
        self.star = star

    def compile(self, params: list[Any]) -> str:
        if self.star:
            return f"{self.name}(*)"
        inner = ", ".join(a.compile(params) for a in self.args)
        return f"{self.name}({inner})"


class FilterWhere(Expr):
    __slots__ = ("expr", "predicate")

    def __init__(self, expr: Expr, predicate: Expr) -> None:
        self.expr = expr
        self.predicate = predicate

    def compile(self, params: list[Any]) -> str:
        return f"{self.expr.compile(params)} FILTER (WHERE {self.predicate.compile(params)})"


class Window(Expr):
    __slots__ = ("expr", "partition_by", "order_by", "frame")

    def __init__(
        self,
        expr: Expr,
        *,
        partition_by: Sequence[Expr] = (),
        order_by: Sequence[Expr | OrderTerm | str] = (),
        frame: str | None = None,
    ) -> None:
        self.expr = expr
        self.partition_by = list(partition_by)
        self.order_by = [to_order(item) for item in order_by]
        if frame is not None and not frame.startswith("ROWS ") and not frame.startswith("RANGE "):
            raise ValueError(f"Unsupported window frame: {frame!r}")
        self.frame = frame

    def compile(self, params: list[Any]) -> str:
        bits: list[str] = []
        if self.partition_by:
            bits.append(
                "PARTITION BY " + ", ".join(p.compile(params) for p in self.partition_by)
            )
        if self.order_by:
            bits.append("ORDER BY " + ", ".join(o.compile(params) for o in self.order_by))
        if self.frame:
            bits.append(self.frame)
        return f"{self.expr.compile(params)} OVER ({' '.join(bits)})"


class Case(Expr):
    __slots__ = ("arms", "else_")

    def __init__(self, arms: Sequence[tuple[Expr, Expr]], else_: Expr | None = None) -> None:
        if not arms:
            raise ValueError("case() requires at least one (predicate, value) arm")
        self.arms = list(arms)
        self.else_ = else_

    def compile(self, params: list[Any]) -> str:
        chunks = ["CASE"]
        for pred, value in self.arms:
            chunks.append(f"WHEN {pred.compile(params)} THEN {value.compile(params)}")
        if self.else_ is not None:
            chunks.append(f"ELSE {self.else_.compile(params)}")
        chunks.append("END")
        return "(" + " ".join(chunks) + ")"


class Unnest:
    __slots__ = ("expr", "alias", "columns")

    def __init__(self, expr: Expr, *, alias: str, columns: Sequence[str]) -> None:
        quote_ident(alias)
        for col in columns:
            quote_ident(col)
        self.expr = expr
        self.alias = alias
        self.columns = tuple(columns)

    def compile_from(self, params: list[Any]) -> str:
        cols = ", ".join(quote_ident(c) for c in self.columns)
        return f"UNNEST({self.expr.compile(params)}) AS {quote_ident(self.alias)}({cols})"


def to_expr(value: Any) -> Expr:
    if isinstance(value, Expr):
        return value
    if isinstance(value, OrderTerm):
        raise TypeError("OrderTerm is not a value expression")
    return Param(value)


def to_order(value: Expr | OrderTerm | str | int) -> OrderTerm:
    if isinstance(value, OrderTerm):
        return value
    if isinstance(value, int):
        return OrderTerm(Literal(str(value)))
    if isinstance(value, str):
        quote_ident(value)
        return OrderTerm(Literal(quote_ident(value)))
    return OrderTerm(value)


def _is_seq(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray, Expr))


def col(name: str, table: str | None = None) -> Column:
    return Column(name, table=table)


def star() -> Literal:
    return Literal("*")


def coalesce(*values: Any) -> Call:
    if len(values) < 2:
        raise ValueError("coalesce() requires at least two arguments")
    return Call("coalesce", [to_expr(v) for v in values])


def trim(value: Any) -> Call:
    return Call("trim", [to_expr(value)])


def lower(value: Any) -> Call:
    return Call("lower", [to_expr(value)])


def abs_(value: Any) -> Call:
    return Call("abs", [to_expr(value)])


def round_(value: Any, digits: Any = 0) -> Call:
    return Call("round", [to_expr(value), to_expr(digits)])


def sum_(value: Any) -> Call:
    return Call("sum", [to_expr(value)])


def max_(value: Any) -> Call:
    return Call("max", [to_expr(value)])


def min_(value: Any) -> Call:
    return Call("min", [to_expr(value)])


def count(value: Any | None = None) -> Call:
    if value is None:
        return Call("count", star=True)
    return Call("count", [to_expr(value)])


def count_star() -> Call:
    return count()


class _CountDistinct(Expr):
    __slots__ = ("expr",)

    def __init__(self, expr: Expr) -> None:
        self.expr = expr

    def compile(self, params: list[Any]) -> str:
        return f"count(DISTINCT {self.expr.compile(params)})"


def count_distinct(value: Any) -> _CountDistinct:
    return _CountDistinct(to_expr(value))


def concat(*values: Any) -> Expr:
    if len(values) < 2:
        raise ValueError("concat() requires at least two arguments")
    acc = to_expr(values[0])
    for value in values[1:]:
        acc = Binary("||", acc, to_expr(value))
    return acc


def any_value(value: Any) -> Call:
    return Call("any_value", [to_expr(value)])


def rank() -> Call:
    return Call("rank")


def row_number() -> Call:
    return Call("row_number")


def regexp_extract(value: Any, pattern: Any, group: Any = 0) -> Call:
    return Call("regexp_extract", [to_expr(value), to_expr(pattern), to_expr(group)])


def try_cast(value: Any, sql_type: str) -> Call:
    kind = sql_type.strip().upper()
    if kind not in _SQL_TYPES:
        raise ValueError(f"Unsupported CAST type: {sql_type!r}")
    return _TryCast(to_expr(value), kind)


class _TryCast(Call):
    __slots__ = ("expr", "sql_type")

    def __init__(self, expr: Expr, sql_type: str) -> None:
        super().__init__("try_cast", [expr])
        self.expr = expr
        self.sql_type = sql_type

    def compile(self, params: list[Any]) -> str:
        return f"try_cast({self.expr.compile(params)} AS {self.sql_type})"


def range_(end: Any) -> Call:
    return Call("range", [to_expr(end)])


def unnest(expr: Any, *, alias: str, columns: Sequence[str]) -> Unnest:
    return Unnest(to_expr(expr), alias=alias, columns=columns)


def case(*arms: tuple[Any, Any], else_: Any = None) -> Case:
    parsed = [(to_expr(pred), to_expr(value)) for pred, value in arms]
    return Case(parsed, else_=None if else_ is None else to_expr(else_))


ROWS_CUMULATIVE = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"


def and_(*preds: Expr) -> Expr:
    if not preds:
        raise ValueError("and_() requires at least one predicate")
    acc = preds[0]
    for pred in preds[1:]:
        acc = acc & pred
    return acc


def or_(*preds: Expr) -> Expr:
    if not preds:
        raise ValueError("or_() requires at least one predicate")
    acc = preds[0]
    for pred in preds[1:]:
        acc = acc | pred
    return acc
