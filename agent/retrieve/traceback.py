"""Traceback → exact file and line.

The most precise retrieval strategy we have. When a Python traceback is
in the logs, it names the file, the line, and the function outright — no
guessing, no similarity scoring. This runs first for exactly that reason.

The one judgement call: a traceback's *last* frame is usually inside a
library or the standard library, because that's where the error finally
raised. That frame is true but useless — nobody patches json/__init__.py.
We want the deepest frame that still belongs to the application, which is
the line a human would actually point at.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from agent.models import CodeChunk, CodeContext, RetrievalStrategy

# Kubernetes prefixes every log line with an RFC3339 timestamp.
K8S_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s?")

# A traceback frame:   File "/app/main.py", line 83, in load_feature_flags
FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')

# The final line:      json.decoder.JSONDecodeError: Expecting value: ...
EXC_LINE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Warning)):\s*(?P<msg>.*)")

# Frames we never want to cite, even if they're the deepest.
VENDOR_MARKERS = ("site-packages", "dist-packages", "/usr/local/lib/python", "/usr/lib/python")

# How many lines of source to pull around the offending line.
CONTEXT_LINES = 12


class Frame(NamedTuple):
    file: str
    line: int
    func: str


def _strip_timestamp(text: str) -> str:
    return K8S_TIMESTAMP.sub("", text)


def _is_vendor(path: str) -> bool:
    return any(m in path for m in VENDOR_MARKERS)


def parse_tracebacks(lines: list[dict[str, Any]]) -> list[tuple[list[Frame], str, str]]:
    """Pull every traceback out of the log.

    Returns a list of (frames, exception_type, exception_message).
    Ordered as found, so the last one is the most recent.
    """
    found = []
    frames: list[Frame] = []
    inside = False

    for raw in lines:
        text = _strip_timestamp(raw.get("text", ""))

        if "Traceback (most recent call last):" in text:
            inside = True
            frames = []
            continue

        if not inside:
            continue

        m = FRAME.match(text)
        if m:
            frames.append(Frame(m.group("file"), int(m.group("line")), m.group("func")))
            continue

        # Source echo lines and caret markers sit between frames; skip them.
        if text.startswith(("    ", "\t")) or not text.strip():
            continue

        m = EXC_LINE.match(text.strip())
        if m and frames:
            found.append((frames, m.group("type"), m.group("msg")))
            inside = False
            frames = []

    return found


def pick_app_frame(frames: list[Frame]) -> Frame | None:
    """Deepest frame that isn't library code.

    Walk backwards from the raise site until we find application code.
    If everything is vendored, we have nothing worth citing.
    """
    for frame in reversed(frames):
        if not _is_vendor(frame.file):
            return frame
    return None


def map_to_repo(container_path: str, repo_path: str) -> Path | None:
    """Translate a container path onto a path on disk.

    The app reports /app/main.py — that's where the file lives *inside the
    container*, per the Dockerfile's WORKDIR. On disk it's at the resolved
    source root. We match on filename rather than trying to reconstruct the
    Dockerfile's layout, which we can't see from here.
    """
    filename = Path(container_path).name
    root = Path(repo_path)

    direct = root / filename
    if direct.is_file():
        return direct

    matches = list(root.rglob(filename))
    return matches[0] if len(matches) == 1 else None


def read_chunk(path: Path, line: int, repo_root: Path) -> CodeChunk:
    """Read a window of source around the offending line."""
    source = path.read_text(encoding="utf-8").splitlines()

    start = max(1, line - CONTEXT_LINES // 2)
    end = min(len(source), line + CONTEXT_LINES // 2)

    return CodeChunk(
        path=path.relative_to(repo_root).as_posix(),
        start_line=start,
        end_line=end,
        content="\n".join(source[start - 1:end]),
    )


def retrieve(context: dict[str, Any], code: CodeContext) -> CodeContext:
    """Add traceback-derived code to an already-resolved CodeContext.

    Takes the CodeContext from resolve.py. Every failure path adds a note
    and returns the object unchanged rather than raising — Phase 3 always
    gets something readable.
    """
    lines = ((context.get("logs") or {}).get("lines")) or []
    tracebacks = parse_tracebacks(lines)

    if not tracebacks:
        code.notes.append("no Python traceback found in logs")
        return code

    frames, exc_type, exc_msg = tracebacks[-1]
    code.notes.append(f"traceback found: {exc_type}: {exc_msg}")

    frame = pick_app_frame(frames)
    if frame is None:
        code.notes.append("traceback contains only library frames; nothing to cite")
        return code

    if not code.repo_path:
        code.notes.append(
            f"traceback points at {frame.file}:{frame.line} "
            "but no repo is resolved, so source is unavailable"
        )
        return code

    on_disk = map_to_repo(frame.file, code.repo_path)
    if on_disk is None:
        code.notes.append(f"could not locate {frame.file} under {code.repo_path}")
        return code

    code.chunks.append(read_chunk(on_disk, frame.line, Path(code.repo_path)))
    if RetrievalStrategy.TRACEBACK not in code.strategies_fired:
        code.strategies_fired.append(RetrievalStrategy.TRACEBACK)
    code.notes.append(
        f"cited {Path(frame.file).name}:{frame.line} in {frame.func}() "
        f"(deepest application frame)"
    )
    return code