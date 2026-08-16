"""Declared configuration vs. actual source.

Some failures leave no traceback because nothing crashed. Scenario 03 is the
clean case: the probe targets a path and port the app never serves, so the
process is healthy and the logs are silent. The bug exists only in the
comparison between what the manifest declares and what the code implements.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Flask decorators that register a route. `route` takes methods= as a kwarg;
# the rest encode the verb in the attribute name.
ROUTE_DECORATORS = {"route", "get", "post", "put", "patch", "delete"}


@dataclass
class Route:
    path: str
    method: str
    func: str
    file: str      # repo-relative, POSIX
    line: int


def extract_routes(source_root: str) -> list[Route]:
    """Walk every .py file under source_root and collect Flask routes.

    Uses the ast module rather than regex: a decorator in a docstring or
    comment is structurally absent from the tree, so it cannot produce a
    false route, and every node carries its own line number for citation.
    """
    routes: list[Route] = []
    root = Path(source_root)

    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        rel = py.as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                r = _route_from_decorator(dec, node, rel)
                if r:
                    routes.append(r)

    return routes


def _route_from_decorator(dec, func_node, rel_path: str) -> Route | None:
    """Turn `@app.get("/readyz")` into a Route, or return None."""
    if not isinstance(dec, ast.Call):
        return None
    if not isinstance(dec.func, ast.Attribute):
        return None
    if dec.func.attr not in ROUTE_DECORATORS:
        return None
    if not dec.args or not isinstance(dec.args[0], ast.Constant):
        return None
    if not isinstance(dec.args[0].value, str):
        return None

    method = dec.func.attr.upper()
    if method == "ROUTE":
        method = "GET"
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [
                    e.value for e in kw.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                if methods:
                    method = methods[0].upper()

    return Route(
        path=dec.args[0].value,
        method=method,
        func=func_node.name,
        file=rel_path,
        line=func_node.lineno,
    )


from agent.models import CodeChunk, CodeContext, RetrievalStrategy


def _probe_targets(spec: dict) -> list[dict]:
    """Every probe declared across all containers, flattened."""
    out = []
    for c in spec.get("containers", []):
        declared_ports = [
            p.get("containerPort") for p in (c.get("ports") or [])
            if p.get("containerPort") is not None
        ]
        for kind in ("readinessProbe", "livenessProbe"):
            probe = c.get(kind)
            if not probe:
                continue
            out.append({
                "kind": kind,
                "container": c.get("name"),
                "path": probe.get("path"),
                "port": probe.get("port"),
                "declared_ports": declared_ports,
            })
    return out


def check_probes(context: dict, code: CodeContext) -> CodeContext:
    """Compare declared probe targets against ports and routes in source.

    Two mismatches, checked separately because they have different evidence:
    the port is verifiable against the manifest alone, while the path exists
    only as a decorator in the source and requires retrieval to see.
    """
    spec = context.get("workload_spec") or {}
    probes = _probe_targets(spec)

    if not probes:
        code.notes.append("no probes declared; nothing to cross-check")
        return code

    if not code.repo_path:
        code.notes.append("probes declared but no repo resolved; path check skipped")
        return code

    routes = extract_routes(code.repo_path)
    route_paths = {r.path for r in routes}
    fired = False

    for p in probes:
        # Port: manifest-only check, no source needed.
        if p["port"] is not None and p["declared_ports"]:
            if p["port"] not in p["declared_ports"]:
                code.notes.append(
                    f"{p['kind']} targets port {p['port']} but container "
                    f"{p['container']} declares {p['declared_ports']}"
                )
                fired = True

        # Path: only visible against source.
        if p["path"] and p["path"] not in route_paths:
            near = sorted(route_paths)
            code.notes.append(
                f"{p['kind']} targets path {p['path']} which is not a route "
                f"in the source; defined routes are {near}"
            )
            fired = True
            for r in routes:
                chunk = _chunk_for_route(r, code.repo_path)
                if chunk:
                    code.chunks.append(chunk)

    if fired and RetrievalStrategy.MANIFEST_SOURCE not in code.strategies_fired:
        code.strategies_fired.append(RetrievalStrategy.MANIFEST_SOURCE)
    if not fired:
        code.notes.append("all probe targets match declared ports and source routes")

    return code


def _chunk_for_route(route: Route, repo_path: str) -> CodeChunk | None:
    """Read the handler body so a diagnosis can cite the real route."""
    p = Path(route.file)
    if not p.is_file():
        return None
    lines = p.read_text(encoding="utf-8").splitlines()
    start = max(1, route.line - 1)          # include the decorator
    end = min(len(lines), route.line + 4)
    return CodeChunk(
        path=p.as_posix(),
        start_line=start,
        end_line=end,
        content="\n".join(lines[start - 1:end]),
        symbol=route.func,
        strategy=RetrievalStrategy.MANIFEST_SOURCE,
    )
