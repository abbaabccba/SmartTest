from core.async_tasks import TaskCancelled
from dataclasses import dataclass
from datetime import datetime
import re
from .project_rules import BASIC_INFORMATION_RULE
from core.product_lines import PRODUCT_LINES, ProductLine


@dataclass(frozen=True)
class ProjectLocation:
    status_page_id: str
    project_id: str
    name: str
    status_url: str
    home_url: str
    space_key: str = ""
    page_identity: str = ""


PAGE_ALIASES = (
    (BASIC_INFORMATION_RULE.output_key, re.compile(
        re.escape(BASIC_INFORMATION_RULE.source_field).replace("\\ ", r"\s*") + r"\s*$", re.I,
    )),
    ("status", re.compile(r"(?:project\s*)?status\s*report\s*$", re.I)),
    ("test_information", re.compile(r"test\s*information\s*$", re.I)),
    ("wifi_test_plan", re.compile(r"wifi\s*test\s*plan\s*$", re.I)),
    ("test_plan", re.compile(r"test\s*plan\s*$", re.I)),
    ("environment", re.compile(r"test\s*environment(?:\s*setup\s*(?:and|&)\s*precautions)?\s*$", re.I)),
    ("experience", re.compile(r"(?:summary\s*of\s*)?experience\s*(?:and|&)\s*typical\s*cases\s*$", re.I)),
    ("report_store", re.compile(r"test\s*report\s*store\s*$", re.I)),
)

def canonical_page_kind(title: str) -> str | None:
    return _page_kind_and_prefix(title)[0]


def _page_kind_and_prefix(title):
    clean = re.sub(r"^[\d\s.★*#_-]+", "", str(title or "").strip())
    clean = re.sub(r"\s+", " ", clean)
    for kind, pattern in PAGE_ALIASES:
        if pattern.fullmatch(clean):
            return kind, ""
        separated = re.fullmatch(r"(.*\S)\s*[-–—:]\s*(.+)", clean)
        if separated and pattern.fullmatch(separated.group(2)):
            return kind, separated.group(1).strip()
    return None, ""

def _commercial_year(value):
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    day_first = re.fullmatch(
        r"(?P<day>\d{1,2}) (?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
        r"(?P<year>\d{4})",
        clean,
        re.IGNORECASE,
    )
    if day_first:
        month = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }[day_first.group("month").casefold()]
        try:
            return datetime(
                int(day_first.group("year")), month, int(day_first.group("day")),
            ).year
        except ValueError:
            return None
    for pattern in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
        "%b %d, %Y", "%B %d, %Y",
    ):
        try:
            return datetime.strptime(clean, pattern).year
        except ValueError:
            pass
    return None


_PROJECT_NAME_PREFIX = re.compile(
    r"^\s*(?:(?:[★☆*#_~]+\s*)|(?:\d+(?:\s*[.)、:：-]\s*|\s+)))+",
)
_PROJECT_NAME_SUFFIX = re.compile(
    r"\s*(?:[-–—:：]\s*)?\**\s*"
    r"(?:Basic\s+Information|Project\s+Status\s+Report)\s*\**\s*$",
    re.IGNORECASE,
)


def canonical_project_name(title, project_id=""):
    """Derive a UI name without changing the Confluence audit target title."""
    value = str(title or "").strip()
    value = _PROJECT_NAME_PREFIX.sub("", value)
    value = _PROJECT_NAME_SUFFIX.sub("", value).strip(" \t-–—:：")
    value = re.sub(r"\s+", " ", value)
    return value or str(project_id)


def _normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


_PROJECT_GRAPH_MAX_DEPTH = 4
_PROJECT_GRAPH_MAX_NODES = 100


