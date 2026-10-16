from __future__ import annotations

from copy import deepcopy
from concurrent.futures import Future, as_completed
from datetime import datetime, timezone
import hashlib
import json
from urllib.parse import parse_qs, urljoin, urlsplit
import re
from time import perf_counter

from core.logging import smart_log
from core.product_lines import PRODUCT_LINES
from .project_discovery import ProjectLocation, _commercial_year, canonical_project_name, discover_project_pages, locate_basic_information
from .html import html_tables, links, table_attributes, table_field_html, table_fields, text
from .role_parser import extract_project_roles, extract_role_people, resolve_role_display_names
from .project_rules import CANONICAL_PROJECT_FIELDS, ROLE_LABELS, WIFI_ROLE_LABELS, normalize_project_field


SCHEMA_VERSION = 2
ROLE_PARSER_VERSION = 4
PRODUCT_SPACE_FACET = "__product_space__"
PROJECT_SPACE_FACET_DEFINITIONS = (
    (PRODUCT_SPACE_FACET, "Product Space"),
    ("page", "Page"),
    ("date of commercial approval", "Date of Commercial approval"),
    ("project id", "Project ID"),
    ("odm", "ODM"),
    ("oem/operator", "OEM/Operator"),
    ("key part number", "Key Part Number"),
    ("project status", "Project Status"),
    ("current stage", "Current Stage"),
    ("major pm", "Major PM"),
    ("project owner", "Project Owner"),
    ("support mode", "Support Mode"),
    ("launch os", "Launch OS"),
    ("date of kick off", "Date of Kick Off"),
    ("planned closure", "planned closure"),
    ("actual closure", "actual closure"),
    ("mp time", "MP Time"),
    ("launch time", "Launch Time"),
    ("next target", "Next Target"),
    ("next target date", "Next Target Date"),
    ("sum", "Sum"),
)
PROJECT_SPACE_FILTER_FIELDS = tuple(key for key, _label in PROJECT_SPACE_FACET_DEFINITIONS)


class CatalogParseError(RuntimeError):
    def __init__(self, reason, *, row=0):
        super().__init__('remote_unavailable')
        self.reason = reason
        self.row = row


