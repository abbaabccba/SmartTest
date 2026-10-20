from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from core.issues.create_schema import CreateFieldControl, CreateFieldOption, CreateFieldSchema
from core.jira.services.create_schema_service import jira_create_defaults


@dataclass(frozen=True)
class JiraCreateDraftError:
    field_id: str
    message: str
    blocking: bool = True


@dataclass(frozen=True)
class JiraCreateDraftField:
    schema: CreateFieldSchema
    value: Any
    error: str = ""

    @property
    def field_id(self) -> str:
        return self.schema.field_id


@dataclass
class JiraCreateDraft:
    fields: tuple[JiraCreateDraftField, ...]

    def __post_init__(self) -> None:
        self._apply_create_defaults()

    def _apply_create_defaults(self) -> None:
        defaults = jira_create_defaults(
            (field.schema for field in self.fields),
            {field.field_id: field.value for field in self.fields},
        )
        self.fields = tuple(
            replace(
                field, value=defaults[field.field_id],
                error=validate_create_value(field.schema, defaults[field.field_id]),
            )
            if field.field_id in defaults else field
            for field in self.fields
        )

    def value(self, field_id: str) -> Any:
        field = self._field(field_id)
        return field.value if field is not None else None

    @property
    def errors(self) -> tuple[JiraCreateDraftError, ...]:
        return tuple(
            JiraCreateDraftError(field_id=field.field_id, message=field.error)
            for field in self.fields
            if field.error
        )

    def update(self, field_id: str, value: Any) -> None:
        updated = []
        found = False
        for field in self.fields:
            if field.field_id != field_id:
                updated.append(field)
                continue
            found = True
            error = validate_create_value(field.schema, value)
            updated.append(replace(field, value=value, error=error))
        if not found:
            raise KeyError(field_id)
        self.fields = tuple(updated)
        self._apply_create_defaults()

    def _field(self, field_id: str) -> JiraCreateDraftField | None:
        return next((field for field in self.fields if field.field_id == field_id), None)


def find_create_option(
    options: Iterable[CreateFieldOption],
    label_or_value: str,
) -> CreateFieldOption | None:
    return next(
        (
            option
            for option in options
            if option.label == label_or_value or option.value == label_or_value
        ),
        None,
    )


def validate_create_value(schema: CreateFieldSchema, value: Any) -> str:
    if schema.required and value in (None, "", [], {}):
        return f"{schema.name} is required"
    if not schema.options or value in (None, "", [], {}):
        return ""
    if schema.control == CreateFieldControl.CASCADE:
        if not isinstance(value, dict):
            return f"Invalid option for {schema.name}"
        parent = find_create_option(schema.options, str(value.get("parent") or ""))
        child_value = str(value.get("child") or "")
        if parent and schema.child_required and not child_value:
            return f"{schema.name} child is required"
        child = find_create_option(parent.children, child_value) if parent and child_value else None
        return "" if parent and (not child_value or child) else f"Invalid option for {schema.name}"
    if schema.control == CreateFieldControl.MULTI:
        if not isinstance(value, (list, tuple)):
            return f"Invalid option for {schema.name}"
        return (
            ""
            if all(find_create_option(schema.options, str(item)) for item in value)
            else f"Invalid option for {schema.name}"
        )
    if schema.control == CreateFieldControl.SINGLE:
        return "" if find_create_option(schema.options, str(value)) else f"Invalid option for {schema.name}"
    return ""