def discover_project_pages(client, project: ProjectLocation, *, return_errors=False, return_context=False,
                           resolved_entry_page_id="", resolved_root_page_id=""):
    """Discover audited pages within the owning project's child-page graph."""
    home = (client.get_page(resolved_root_page_id) if resolved_root_page_id
            else client.get_page_by_url(project.home_url))
    candidates = {}
    errors = {}
    graph_root = home
    if not resolved_root_page_id and canonical_page_kind(home.title) == "status":
        try:
            graph_root = client.get_parent_page(home.id) or home
        except TaskCancelled:
            raise
        except Exception as exc:
            errors["branch:project_root"] = _issue(
                type(exc).__name__,
                f"pageId={home.id}; title={_safe_title(home.title)}",
            )

    queue = [(graph_root, 0, [str(graph_root.id)])]
    visited = set()
    candidate_paths = {}
    while queue and len(visited) < _PROJECT_GRAPH_MAX_NODES:
        page, depth, path = queue.pop(0)
        page_id = str(page.id)
        if not page_id or page_id in visited:
            continue
        visited.add(page_id)
        kind, prefix = _page_kind_and_prefix(page.title)
        if kind:
            kind_candidates = candidates.setdefault(kind, [])
            if all(existing.id != page.id for existing, _ in kind_candidates):
                kind_candidates.append((page, prefix))
                candidate_paths[str(page.id)] = path

        if depth >= _PROJECT_GRAPH_MAX_DEPTH:
            continue
        try:
            children = client.get_page_children(page.id)
        except TaskCancelled:
            raise
        except Exception as exc:
            error_kind = kind or f"branch:{page_id}"
            errors[error_kind] = _issue(
                type(exc).__name__,
                f"pageId={page_id}; title={_safe_title(page.title)}",
            )
        else:
            queue.extend((child, depth + 1, [*path, str(child.id)]) for child in children)

    pages = {}
    identity_tokens = _project_identity_tokens(project)
    for kind, kind_candidates in candidates.items():
        ranked = [
            (page, _project_identity_match_level(prefix, identity_tokens))
            for page, prefix in kind_candidates
        ]
        best_level = max(level for _, level in ranked)
        matching = [page for page, level in ranked if level == best_level]
        if best_level and len(matching) == 1:
            pages[kind] = matching[0]
            continue
        error_type = "AmbiguousPage" if best_level else "ForeignProjectPage"
        candidate_ids = sorted(page.id for page, _ in kind_candidates)
        errors[kind] = _issue(
            error_type, f"candidate pageIds={','.join(candidate_ids)}",
        )

    if return_context:
        return pages, errors, {
            "entry_page_id": str(resolved_entry_page_id or home.id), "root_page_id": str(graph_root.id),
            "page_paths": {kind: candidate_paths[str(page.id)] for kind, page in pages.items()},
        }
    return (pages, errors) if return_errors else pages


def locate_basic_information(client, project: ProjectLocation, *, resolved_entry_page_id="",
                             resolved_root_page_id="", discovered=None):
    """Single owner for Basic Information location and its discovery metadata."""
    if discovered is None:
        pages, errors, context = discover_project_pages(
            client, project, return_errors=True, return_context=True,
            resolved_entry_page_id=resolved_entry_page_id,
            resolved_root_page_id=resolved_root_page_id,
        )
    else:
        pages, errors, context = discovered
    page = pages.get("basic")
    error = errors.get("basic", BASIC_INFORMATION_RULE.failure_semantic) if page is None else ""
    return page, error, context


def _page_identity(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _issue(error_type, response_summary):
    return f"{error_type}|{response_summary}"


def _safe_title(value):
    return re.sub(r"[\x00-\x1f<>]", " ", str(value or "")).strip()[:160]


def _project_identity_tokens(project):
    values = (
        project.status_page_id,
        project.project_id,
        project.name,
        canonical_project_name(project.name, project.project_id),
    )
    return {
        token
        for value in values
        for token in (
            _page_identity(value),
            *(
                _page_identity(match)
                for match in re.findall(
                    r"[A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9-]*",
                    str(value or ""),
                )
            ),
        )
        if token
    }


def _project_identity_match_level(prefix, identity_tokens):
    if not prefix:
        return 1
    prefix_token = _page_identity(prefix)
    if not prefix_token:
        return 0
    if any(prefix_token == token or prefix_token in token for token in identity_tokens):
        return 2
    if any(
        prefix_token.endswith(token) and len(prefix_token) == len(token) + 1
        for token in identity_tokens
    ):
        return 1
    return 0