def refresh_project_catalogs(client, store, product_lines=PRODUCT_LINES, *, now=None, manager=None):
    """Fetch only independent Product Space catalogs and publish them atomically."""
    now = now or datetime.now(timezone.utc)
    previous = store.load() or {"projects": []}
    old_by_id = {row["identity"]: row for row in previous["projects"]}
    total_started = perf_counter()

    def fetch(index_line):
        index, line = index_line
        request_started = perf_counter()
        phase = 'request'
        try:
            source = client.get_page_by_url(line.confluence_url)
            request_ms = round((perf_counter() - request_started) * 1000, 3)
            parse_started = perf_counter()
            phase = 'parse'
            rows = _catalog_rows(source, line.confluence_space_key)
            smart_log("Confluence catalog stage timing", domain="framework", source="confluence_catalog", emit_runtime_event=False,
                      extra={"stage": "catalog.line", "space_key": line.confluence_space_key, "duration_ms": request_ms,
                             "parse_duration_ms": round((perf_counter() - parse_started) * 1000, 3), "project_count": len(rows), "outcome": "success"})
            return index, line, source, rows, None
        except Exception as exc:
            response = getattr(exc, 'response', None)
            smart_log("Confluence catalog stage timing", level="error", domain="framework", source="confluence_catalog", emit_runtime_event=False,
                      extra={"stage": "catalog.line", "space_key": line.confluence_space_key,
                             "duration_ms": round((perf_counter() - request_started) * 1000, 3),
                             "project_count": 0, "outcome": "failure", "exception_type": type(exc).__name__,
                             "phase": phase, "attempt": 1,
                             "http_status": getattr(exc, 'status_code', None) or getattr(response, 'status_code', None),
                             "reason": exc.reason if isinstance(exc, CatalogParseError) else '',
                             "row": exc.row if isinstance(exc, CatalogParseError) else 0})
            return index, line, None, [], exc

    fetched = {}

    def build_snapshot(*, phase):
        projects = []
        sources = []
        stage_domains = {}
        discrepancies = set()
        seen = set()
        inaccessible_spaces = set()
        completed_spaces = set()
        for index, line, source, rows, error in sorted(fetched.values()):
            completed_spaces.add(line.confluence_space_key)
            if error:
                if not _is_access_denied(error):
                    raise error
                inaccessible_spaces.add(line.confluence_space_key)
                continue
            evidence = _page_evidence(source, line.confluence_space_key)
            evidence["display_name"] = line.name
            sources.append(evidence)
            for source_catalog in rows:
                catalog = deepcopy(source_catalog)
                catalog["catalog_source"] = evidence
                discrepancies.update(catalog.pop("discrepancies", ()))
                seen.add(catalog["identity"])
                stage = catalog.get("fields", {}).get("current stage", "")
                if stage and stage not in stage_domains.setdefault(line.confluence_space_key, []):
                    stage_domains[line.confluence_space_key].append(stage)
                old = old_by_id.get(catalog["identity"])
                if old and old.get("catalog_fingerprint") == catalog["catalog_fingerprint"]:
                    row = deepcopy(old); row.update(catalog); row.update(active=True)
                else:
                    row = {**catalog, "roles": {label: [] for label in ROLE_LABELS},
                           "active": True, "status": "catalog_ready", "error": None,
                           "detail_source": None, "updated_at": now.isoformat()}
                projects.append(row)
        for identity, old in old_by_id.items():
            space_key = old.get("space_key")
            if space_key not in completed_spaces:
                projects.append(deepcopy(old))
            elif identity not in seen and space_key not in inaccessible_spaces:
                row = deepcopy(old); row.update(active=False, status="inactive", updated_at=now.isoformat())
                projects.append(row)
        projects.sort(key=lambda row: (row["project_id"].casefold(), row["identity"].casefold()))
        completed = len(fetched)
        return {"schema_version": SCHEMA_VERSION, "source": "Confluence Product Line Project Spaces",
                "updated_at": now.isoformat(), "sources": sources,
                "complete_spaces": sorted(completed_spaces),
                "field_discrepancies": sorted(discrepancies, key=str.casefold),
                "projects": projects, "stage_domains": stage_domains, "phase": phase,
                "catalog_progress": {"completed": completed,
                                     "pending": len(product_lines) - completed,
                                     "total": len(product_lines)}}

    def submit(item):
        if manager is not None:
            return manager.submit(f"confluence-catalog:{item[1].confluence_space_key}", lambda _token, _progress: fetch(item))
        future = Future()
        try:
            future.set_result(fetch(item))
        except Exception as error:
            future.set_exception(error)
        return future

    futures = [submit(item) for item in enumerate(product_lines, start=1)]
    for future in as_completed(futures):
        result = future.result()
        fetched[result[0]] = result
        snapshot = build_snapshot(
            phase="catalog_ready" if len(fetched) == len(product_lines) else "catalog_loading",
        )
        store.save(snapshot)
    smart_log("Confluence catalog stage timing", domain="framework", source="confluence_catalog", emit_runtime_event=False,
              extra={"stage": "catalog.total", "duration_ms": round((perf_counter() - total_started) * 1000, 3),
                     "space_count": len(product_lines), "project_count": len(snapshot["projects"])})
    return snapshot


