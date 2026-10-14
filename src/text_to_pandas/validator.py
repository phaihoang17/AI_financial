"""Static validation of generated Pandas code (ADR-069).

This is a pre-filter, not the security boundary: the isolated worker process
(``src/sandbox/pandas_worker.py``) with OS limits and audit guards is. The
validator rejects policy violations early (imports, dunder/private access,
dangerous builtins, I/O and string-evaluation APIs) and catches cheap,
repairable mistakes before execution (unknown names, columns or metric IDs, a
missing ``result``). The worker reruns the security subset on its own.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Set, Tuple

MAX_CODE_CHARS = 8000
ALLOWED_MODULES = {"pandas": "pd", "numpy": "np", "math": "math"}
PRELOADED_NAMES = frozenset({"pd", "np", "math"})
OUTPUT_NAMES = frozenset({"result", "evidence"})
SAFE_BUILTIN_NAMES = frozenset({
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "int", "isinstance", "iter", "len", "list", "map", "max", "min", "next", "pow", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
    "ArithmeticError", "AttributeError", "Exception", "IndexError", "KeyError", "LookupError",
    "StopIteration", "TypeError", "ValueError", "ZeroDivisionError",
})
FORBIDDEN_NAMES = frozenset({
    "__import__", "breakpoint", "bytearray", "callable", "classmethod", "compile", "copyright", "credits",
    "delattr", "dir", "eval", "exec", "exit", "getattr", "globals", "hasattr", "help", "id", "input",
    "license", "locals", "memoryview", "object", "open", "print", "property", "quit", "setattr",
    "staticmethod", "super", "type", "vars",
})
FORBIDDEN_ATTRIBUTES = frozenset({
    # string evaluation and global state
    "eval", "query", "options", "set_option", "get_option", "reset_option", "option_context",
    "describe_option", "register_dataframe_accessor", "register_series_accessor",
    # rendering / plotting (lazy imports, no output channel)
    "style", "plot", "hist", "boxplot",
    # file, process and package internals
    "io", "util", "testing", "load", "save", "savez", "savez_compressed", "savetxt", "loadtxt",
    "genfromtxt", "fromfile", "tofile", "memmap", "ctypeslib", "lib", "distutils", "f2py", "show_config",
    "DataSource", "system", "popen",
    # frame / code / generator introspection
    "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "gi_frame", "gi_code", "cr_frame",
    "co_code", "tb_frame", "tb_next", "func_globals",
})
ALLOWED_TO_ATTRIBUTES = frozenset({
    "to_dict", "to_list", "to_numpy", "to_frame", "to_records", "to_numeric", "to_datetime",
    "to_timedelta", "to_period", "to_timestamp", "to_series", "to_flat_index", "to_string",
})
_FORBIDDEN_NODES = (
    ast.ClassDef, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.With, ast.Global,
    ast.Nonlocal, ast.Await, ast.Yield, ast.YieldFrom,
)
SECURITY_CODES = frozenset({
    "FORBIDDEN_IMPORT", "FORBIDDEN_NAME", "FORBIDDEN_ATTRIBUTE", "PRIVATE_ATTRIBUTE", "FORBIDDEN_STATEMENT",
})


@dataclass(frozen=True)
class CodeIssue:
    code: str
    message: str
    line: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "line": self.line}


@dataclass(frozen=True)
class CodeValidationReport:
    issues: Tuple[CodeIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def security_violation(self) -> bool:
        return any(issue.code in SECURITY_CODES for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}


def _line(node: ast.AST) -> Optional[int]:
    return getattr(node, "lineno", None)


def _string_constant(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _subscript_key(node: ast.AST) -> Optional[str]:
    return _string_constant(node.slice) if isinstance(node, ast.Subscript) else None


def _assigned_names(tree: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.Lambda)):
            if isinstance(node, ast.FunctionDef):
                names.add(node.name)
            arguments = node.args
            for argument in arguments.posonlyargs + arguments.args + arguments.kwonlyargs:
                names.add(argument.arg)
            for argument in (arguments.vararg, arguments.kwarg):
                if argument is not None:
                    names.add(argument.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _assigns_result(body: Sequence[ast.stmt]) -> bool:
    for statement in body:
        targets: List[ast.AST] = []
        if isinstance(statement, ast.Assign):
            targets = list(statement.targets)
        elif isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
            targets = [statement.target]
        for target in targets:
            for node in ast.walk(target):
                if isinstance(node, ast.Name) and node.id == "result":
                    return True
        if isinstance(statement, (ast.If, ast.For, ast.While, ast.Try)):
            blocks = [statement.body, getattr(statement, "orelse", [])]
            if isinstance(statement, ast.Try):
                blocks += [handler.body for handler in statement.handlers] + [statement.finalbody]
            if any(_assigns_result(block) for block in blocks):
                return True
    return False


def _metric_literals(node: ast.AST) -> List[Tuple[str, int]]:
    """String literals compared against a ``[...]["metric"]`` column."""
    found: List[Tuple[str, int]] = []
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        if any(_subscript_key(operand) == "metric" for operand in operands):
            for operand in operands:
                value = _string_constant(operand)
                if value is not None:
                    found.append((value, _line(node) or 0))
                elif isinstance(operand, (ast.List, ast.Tuple, ast.Set)):
                    found += [(item.value, _line(node) or 0) for item in operand.elts
                              if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "isin"
        and _subscript_key(node.func.value) == "metric"
        and node.args
        and isinstance(node.args[0], (ast.List, ast.Tuple, ast.Set))
    ):
        found += [(item.value, _line(node) or 0) for item in node.args[0].elts
                  if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    return found


def validate_pandas_code(
    code: str,
    *,
    datasets: Mapping[str, Sequence[str]],
    known_metrics: Optional[Collection[str]] = None,
    security_only: bool = False,
) -> CodeValidationReport:
    """Return every issue found; an empty report means the code may be executed."""
    issues: List[CodeIssue] = []
    if not isinstance(code, str) or not code.strip():
        return CodeValidationReport((CodeIssue("EMPTY_CODE", "code is empty", None),))
    if len(code) > MAX_CODE_CHARS:
        return CodeValidationReport((CodeIssue("CODE_TOO_LONG", f"code exceeds {MAX_CODE_CHARS} characters", None),))
    try:
        tree = ast.parse(code, filename="<generated>", mode="exec")
    except SyntaxError as error:
        return CodeValidationReport((CodeIssue("SYNTAX_ERROR", f"{error.msg}", error.lineno),))

    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN_NODES):
            issues.append(CodeIssue("FORBIDDEN_STATEMENT", f"{type(node).__name__} is not allowed", _line(node)))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_MODULES:
                    issues.append(CodeIssue("FORBIDDEN_IMPORT", f"import of {alias.name} is not allowed", _line(node)))
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_MODULES:
                issues.append(CodeIssue("FORBIDDEN_IMPORT", f"import from {node.module} is not allowed", _line(node)))
            for alias in node.names:
                if alias.name.startswith("_") or alias.name in FORBIDDEN_ATTRIBUTES or alias.name == "*" or (
                    alias.name.startswith(("read_", "to_")) and alias.name not in ALLOWED_TO_ATTRIBUTES
                ):
                    issues.append(CodeIssue("FORBIDDEN_IMPORT", f"import of {alias.name} is not allowed", _line(node)))
        elif isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id in FORBIDDEN_NAMES:
                issues.append(CodeIssue("FORBIDDEN_NAME", f"name {node.id} is not allowed", _line(node)))
        elif isinstance(node, ast.Attribute):
            attribute = node.attr
            if attribute.startswith("_"):
                issues.append(CodeIssue("PRIVATE_ATTRIBUTE", f"attribute {attribute} is not allowed", _line(node)))
            elif attribute in FORBIDDEN_ATTRIBUTES or attribute.startswith("read_") or (
                attribute.startswith("to_") and attribute not in ALLOWED_TO_ATTRIBUTES
            ):
                issues.append(CodeIssue("FORBIDDEN_ATTRIBUTE", f"attribute {attribute} is not allowed", _line(node)))
        elif isinstance(node, ast.FunctionDef) and node.decorator_list:
            issues.append(CodeIssue("FORBIDDEN_STATEMENT", "decorators are not allowed", _line(node)))
    if security_only:
        return CodeValidationReport(tuple(issues))

    assigned = _assigned_names(tree)
    reported: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if (
                name in assigned or name in SAFE_BUILTIN_NAMES or name in datasets
                or name in PRELOADED_NAMES or name in FORBIDDEN_NAMES or name.startswith("__")
                or name in reported
            ):
                continue
            reported.add(name)
            issues.append(CodeIssue(
                "UNKNOWN_NAME",
                f"name {name} is not defined; available DataFrames: {', '.join(sorted(datasets))}",
                _line(node),
            ))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in datasets \
                and node.value.id not in assigned:
            column = _subscript_key(node)
            if column is not None and column not in datasets[node.value.id]:
                issues.append(CodeIssue(
                    "UNKNOWN_COLUMN",
                    f"{node.value.id} has no column {column!r}; columns: {', '.join(datasets[node.value.id])}",
                    _line(node),
                ))
        if known_metrics is not None:
            for value, line in _metric_literals(node):
                if value not in known_metrics:
                    issues.append(CodeIssue(
                        "UNKNOWN_METRIC",
                        f"{value!r} is not a metric id; use the metrics dictionary (ratios must be computed "
                        "from their component metrics)",
                        line,
                    ))
    if not _assigns_result(tree.body):
        issues.append(CodeIssue("MISSING_RESULT", "the code must assign the final answer to `result`", None))
    return CodeValidationReport(tuple(issues))
