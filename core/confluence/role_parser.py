from __future__ import annotations

from core.async_tasks import TaskCancelled

import re
from urllib.parse import parse_qs, urlsplit

from .html import text
from .project_rules import ROLE_LABELS


def extract_project_roles(body):
    roles = {label: [] for label in ROLE_LABELS}
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body or "", re.I | re.S):
        cells = re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.I | re.S)
        if len(cells) < 2:
            continue
        role = text(cells[0]).strip()
        if role in roles:
            roles[role] = extract_role_people(cells[1], role)
    return roles


def resolve_role_display_names(client, roles, resolved_names):
    attempted = resolved = 0
    for people in roles.values():
        for person in people:
            identity, name = str(person.get("identity") or "").strip(), str(person.get("name") or "").strip()
            if not identity:
                continue
            if name and _normalize(name) != _normalize(identity):
                resolved_names.setdefault(identity, name)
            elif identity not in resolved_names:
                attempted += 1
                try: display_name = client.get_user_display_name(identity)
                except TaskCancelled:
                    raise
                except Exception: display_name = ""  # noqa: BLE001
                resolved_names[identity] = display_name or identity
                resolved += bool(display_name)
            person["name"] = resolved_names.get(identity) or identity
    return attempted, resolved


def extract_role_people(cell, role):
    people, seen_ids, seen_names = [], set(), set()
    for segment_index, segment in enumerate(re.split(r"<br\s*/?>", cell, flags=re.I)):
        structured, anchor_names = [], []
        for tag in re.findall(r"<ri:user\b[^>]*/?>", segment, re.I):
            identity = _attribute(tag, "ri:account-id") or _attribute(tag, "ri:userkey") or _attribute(tag, "ri:username")
            if identity: structured.append((_attribute(tag, "ri:display-name") or identity, identity, "ri:user"))
        for attrs, body in re.findall(r"<a\b([^>]*)>(.*?)</a>", segment, re.I | re.S):
            name = text(body).strip(); query = parse_qs(urlsplit(_attribute(attrs, "href")).query)
            identity = (_attribute(attrs, "data-account-id") or _attribute(attrs, "data-username") or
                        (query.get("accountId") or query.get("userKey") or query.get("username") or [""])[0])
            if identity: structured.append((name or identity, identity, "user-anchor"))
            elif name: anchor_names.append(name)
        residual = re.sub(r"<ri:user\b[^>]*/?>", " ", segment, flags=re.I)
        residual = re.sub(r"<a\b[^>]*>.*?</a>", " ", residual, flags=re.I | re.S)
        residual = text(residual).strip()
        plain = ([name.strip() for name in re.split(r"[,;、，]+", residual) if name.strip()]
                 if structured and re.match(r"^[,;、，]", residual) else
                 anchor_names + [name.strip() for name in re.split(r"[,;、，\n]+", residual) if name.strip()] if not structured else [])
        for name, identity, source in structured:
            if identity not in seen_ids:
                seen_ids.add(identity); people.append({"identity": identity, "name": name, "role": role,
                    "source_evidence": {"kind": source, "segment": segment_index}})
        for name in plain:
            normalized = _normalize(name)
            if normalized not in seen_names:
                seen_names.add(normalized); people.append({"identity": "", "name": name, "role": role,
                    "source_evidence": {"kind": "plain-text", "segment": segment_index}})
    return people


def _attribute(value, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(['\"])(.*?)\1", value or "", re.I | re.S)
    return match.group(2).strip() if match else ""


def _normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()