def extract_project_detail(client, original, *, now=None, resolved_names=None):
    """Fetch and map one catalog project into one complete current-state payload."""
    now = now or datetime.now(timezone.utc)
    row = deepcopy(original)
    names = resolved_names if resolved_names is not None else {}
    total_started = perf_counter()
    if row.get("detail_source") and row.get("status") == "current" and _current_role_parser(row):
        resolve_role_display_names(client, row.get("roles", {}), names)
        return row
    project = ProjectLocation(
        status_page_id=row.get("page_id", ""), project_id=row["project_id"],
        name=" ".join(filter(None, (row["name"], row.get("page_id", "")))),
        status_url=row["page_url"], home_url=row["page_url"],
        space_key=row["space_key"], page_identity=row["project_id"],
    )
    discovery_started = perf_counter()
    discovered = discover_project_pages(
        client, project,
        return_errors=True, return_context=True,
        resolved_entry_page_id=row.get("entry_page_id", "") if not row.get("page_id") else "",
        resolved_root_page_id=row.get("root_page_id", "") if not row.get("page_id") else "",
    )
    smart_log("Confluence detail stage timing", domain="framework", source="confluence_detail", emit_runtime_event=False,
              extra={"stage": "detail.discovery", "duration_ms": round((perf_counter() - discovery_started) * 1000, 3),
                     "page_count": len(discovered[0]), "error_count": len(discovered[1])})
    metadata, error, context = locate_basic_information(client, project, discovered=discovered)
    if metadata is None:
        raise LookupError(error)
    basic_started = perf_counter()
    detail = client.get_page(metadata.id)
    basic_ms = round((perf_counter() - basic_started) * 1000, 3)
    pages, _errors, _context = discovered
    pages["basic"] = detail
    parse_started = perf_counter()
    basic_body = detail.body or detail.view_body
    roles = extract_project_roles(basic_body)
    fields = dict(row.get("fields", {}))
    basic_fields = table_fields(basic_body)
    fields["launch os"] = basic_fields.get("launch os", "")
    fields["wifi module"] = basic_fields.get("wifi module", "")
    if is_wireless_module(fields["wifi module"]):
        wifi_plan = pages.get("wifi_test_plan")
        people = []
        if wifi_plan is not None:
            wifi_plan = client.get_page(wifi_plan.id)
            pages["wifi_test_plan"] = wifi_plan
            wifi_body = wifi_plan.body or wifi_plan.view_body
            people = extract_role_people(table_field_html(wifi_body, "Test Owner"), "Test Owner")
        if not people:
            people = [{
                "identity": "", "name": "Unknown", "role": "Test Owner",
                "source_evidence": {"kind": "missing", "segment": 0},
            }]
        roles[WIFI_ROLE_LABELS[0]] = deepcopy(people)
        roles[WIFI_ROLE_LABELS[1]] = deepcopy(people)
        roles[WIFI_ROLE_LABELS[2]] = [{
            "identity": "zijie.chen", "name": "zijie.chen", "role": WIFI_ROLE_LABELS[2],
            "source_evidence": {"kind": "fixed", "segment": 0},
        }]
    parse_ms = round((perf_counter() - parse_started) * 1000, 3)
    user_started = perf_counter()
    attempted, resolved = resolve_role_display_names(client, roles, names)
    smart_log("Confluence detail stage timing", domain="framework", source="confluence_detail", emit_runtime_event=False,
              extra={"stage": "detail.basic_information", "duration_ms": basic_ms,
                     "parse_duration_ms": parse_ms, "user_lookup_duration_ms": round((perf_counter() - user_started) * 1000, 3),
                     "user_lookup_count": attempted, "user_resolved_count": resolved})
    row.update(entry_page_id=context["entry_page_id"], root_page_id=context["root_page_id"],
               detail_path=context.get("page_paths", {}).get("basic", []), roles=roles, fields=fields,
               status="current", error=None,
               evidence=[{"source": kind, **_page_evidence(page, row["space_key"])}
                         for kind, page in pages.items()],
               detail_source=_detail_evidence(detail, row["space_key"]), updated_at=now.isoformat())
    smart_log("Confluence detail stage timing", domain="framework", source="confluence_detail", emit_runtime_event=False,
              extra={"stage": "detail.total", "duration_ms": round((perf_counter() - total_started) * 1000, 3),
                     "evidence_count": len(row["evidence"])})
    return row


def is_wireless_module(value):
    normalized = str(value or "").casefold()
    return "w1" in normalized or "w2" in normalized


def _query_filters(filters):
    normalized = {
        _normalize(key): tuple(dict.fromkeys(
            _normalize(item) for item in (value if isinstance(value, (list, tuple, set)) else (value,))
            if _normalize(item)
        ))
        for key, value in (filters or {}).items()
    }
    return {key: values for key, values in normalized.items() if values}


