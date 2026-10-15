import html as html_module
from html.parser import HTMLParser
import re


def table_attributes(html: str) -> list[dict[str, str | None]]:
    class Tables(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables = []

        def handle_starttag(self, tag, attrs):
            if tag == 'table':
                self.tables.append(dict(attrs))

    parser = Tables()
    parser.feed(html or '')
    return parser.tables


def text(html: str) -> str:
    value = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", html_module.unescape(value)).strip()

def table_fields(html: str) -> dict[str, str]:
    result = {}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html or "", re.I | re.S):
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.I | re.S)
        if len(cells) >= 2:
            result[text(cells[0]).casefold()] = text(cells[1])
    return result

def table_field_html(html: str, label: str) -> str:
    wanted = str(label or "").strip().casefold()
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html or "", re.I | re.S):
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.I | re.S)
        if len(cells) >= 2 and text(cells[0]).strip().casefold() == wanted:
            return cells[1]
    return ""

def html_tables(html: str) -> list[list[list[str]]]:
    tables = []
    for table in re.findall(r"<table\b[^>]*>(.*?)</table>", html or "", re.I | re.S):
        rows = []
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.I | re.S):
            cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.I | re.S)
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables

def first_link(html: str, label: str) -> str:
    for href, body in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html or "", re.I | re.S):
        if label.casefold() in text(body).casefold():
            return href
    return ""

def links(html: str) -> list[tuple[str, str]]:
    return [
        (href, text(body))
        for href, body in re.findall(
            r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            html or "", re.I | re.S,
        )
    ]
