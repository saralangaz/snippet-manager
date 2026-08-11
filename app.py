"""Snippet Manager — standalone FastAPI app.

Owns its own data file. Fully isolated: no import or runtime dependency on
the Project Control Tower.
"""
import os
import re
import sys
import threading
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from seed_data import build_seed_data

HERE = os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts: str) -> str:
    """Path to a bundled, read-only asset (e.g. ui/).

    When frozen by PyInstaller, bundled data lives under sys._MEIPASS
    (onefile: a temp extraction dir; onedir: the app's resources folder).
    In dev, it's just next to this file.
    """
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, *parts)


def default_data_dir() -> str:
    """Where the writable snippets file lives.

    Packaged builds must NOT write inside the PyInstaller bundle (onefile's
    _MEIPASS is a temp dir wiped between runs; onedir's folder may not be
    writable/updatable). Use a per-user app-data directory instead. In dev,
    keep using ./data so the existing workflow / seeded file still works.
    """
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return os.path.expanduser("~/Library/Application Support/Snippet Manager")
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
            return os.path.join(appdata, "Snippet Manager")
        xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(xdg, "snippet-manager")
    return os.path.join(HERE, "data")


UI_DIR = resource_path("ui")
DATA_DIR = default_data_dir()

# Override with SNIPPET_MANAGER_DATA_FILE if you want the file elsewhere.
SNIPPETS_FILE = os.environ.get("SNIPPET_MANAGER_DATA_FILE") or os.path.join(DATA_DIR, "snippets.yaml")

# Known categories: id -> (label, icon). Used to auto-create a category with a
# sensible icon, and to suggest a category from a command's first word.
CATEGORY_DEFS = {
    "git": ("Git", "⎇"),
    "docker": ("Docker", "🐳"),
    "kubernetes": ("Kubernetes", "⎈"),
    "python": ("Python", "🐍"),
    "javascript": ("JavaScript / Node", "⬡"),
    "bash": ("Bash / Linux", "💻"),
    "api": ("API / cURL", "🌐"),
    "aws": ("AWS CLI", "▲"),
    "azure": ("Azure CLI", "◆"),
    "general": ("General", "▸"),
}

# Base-command -> category id, checked in order against the first word(s) of
# a pasted command.
COMMAND_CATEGORY_HINTS = [
    (re.compile(r"^(kubectl|helm)\b"), "kubernetes"),
    (re.compile(r"^docker(-compose)?\b"), "docker"),
    (re.compile(r"^git\b"), "git"),
    (re.compile(r"^(python3?|pip3?|pytest|venv|virtualenv|black|ruff|flake8|mypy|poetry)\b"), "python"),
    (re.compile(r"^(npm|npx|yarn|pnpm|node)\b"), "javascript"),
    (re.compile(r"^curl\b"), "api"),
    (re.compile(r"^aws\b"), "aws"),
    (re.compile(r"^az\b"), "azure"),
    (re.compile(r"^(find|grep|chmod|chown|ps|kill|df|du|lsof|awk|sed|tar|ssh|scp|rsync|xargs)\b"), "bash"),
]

# Fallback for commands whose first token isn't a recognizable tool name —
# shell builtins like `source`, `cd`, `export` never match the hints above.
# Scan the whole command for a category-defining keyword before giving up.
COMMAND_CATEGORY_KEYWORDS = [
    (re.compile(r"\b(venv|pip3?|pytest|activate|poetry|pyproject|requirements\.txt|virtualenv|django)\b"), "python"),
    (re.compile(r"\b(npm|npx|yarn|pnpm|node|package\.json)\b"), "javascript"),
    (re.compile(r"\b(kubectl|k8s|kubeconfig)\b"), "kubernetes"),
    (re.compile(r"\b(docker|dockerfile|container)\b"), "docker"),
    (re.compile(r"\b(git|commit|rebase|checkout)\b"), "git"),
    (re.compile(r"\bcurl\b|https?://"), "api"),
    (re.compile(r"\baws\b"), "aws"),
    (re.compile(r"\baz\b|\bazure\b"), "azure"),
]


def suggest_category_id(command: str) -> str:
    cmd = command.strip()
    for pattern, cat_id in COMMAND_CATEGORY_HINTS:
        if pattern.match(cmd):
            return cat_id
    for pattern, cat_id in COMMAND_CATEGORY_KEYWORDS:
        if pattern.search(cmd):
            return cat_id
    # Never fall back to an unhelpful literal "general" — bash is a
    # sensible default for anything shell-flavored we can't otherwise place.
    return "bash"


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "general"


def derive_title(command: str) -> str:
    # Strip {{params}} for a cleaner title, keep the raw command as the source of truth.
    stripped = re.sub(r"\{\{\w+\}\}", "", command).strip()
    stripped = re.sub(r"\s+", " ", stripped)
    if not stripped:
        stripped = command.strip()
    title = stripped[:60]
    return title[:1].upper() + title[1:] if title else "Untitled snippet"