def query_project_facts(snapshot, *, filters=None, fixed_filters=None, search="", include_inactive=False):
    filters, fixed = _query_filters(filters), _query_filters(fixed_filters)
    needle = _normalize(search)
    candidates = [row for row in (snapshot or {}).get("projects", [])
                  if (include_inactive or row.get("active", True))
                  and _matches_filters(row, row.get("fields", {}), fixed)]
    rows = []
    for row in candidates:
        fields = dict(row.get("fields", {}))
        if not _matches_filters(row, fields, filters):
            continue
        people = " ".join(
            f"{person.get('name', '')} {person.get('account', '')} {person.get('identity', '')}"
            for role in row.get("roles", {}).values() for person in role
        )
        field_text = " ".join(f"{key} {value}" for key, value in fields.items())
        haystack = " ".join((
            row.get("project_id", ""), row.get("name", ""), row.get("space_key", ""),
            row.get("customer_summary", ""), people, field_text,
        ))
        if needle and needle not in _normalize(haystack):
            continue
        visible = deepcopy(row)
        visible["fields"] = fields
        visible["responsibility_unavailable"] = not any(
            visible.get("roles", {}).get(role) for role in ROLE_LABELS
        )
        rows.append(visible)
    facet_keys = sorted({key for row in candidates for key in row.get("fields", {})})
    facets = {key: sorted({row["fields"][key] for row in candidates if row.get("fields", {}).get(key)}, key=str.casefold)
              for key in facet_keys}
    for key in PROJECT_SPACE_FILTER_FIELDS:
        facets.setdefault(key, [])
    facets["date of commercial approval"] = sorted({
        year for row in candidates
        if (year := _commercial_year(row.get("fields", {}).get("date of commercial approval", "")))
    })
    selected_spaces = filters.get(PRODUCT_SPACE_FACET, ())
    stage_domains = dict((snapshot or {}).get("stage_domains", {}))
    if not stage_domains:
        for row in candidates:
            stage = row.get("fields", {}).get("current stage", "")
            if stage and stage not in stage_domains.setdefault(row.get("space_key", ""), []):
                stage_domains[row.get("space_key", "")].append(stage)
    domain_spaces = selected_spaces or tuple(stage_domains)
    facets["current stage"] = sorted({
        stage for space in domain_spaces
        for stage in stage_domains.get(space.upper(), ())
        if stage
    }, key=str.casefold)
    labels = {line.confluence_space_key: line.name for line in PRODUCT_LINES}
    labels.update({source.get("space_key"): source.get("display_name")
                   for source in (snapshot or {}).get("sources", [])
                   if source.get("space_key") and source.get("display_name")})
    facets[PRODUCT_SPACE_FACET] = [
        {"value": value, "label": labels.get(value) or value}
        for value in sorted({row.get("space_key", "") for row in candidates if row.get("space_key")}, key=str.casefold)
    ]
    hierarchy = _owner_hierarchy(rows)
    return {"projects": rows, "facets": facets, "ownerHierarchy": hierarchy}


def _matches_filters(row, fields, filters):
    for key, values in filters.items():
        if key == PRODUCT_SPACE_FACET:
            actual = _normalize(row.get("space_key", ""))
        elif key == "date of commercial approval":
            year = _commercial_year(fields.get(key, ""))
            actual = str(year or "")
        else:
            actual = _normalize(fields.get(key, ""))
        if actual not in values:
            return False
    return True


def _owner_hierarchy(projects):
    hierarchy = []
    for role in ROLE_LABELS:
        people = {}
        for project in projects:
            members = [person for person in project.get("roles", {}).get(role, ())
                       if str(person.get("identity") or "").strip() or str(person.get("name") or "").strip()]
            for person in members or ({},):
                identity = str(person.get("identity") or "").strip()
                name = str(person.get("name") or "").strip()
                key = identity or name.casefold() or None
                owner = people.setdefault(key, {"name": name, "identity": identity, "projects": []})
                if all(row.get("identity") != project.get("identity") for row in owner["projects"]):
                    owner["projects"].append(deepcopy(project))
        hierarchy.append({
            "role": role,
            "people": sorted(people.values(), key=lambda row: (row["name"].casefold(), row["identity"].casefold())),
        })
    return hierarchy


