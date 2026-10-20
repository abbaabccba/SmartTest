from __future__ import annotations

from core.read_requests import read_request
from dataclasses import replace
from datetime import datetime
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlsplit

try:
    from atlassian import Confluence
except ImportError:
    Confluence = None

from core.logging import smart_log

from .models import ConfluenceGatewayConfig, ConfluencePage


class ConfluenceDependencyError(RuntimeError):
    pass


def _date(value: Any) -> datetime | None:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None

class ConfluenceGateway:
    def __init__(self, config: ConfluenceGatewayConfig, username: str, password: str, *, api=None):
        self.config = config
        if api is not None:
            self._api = api
            return
        try:
            if Confluence is None:
                raise ConfluenceDependencyError(
                    "atlassian-python-api is not installed",
                )
            self._api = Confluence(url=config.base_url.rstrip("/"), username=username, password=password, backoff_and_retry=False, retry_with_header=False)
        except ConfluenceDependencyError:
            raise
        except Exception as exc:
            raise RuntimeError("Confluence client initialization failed") from exc

    def _url(self, links: dict, key="webui") -> str:
        value = str((links or {}).get(key) or "")
        if value.startswith(("http://", "https://")):
            return value
        return f"{self.config.base_url.rstrip('/')}/{value.lstrip('/')}" if value else ""

    @staticmethod
    def _timed(stage, operation, **extra):
        started = perf_counter()
        outcome = "success"
        try:
            return operation()
        except Exception:
            outcome = "failure"
            raise
        finally:
            smart_log("Confluence REST timing", domain="framework", source="confluence_gateway", emit_runtime_event=False,
                      extra={"stage": stage, "duration_ms": round((perf_counter() - started) * 1000, 3),
                             "outcome": outcome, **extra})

    def _page(self, payload: dict, *, prefer_export=False) -> ConfluencePage:
        content = payload.get("content") if isinstance(payload.get("content"), dict) else payload
        version = content.get("version") or {}
        body = content.get("body") or {}
        view_body = str(((body.get("view") or {}).get("value")) or "")
        if prefer_export:
            view_body = str(
                ((body.get("export_view") or {}).get("value")) or view_body
            )
        return ConfluencePage(str(content.get("id") or ""), str(content.get("title") or ""),
            self._url(content.get("_links") or {}),
            str(((body.get("storage") or {}).get("value")) or ""),
            view_body,
            int(version.get("number") or 0), _date(version.get("when")))

    def search_pages(self, cql: str, *, limit: int = 100) -> list[ConfluencePage]:
        pages, start = [], 0
        while True:
            payload = read_request(self._api.cql, cql, start=start, limit=limit, expand="body.storage,body.view,version")
            rows = payload.get("results") or []
            pages.extend(self._page(row) for row in rows)
            start += len(rows)
            if not rows or start >= int(payload.get("totalSize") or start):
                return pages

    def search_page_metadata(self, cql: str, *, limit: int = 100) -> list[ConfluencePage]:
        """Return permission-filtered current page metadata without downloading bodies."""
        pages, start = [], 0
        while True:
            payload = read_request(self._api.cql, cql, start=start, limit=limit, expand="version,space")
            rows = payload.get("results") or []
            pages.extend(replace(self._page(row), body="", view_body="") for row in rows)
            start += len(rows)
            if not rows or start >= int(payload.get("totalSize") or start):
                return pages

    def get_page(self, page_id: str, *, prefer_export=False) -> ConfluencePage:
        expand = (
            "body.storage,body.view,body.export_view,version"
            if prefer_export else "body.storage,body.view,version"
        )
        return self._timed("rest.page.current", lambda: self._page(
            read_request(self._api.get_page_by_id, page_id, expand=expand), prefer_export=prefer_export,
        ), page_id=str(page_id))

    def get_page_version(self, page_id: str, version: int) -> ConfluencePage:
        return self._timed("rest.page.history", lambda: self._page(read_request(self._api.get_page_by_id,
            page_id,
            expand="body.storage,body.view,version",
            status="historical",
            version=int(version),
        )), page_id=str(page_id), version=int(version))

    def get_page_by_url(self, url: str, *, prefer_export=False) -> ConfluencePage:
        configured = urlsplit(self.config.base_url)
        parsed = urlsplit(str(url or ""))
        if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != configured.netloc.casefold():
            raise ValueError("Confluence page URL must use the configured host")
        page_id = (parse_qs(parsed.query).get("pageId") or [""])[0]
        if page_id:
            return self.get_page(page_id, prefer_export=prefer_export)
        parts = [unquote_plus(item) for item in parsed.path.split("/") if item]
        try:
            marker = parts.index("display")
            space, title = parts[marker + 1], parts[marker + 2]
        except (ValueError, IndexError):
            raise ValueError("Unsupported Confluence page URL") from None
        expand = (
            "body.view,body.export_view,version"
            if prefer_export else "body.view,version"
        )
        payload = self._timed("rest.catalog.page", lambda: read_request(self._api.get_page_by_title, space, title, expand=expand),
                              space_key=space)
        if not payload:
            raise ValueError("Confluence page could not be resolved")
        return self._page(payload, prefer_export=prefer_export)

    def get_parent_page(self, page_id: str) -> ConfluencePage | None:
        ancestors = self._timed("rest.page.ancestors", lambda: read_request(self._api.get_page_ancestors, page_id) or [],
                                page_id=str(page_id))
        if not ancestors:
            return None
        parent_id = str((ancestors[-1] or {}).get("id") or "")
        return self.get_page(parent_id) if parent_id else None

    def get_user_display_name(self, user_key: str) -> str:
        """Resolve a Confluence identity through the maintained Atlassian client API."""
        payload = self._get_user_by_key(str(user_key))
        return str(payload.get("displayName") or payload.get("publicName") or payload.get("name") or "").strip()

    def _get_user_by_key(self, identity: str) -> dict[str, Any]:
        return self._timed("rest.user.lookup", lambda: read_request(self._api.get_user_details_by_userkey, identity) or {})

    def get_page_children(self, page_id: str, *, limit: int = 100) -> list[ConfluencePage]:
        started = perf_counter()
        pages, start, seen_pages = [], 0, set()
        request_count = 0
        try:
            while True:
                request_count += 1
                payload = read_request(self._api.get_page_child_by_type,
                    page_id, type="page", start=start, limit=limit,
                    expand="version",
                ) or {}
                list_response = isinstance(payload, list)
                rows = payload if list_response else payload.get("results") or []
                if not rows:
                    return pages
                signature = tuple(
                    (
                        str(row.get("id") or ""),
                        str(row.get("title") or ""),
                        str((row.get("_links") or {}).get("webui") or ""),
                    )
                    for row in rows
                )
                if signature in seen_pages:
                    raise RuntimeError(
                        "Confluence child-page pagination returned a repeated page",
                    )
                seen_pages.add(signature)
                pages.extend(
                    replace(self._page(row), body="", view_body="")
                    for row in rows
                )
                start += len(rows)
                if list_response and len(rows) < limit:
                    return pages
                total = None if list_response else payload.get(
                    "totalSize", payload.get("total"),
                )
                if (total is not None and start >= int(total)) or (total is None and len(rows) < limit):
                    return pages
        finally:
            smart_log("Confluence REST timing", domain="framework", source="confluence_gateway", emit_runtime_event=False,
                      extra={"stage": "rest.page.children", "duration_ms": round((perf_counter() - started) * 1000, 3),
                             "page_id": str(page_id), "request_count": request_count, "child_count": len(pages)})
