from __future__ import annotations

from dataclasses import FrozenInstanceError
from copy import deepcopy

import pytest

from core.issues.create_schema import CreateFieldControl
from core.issues.errors import JiraRequestError
from core.jira.services.create_schema_service import JiraCreateSchemaService, jira_create_defaults
from core.jira.gateway import JiraGateway
from core.jira.create_draft import JiraCreateDraft, JiraCreateDraftField
from core.tools.SmartHome.redmine.clone_draft import RedmineCloneDraftService
from core.tools.SmartHome.redmine.models import RedmineIssueDetail, RedmineProject


CREATE_META = {
    "projects": [{
        "key": "SH",
        "name": "SmartHome",
        "issuetypes": [{
            "id": "1",
            "name": "Bug",
            "fields": {
                "summary": {"name": "Summary", "required": True, "schema": {"type": "string"}},
                "description": {"name": "Description", "required": False, "schema": {"type": "string"}, "defaultValue": "Jira template"},
                "customfield_12200": {
                    "name": "Channel of Reporter",
                    "required": True,
                    "schema": {"type": "option", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:cascadingselect"},
                    "allowedValues": [{"id": "", "value": "None"}, {"id": "13251", "value": "Customer-Feedback", "children": [{"id": None, "value": "None"}, {"id": "17011", "value": "Requirement unclear"}]}, {"id": "13261", "value": "Self-Test"}],
                    "defaultValue": {"id": "13251", "child": {"id": ""}},
                },
                "components": {
                    "name": "Component/s",
                    "required": False,
                    "schema": {"type": "array", "items": "component"},
                    "allowedValues": [{"id": "20", "name": "Customization"}],
                    "defaultValue": [{"id": "20"}],
                },
                "customfield_10700": {
                    "name": "Manager",
                    "required": False,
                    "schema": {"type": "string", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:userpicker"},
                    "defaultValue": {"name": "fred.chen", "displayName": "Fred Chen"},
                },
                "assignee": {
                    "name": "Assignee",
                    "required": False,
                    "schema": {"type": "user", "system": "assignee"},
                },
                "priority": {
                    "name": "Priority",
                    "required": True,
                    "schema": {"type": "priority"},
                    "allowedValues": [{"id": "2", "name": "P2"}],
                    "defaultValue": {"id": "2"},
                },
                "labels": {"name": "Labels", "required": False, "schema": {"type": "array", "items": "string"}},
                "customfield_multi": {
                    "name": "Multi Select",
                    "required": False,
                    "schema": {
                        "type": "array",
                        "items": "option",
                        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
                    },
                    "allowedValues": [{"id": "16000", "value": "One"}],
                },
            },
        }],
    }],
}


class RecordingClient:
    def __init__(self, response_data):
        self.response_data = response_data
        self.calls = []

    def fetch_create_metadata(self, project_key, issue_type):
        self.calls.append(("metadata", project_key, issue_type))
        return self.response_data

    def search_users(self, query, *, project_key):
        self.calls.append(("users", query, project_key))
        return [
            {"account": str(item.get("name") or ""), "display_name": str(item.get("displayName") or ""), "avatar_url": str((item.get("avatarUrls") or {}).get("48x48") or "")}
            for item in self.response_data
        ]

    def current_user(self):
        self.calls.append(("current_user",))
        item = self.response_data
        return {"account": str(item.get("name") or ""), "display_name": str(item.get("displayName") or ""), "avatar_url": str((item.get("avatarUrls") or {}).get("48x48") or "")}


def test_fetch_create_metadata_scopes_project_and_issue_type():
    client = RecordingClient(CREATE_META)

    payload = client.fetch_create_metadata("SH", "Bug")

    assert payload == CREATE_META
    assert client.calls == [("metadata", "SH", "Bug")]


def test_search_users_is_project_scoped_and_normalizes_public_identity_only():
    client = RecordingClient([{
        "name": "fred.chen",
        "displayName": "Fred Chen",
        "avatarUrls": {"48x48": "https://jira/avatar/fred"},
        "password": "must-not-leak",
    }])

    users = client.search_users("fred", project_key="SH")

    assert users == [{
        "account": "fred.chen",
        "display_name": "Fred Chen",
        "avatar_url": "https://jira/avatar/fred",
    }]
    assert client.calls == [("users", "fred", "SH")]


def test_current_user_reads_authenticated_identity_from_myself():
    client = RecordingClient({
        "name": "subing.xu",
        "displayName": "Subing Xu",
        "avatarUrls": {"48x48": "https://jira/avatar/subing"},
        "password": "must-not-leak",
    })

    user = client.current_user()

    assert user == {
        "account": "subing.xu",
        "display_name": "Subing Xu",
        "avatar_url": "https://jira/avatar/subing",
    }
    assert client.calls == [("current_user",)]


def test_schema_maps_jira_native_controls_required_options_and_order():
    service = JiraCreateSchemaService(RecordingClient(CREATE_META))

    schema = service.schema("SH", "Bug")
    fields = {item.field_id: item for item in schema}

    assert [item.field_id for item in schema] == [
        "project",
        "issuetype",
        *CREATE_META["projects"][0]["issuetypes"][0]["fields"],
    ]
    assert fields["project"].required and fields["project"].control == "single"
    assert fields["project"].value == "SH"
    assert fields["project"].options[0].value == "SH"
    assert fields["issuetype"].required and fields["issuetype"].control == "single"
    assert fields["issuetype"].value == "Bug"
    assert fields["issuetype"].options[0].value == "Bug"
    assert fields["summary"].control == "text" and fields["summary"].required
    assert fields["description"].control == "multiline"
    assert fields["customfield_12200"].control == "cascade"
    assert fields["customfield_12200"].child_required
    assert fields["components"].control == "multi"
    assert fields["customfield_10700"].control == "user"
    assert fields["assignee"].control == "user"
    assert fields["priority"].control == "single"
    assert fields["labels"].control == "multi"
    assert fields["customfield_multi"].control == "multi"
    assert not fields["labels"].required
    assert fields["description"].value == "Jira template"
    assert fields["priority"].value == "2"
    assert fields["components"].value == ["20"]
    assert fields["customfield_12200"].value == {"parent": "13251", "child": ""}
    assert fields["customfield_10700"].value == "fred.chen"
    assert fields["priority"].options[0].value == "2"
    assert fields["priority"].options[0].label == "P2"
    none_option, cascade, self_test = fields["customfield_12200"].options
    assert none_option.value == "" and none_option.label == "None"
    assert cascade.value == "13251" and cascade.label == "Customer-Feedback"
    assert [(item.value, item.label) for item in cascade.children] == [
        ("17011", "Requirement unclear")
    ]
    assert self_test.value == "13261"

    assert {item.value for item in CreateFieldControl} == {
        "text", "multiline", "single", "multi", "cascade", "user"
    }
    with pytest.raises(FrozenInstanceError):
        fields["summary"].required = False


def test_ordinary_cascade_schema_injects_selectable_empty_child_when_metadata_omits_placeholder():
    import copy
    payload = copy.deepcopy(CREATE_META)
    channel = payload["projects"][0]["issuetypes"][0]["fields"]["customfield_12200"]
    channel["name"] = "Other Cascade"
    customer = next(item for item in channel["allowedValues"] if item.get("value") == "Customer-Feedback")
    customer["children"] = [
        {"id": "reason1", "value": "Reason 1"},
        {"id": "reason2", "value": "Reason 2"},
    ]

    field = next(
        item for item in JiraCreateSchemaService(RecordingClient(payload)).schema("SH", "Bug")
        if item.field_id == "customfield_12200"
    )
    customer_option = next(item for item in field.options if item.label == "Customer-Feedback")

    assert not field.child_required
    assert [(item.value, item.label) for item in customer_option.children] == [
        ("", "None"), ("reason1", "Reason 1"), ("reason2", "Reason 2")
    ]
    assert field.value == {"parent": "13251", "child": ""}


def test_software_release_is_single_select_despite_jira_multiselect_metadata():
    import copy
    payload = copy.deepcopy(CREATE_META)
    fields = payload["projects"][0]["issuetypes"][0]["fields"]
    fields["customfield_release"] = {
        "name": "Software Release",
        "required": True,
        "schema": {
            "type": "string",
            "custom": "com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
        },
        "allowedValues": [
            {"id": "release-1", "value": "Release 1"},
            {"id": "release-2", "value": "Release 2"},
        ],
    }

    field = next(
        item for item in JiraCreateSchemaService(RecordingClient(payload)).schema("SH", "Bug")
        if item.field_id == "customfield_release"
    )

    assert field.control == CreateFieldControl.SINGLE
    assert field.payload_control == CreateFieldControl.MULTI


def test_compare_status_is_required_single_select_even_when_metadata_is_optional():
    import copy
    payload = copy.deepcopy(CREATE_META)
    fields = payload["projects"][0]["issuetypes"][0]["fields"]
    fields["customfield_compare"] = {
        "name": "Compare Status",
        "required": False,
        "schema": {
            "type": "string",
            "custom": "com.atlassian.jira.plugin.system.customfieldtypes:multiselect",
        },
        "allowedValues": [{"id": "same", "value": "Same"}],
    }

    field = next(
        item for item in JiraCreateSchemaService(RecordingClient(payload)).schema("SH", "Bug")
        if item.field_id == "customfield_compare"
    )

    assert field.required
    assert field.control == CreateFieldControl.SINGLE
    assert field.payload_control == CreateFieldControl.MULTI


@pytest.mark.parametrize(
    "payload, project_key, issue_type",
    [({"projects": []}, "SH", "Bug"), (CREATE_META, "MISSING", "Bug"), (CREATE_META, "SH", "Support")],
)
def test_schema_rejects_missing_project_or_issue_type(payload, project_key, issue_type):
    with pytest.raises(JiraRequestError):
        JiraCreateSchemaService(RecordingClient(payload)).schema(project_key, issue_type)


@pytest.mark.parametrize("existing", [None, "", "TV-CATEGORY-CASE"])
def test_clone_self_test_defaults_test_case_id_and_preserves_reporter_channel(existing):
    payload = deepcopy(CREATE_META)
    metadata = payload["projects"][0]["issuetypes"][0]["fields"]
    metadata["customfield_channel"] = metadata.pop("customfield_12200")
    metadata["customfield_case_id"] = {
        "name": "Test Case ID", "required": True,
        "schema": {"type": "string"}, "defaultValue": existing,
    }
    schema = JiraCreateSchemaService(RecordingClient(payload)).schema("SH", "Bug")
    draft = RedmineCloneDraftService().build(
        issue=RedmineIssueDetail(id="1", url="https://redmine/1", subject="Bug"),
        project=RedmineProject(name="Project", identifier="project", url="p"),
        schema=schema, account="alice", department="QA", prepared_description="body",
    )
    customer_channel = {"parent": "13251", "child": ""}
    assert draft.value("customfield_channel") == customer_channel
    assert draft.value("customfield_case_id") == (existing or "")

    self_test_channel = {"parent": "13261", "child": ""}
    draft.update("customfield_channel", self_test_channel)

    expected = existing or "None"
    assert draft.value("customfield_channel") == self_test_channel
    assert draft.value("customfield_case_id") == expected
    assert not any(error.field_id == "customfield_case_id" for error in draft.errors)
    fields = JiraGateway.command_fields(draft.to_command())
    assert fields["customfield_case_id"] == expected
    assert fields["customfield_channel"] == {"id": "13261"}

    draft.update("customfield_case_id", "MANUAL-CASE-ID")
    assert draft.value("customfield_case_id") == "MANUAL-CASE-ID"
    draft.update("customfield_channel", customer_channel)
    draft.update("customfield_case_id", "")
    assert draft.value("customfield_case_id") == ""
    assert "customfield_case_id" not in JiraGateway.command_fields(draft.to_command())


def test_jira_create_defaults_uses_metadata_option_identity():
    payload = deepcopy(CREATE_META)
    metadata = payload["projects"][0]["issuetypes"][0]["fields"]
    metadata["customfield_case_id"] = {
        "name": "Test Case ID", "schema": {"type": "string"},
    }
    schema = JiraCreateSchemaService(RecordingClient(payload)).schema("SH", "Bug")
    values = {"customfield_12200": {"parent": "13261", "child": ""}}

    assert jira_create_defaults(schema, values) == {"customfield_case_id": "None"}
    assert values == {"customfield_12200": {"parent": "13261", "child": ""}}
    assert jira_create_defaults(schema, {}) == {}
    assert jira_create_defaults(schema, {**values, "customfield_case_id": "TV-CASE-ID"}) == {}

    draft = JiraCreateDraft(tuple(
        JiraCreateDraftField(field, values.get(field.field_id)) for field in schema
    ))
    assert draft.value("customfield_case_id") == "None"
    assert not any(error.field_id == "customfield_case_id" for error in draft.errors)