def _catalog_rows(source, space_key):
    merged = {}
    found_catalog = False
    for table in html_tables(source.view_body or source.body):
        header_index = next((index for index, cells in enumerate(table)
                             if {"page", "project id"} <= {_header(cell) for cell in cells}), None)
        if header_index is None:
            continue
        found_catalog = True
        raw_headers = [text(cell).strip() for cell in table[header_index]]
        normalized_headers = [_header(cell) for cell in table[header_index]]
        for row_index, cells in enumerate(table[header_index + 1:], start=header_index + 2):
            if not any(text(cell).strip() for cell in cells):
                continue
            if len(cells) < len(raw_headers):
                raise CatalogParseError('catalog_row_missing_cells', row=row_index)
            values = dict(zip(normalized_headers, cells))
            page_links = links(values.get("page", ""))
            project_id = text(values.get("project id", "")).strip()
            if not page_links or not project_id:
                raise CatalogParseError('catalog_row_missing_link_or_project_id', row=row_index)
            href, label = page_links[0]
            identity = f"{space_key}:{project_id}"
            row = merged.setdefault(identity, {
                "raw_fields": {}, "raw_field_html": {}, "fields": {}, "raw_headers": [],
                "project_owners": [],
            })
            for raw_header, normalized, cell in zip(raw_headers, normalized_headers, cells):
                value = text(cell).strip()
                row["raw_headers"].append(raw_header)
                if value and raw_header not in row["raw_fields"]:
                    row["raw_fields"][raw_header] = value
                    row["raw_field_html"][raw_header] = cell
                if value and normalized not in row["fields"]:
                    row["fields"][normalized] = value
                if value and normalized == "project owner":
                    known = {
                        person.get("identity") or str(person.get("name") or "").casefold()
                        for person in row["project_owners"]
                    }
                    row["project_owners"].extend(
                        person for person in extract_role_people(cell, "Project Owner")
                        if (person.get("identity") or str(person.get("name") or "").casefold()) not in known
                    )
            page_url = urljoin(source.url, href)
            row.update(identity=identity, project_id=project_id,
                       name=canonical_project_name(label, project_id),
                       space_key=space_key, page_url=page_url,
                       page_id=(parse_qs(urlsplit(page_url).query).get("pageId") or [""])[0])
    if not found_catalog:
        reports = [attrs for attrs in table_attributes(source.view_body or source.body)
                   if 'metadata-summary-macro' in (attrs.get('class') or '').split()
                   and attrs.get('data-details-space-key') == space_key
                   and 'project id' in {_header(value) for value in (attrs.get('data-headings') or '').split(',')}]
        if not reports or any(attrs.get('data-total-pages') != '0' for attrs in reports):
            raise CatalogParseError('catalog_table_missing')
    result = []
    for row in merged.values():
        row["raw_headers"] = list(dict.fromkeys(row["raw_headers"]))
        row["discrepancies"] = [header for header in row["raw_headers"] if normalize_project_field(header) not in CANONICAL_PROJECT_FIELDS]
        row["field_discrepancies"] = list(row["discrepancies"])
        row["catalog_fingerprint"] = hashlib.sha256(json.dumps(
            {"identity": row["identity"], "raw_fields": row["raw_fields"]},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        result.append(row)
    return result


def _is_access_denied(exc):
    return getattr(getattr(exc, "response", None), "status_code", None) == 403


def _page_evidence(page, space_key):
    return {"space_key": space_key, "page_id": str(page.id), "title": page.title, "url": page.url,
            "version": int(page.version or 0),
            "updated_at": page.updated_at.isoformat() if page.updated_at else None}


def _detail_evidence(page, space_key):
    evidence = _page_evidence(page, space_key)
    evidence["role_parser_version"] = ROLE_PARSER_VERSION
    return evidence


def _current_role_parser(project):
    return project.get("detail_source", {}).get("role_parser_version") == ROLE_PARSER_VERSION


def _header(value):
    return normalize_project_field(text(value))


def _normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()