def _ensure_file():
    os.makedirs(os.path.dirname(SNIPPETS_FILE), exist_ok=True)
    if not os.path.exists(SNIPPETS_FILE):
        with open(SNIPPETS_FILE, "w", encoding="utf-8") as f:
            yaml.dump(build_seed_data(), f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_snippets() -> dict:
    _ensure_file()
    with open(SNIPPETS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {"categories": []}
    # First-run-empty case: file exists (e.g. created by PCT) but has no snippets yet.
    total = sum(len(c.get("snippets", [])) for c in data.get("categories", []))
    if total == 0 and not data.get("categories"):
        data = build_seed_data()
        save_snippets(data)
    return data


def save_snippets(data: dict):
    # Write to a temp file and atomically swap it into place with os.replace,
    # instead of writing SNIPPETS_FILE directly. A direct write left half
    # finished by a crash/power loss/kill mid-dump leaves a truncated, corrupt
    # YAML file behind; os.replace is atomic at the filesystem level, so
    # readers only ever see the old complete file or the new complete one.
    tmp_path = f"{SNIPPETS_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, SNIPPETS_FILE)


# Guards every read-modify-write cycle against the data file. Without this,
# concurrent requests (e.g. a bulk import firing one POST per snippet) each
# load the same on-disk snapshot, mutate their own in-memory copy, and save
# it back — the last writer wins and silently clobbers everyone else's
# changes, which is exactly the kind of thing that corrupts the YAML mid-file.
_write_lock = threading.Lock()


def _find_duplicate(data: dict, command: str) -> dict | None:
    """Exact-match check across every category — same command text already
    saved anywhere counts as a duplicate, regardless of which category it's
    filed under."""
    norm = command.strip()
    for c in data.get("categories", []):
        for s in c["snippets"]:
            if s["command"].strip() == norm:
                return s
    return None


def _insert_snippet(data: dict, payload: "NewSnippet") -> dict | None:
    """Mutate `data` in place with one new snippet; caller saves once.

    Returns None (no-op) if the exact command is already saved — this also
    naturally dedupes within a single bulk batch, since each insert mutates
    `data` before the next one is checked.
    """
    command = payload.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")

    if _find_duplicate(data, command):
        return None

    categories = data.setdefault("categories", [])

    category_input = payload.category.strip()
    if category_input:
        cat_id = slugify(category_input)
        category = next(
            (c for c in categories if c["id"] == cat_id or c["label"].lower() == category_input.lower()),
            None,
        )
        if not category:
            default_label, default_icon = CATEGORY_DEFS.get(cat_id, (category_input, "▸"))
            category = {
                "id": cat_id,
                "label": category_input if category_input else default_label,
                "icon": default_icon,
                "snippets": [],
            }
            categories.append(category)
    else:
        cat_id = suggest_category_id(command)
        category = next((c for c in categories if c["id"] == cat_id), None)
        if not category:
            label, icon = CATEGORY_DEFS.get(cat_id, CATEGORY_DEFS["general"])
            category = {"id": cat_id, "label": label, "icon": icon, "snippets": []}
            categories.append(category)

    title = payload.title.strip() or derive_title(command)
    param_names = re.findall(r"\{\{(\w+)\}\}", command)
    params = [{"name": p, "placeholder": ""} for p in param_names]

    existing_ids = [s["id"] for c in categories for s in c["snippets"]]
    base_id = f"{category['id']}-{len(category['snippets']) + 1}"
    new_id = base_id
    counter = 1
    while new_id in existing_ids:
        new_id = f"{base_id}-{counter}"
        counter += 1

    snippet = {
        "id": new_id,
        "title": title,
        "command": command,
        "description": payload.description.strip(),
        "params": params,
    }
    category["snippets"].append(snippet)
    return snippet


class NewSnippet(BaseModel):
    command: str
    title: str = ""
    description: str = ""
    category: str = ""  # category id or free-typed label; blank = auto-suggest


class NewCategory(BaseModel):
    id: str
    label: str
    icon: str = "▸"


def create_app() -> FastAPI:
    app = FastAPI(title="Snippet Manager")

    @app.get("/api/snippets")
    def get_snippets():
        return load_snippets()

    @app.get("/api/suggest")
    def suggest(command: str = ""):
        cat_id = suggest_category_id(command)
        label, icon = CATEGORY_DEFS.get(cat_id, CATEGORY_DEFS["general"])
        return {
            "category_id": cat_id,
            "category_label": label,
            "icon": icon,
            "title": derive_title(command) if command.strip() else "",
        }

    @app.post("/api/snippets")
    def add_snippet(payload: NewSnippet):
        with _write_lock:
            data = load_snippets()
            snippet = _insert_snippet(data, payload)
            if snippet is None:
                raise HTTPException(status_code=409, detail="This command is already saved")
            save_snippets(data)
        return snippet

    @app.post("/api/snippets/bulk")
    def add_snippets_bulk(payload: list[NewSnippet]):
        # One read-modify-write for the whole batch — this is what a bulk
        # import/add actually needs; firing one POST per snippet in parallel
        # (as the review-list UI used to) races N unsynchronized writers
        # against the same file and corrupts it.
        with _write_lock:
            data = load_snippets()
            created, skipped = [], []
            for p in payload:
                snippet = _insert_snippet(data, p)
                if snippet is None:
                    skipped.append({"command": p.command.strip(), "title": p.title.strip()})
                else:
                    created.append(snippet)
            save_snippets(data)
        return {"created": created, "skipped": skipped}

    @app.delete("/api/snippets/{snippet_id}")
    def delete_snippet(snippet_id: str):
        with _write_lock:
            data = load_snippets()
            for cat in data["categories"]:
                orig = len(cat["snippets"])
                cat["snippets"] = [s for s in cat["snippets"] if s["id"] != snippet_id]
                if len(cat["snippets"]) < orig:
                    save_snippets(data)
                    return {"ok": True}
        raise HTTPException(status_code=404, detail="Snippet not found")

    @app.post("/api/categories")
    def add_category(payload: NewCategory):
        with _write_lock:
            data = load_snippets()
            if any(c["id"] == payload.id for c in data["categories"]):
                raise HTTPException(status_code=400, detail="Category ID already exists")
            data["categories"].append({"id": payload.id, "label": payload.label, "icon": payload.icon, "snippets": []})
            save_snippets(data)
        return {"ok": True}

    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
    return app


app = create_app()
