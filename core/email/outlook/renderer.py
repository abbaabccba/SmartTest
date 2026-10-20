"""Render email bodies and collect local inline-image resources."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from core.markup import render_markup


class OutlookContentError(ValueError):
    """Raised when email content cannot be rendered safely."""


@dataclass(frozen=True)
class InlineImage:
    path: Path
    content_id: str


@dataclass(frozen=True)
class RenderedBody:
    html: str
    plain_text: str
    inline_images: tuple[InlineImage, ...]


class _ImageRewriter(HTMLParser):
    def __init__(self, base_dir: Path) -> None:
        super().__init__(convert_charrefs=False)
        self.base_dir = base_dir.resolve()
        self.parts: list[str] = []
        self.images: dict[Path, InlineImage] = {}

    def _attributes(self, attrs: list[tuple[str, str | None]]) -> str:
        rendered = []
        for name, value in attrs:
            if value is None:
                rendered.append(name)
            else:
                rendered.append(f'{name}="{escape(value, quote=True)}"')
        return (" " + " ".join(rendered)) if rendered else ""

    def _rewrite(self, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        rewritten = []
        for name, value in attrs:
            if name.lower() != "src" or value is None or not _is_local_reference(value):
                rewritten.append((name, value))
                continue
            split = urlsplit(value)
            relative_path = Path(unquote(split.path.replace("/", "\\")))
            path = (self.base_dir / relative_path).resolve()
            if not path.is_file():
                raise OutlookContentError(f"本地正文图片不存在：{value}")
            image = self.images.get(path)
            if image is None:
                image = InlineImage(path, f"smarttest-{uuid5(NAMESPACE_URL, path.as_uri()).hex}@local")
                self.images[path] = image
            rewritten.append((name, f"cid:{image.content_id}"))
        return rewritten

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            attrs = self._rewrite(attrs)
        self.parts.append(f"<{tag}{self._attributes(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img":
            attrs = self._rewrite(attrs)
        self.parts.append(f"<{tag}{self._attributes(attrs)}/>")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _is_local_reference(source: str) -> bool:
    lowered = source.lower()
    return not lowered.startswith(("http://", "https://", "cid:", "data:", "//"))


def _embed_local_image_references(
    fragment: str, base_dir: Path
) -> tuple[str, tuple[InlineImage, ...]]:
    parser = _ImageRewriter(base_dir)
    try:
        parser.feed(fragment)
        parser.close()
    except OutlookContentError:
        raise
    except (OSError, ValueError) as exc:
        raise OutlookContentError(f"正文 HTML 无法解析：{exc}") from exc
    return "".join(parser.parts), tuple(parser.images.values())


def _html_to_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return "\n".join(parser.parts)


def _apply_template(fragment: str, template: str | None) -> str:
    if template is None:
        return fragment
    if template != "technology":
        raise OutlookContentError(f"不支持的邮件模板：{template}")
    return f'''<!doctype html><html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#f4f5f7;font-family:Arial,'Microsoft YaHei',sans-serif;color:#172b4d">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px">
<table role="presentation" width="760" cellspacing="0" cellpadding="0" style="max-width:760px;background:#fff;border-radius:12px">
<tr><td style="padding:30px">{fragment}</td></tr></table></td></tr></table></body></html>'''


def render_body(
    body: str,
    *,
    body_format: Literal["markdown", "html"],
    template: str | None,
    base_dir: Path,
) -> RenderedBody:
    """Render Markdown or HTML and rewrite local images as CID references."""

    if body_format == "markdown":
        fragment = render_markup(body)
        plain_text = body
    elif body_format == "html":
        fragment = body
        plain_text = _html_to_text(body)
    else:
        raise OutlookContentError(f"不支持的正文格式：{body_format}")

    rewritten, images = _embed_local_image_references(fragment, base_dir)
    return RenderedBody(_apply_template(rewritten, template), plain_text, images)
