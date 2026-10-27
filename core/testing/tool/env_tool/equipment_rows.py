from __future__ import annotations

from typing import Any, Callable

from core.testing.params.contracts import default_env_device_type, env_field_contract
from core.testing.tool.pc_tool import local_host_tool

Translator = Callable[[str], str]
LoadingProbe = Callable[[str, str, str], bool]


def default_env_config(kind: str, device_type: str | None = None) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip().lower()
    normalized_type = str(device_type or "").strip() or default_env_device_type(normalized_kind)
    if not normalized_type:
        return {"type": ""}
    row = build_env_equipment_row(
        kind=normalized_kind,
        config={"type": normalized_type},
        env_options={},
        is_loading=lambda _kind, _owner, _key: False,
        tr=lambda key: key,
    )
    if row is None:
        return {"type": normalized_type}
    defaults = {"type": normalized_type}
    for field in list(row.get("fields", []) or []):
        defaults[str(field.get("key", ""))] = field.get("default", "")
    return defaults


def build_env_equipment_row(
    *,
    kind: str,
    config: dict[str, Any],
    env_options: dict[str, list[str]],
    is_loading: LoadingProbe,
    tr: Translator,
) -> dict[str, Any] | None:
    normalized_kind = str(kind or "").strip().lower()
    normalized_config = dict(config) if isinstance(config, dict) else {}
    if normalized_kind == "relay":
        return _build_relay_row(
            config=normalized_config,
            env_options=env_options,
            is_loading=is_loading,
            tr=tr,
        )
    if normalized_kind == "serial":
        return _build_serial_row(
            config=normalized_config,
            env_options=env_options,
            is_loading=is_loading,
            tr=tr,
        )
    return None


def _build_relay_row(
    *,
    config: dict[str, Any],
    env_options: dict[str, list[str]],
    is_loading: LoadingProbe,
    tr: Translator,
) -> dict[str, Any]:
    relay_type = str(config.get("type") or default_env_device_type("relay")).strip() or default_env_device_type("relay")
    return {
        "kind": "relay",
        "kind_source": "fixed",
        "label": tr("test.env.relay.label"),
        "label_source": "fixed",
        "type": relay_type,
        "type_source": "user",
        "typeLabel": tr("test.env.equipment.type.label"),
        "typeLabel_source": "fixed",
        "typeOptions": [
            {"value": "snmp_pdu", "label": tr("test.env.relay.type.snmp_pdu"), "label_source": "fixed"},
            {"value": "usb_relay", "label": tr("test.env.relay.type.usb_relay"), "label_source": "fixed"},
            {"value": "custom_command", "label": tr("test.env.relay.type.custom_command"), "label_source": "fixed"},
        ],
        "fields": _relay_fields(
            relay_type=relay_type,
            config=config,
            env_options=env_options,
            is_loading=is_loading,
            tr=tr,
        ),
    }


def _relay_fields(
    *,
    relay_type: str,
    config: dict[str, Any],
    env_options: dict[str, list[str]],
    is_loading: LoadingProbe,
    tr: Translator,
) -> list[dict[str, Any]]:
    normalized_type = str(relay_type or "").strip() or "snmp_pdu"
    if normalized_type == "custom_command":
        specs = [
            {
                "key": key,
                "label": label,
                "label_source": "fixed",
                "type": "command",
                "default": "",
                "description": tr("test.env.relay.custom_command.command.description"),
                "description_source": "fixed",
            }
            for key, label in (
                ("power_on_command", tr("test.env.relay.custom_command.power_on_command.label")),
                ("power_off_command", tr("test.env.relay.custom_command.power_off_command.label")),
            )
        ]
    elif normalized_type == "usb_relay":
        usb_port_contract = env_field_contract("relay", normalized_type, "port")
        usb_port_options = list(env_options.get(f"relay:{normalized_type}:port", []))
        specs = [
            {
                "key": "port",
                "label": tr("test.env.relay.usb_relay.port.label"),
                "label_source": "fixed",
                "type": "enum",
                "default": "",
                "enum_values": usb_port_options,
                "enum_values_source": "dynamic",
                "loading": is_loading("env", "relay", "port"),
                "options_source": str(usb_port_contract.options_source if usb_port_contract is not None else ""),
                "description": tr("test.env.relay.usb_relay.port.description"),
                "description_source": "fixed",
            },
            {
                "key": "terminals",
                "label": tr("test.env.relay.usb_relay.terminals.label"),
                "label_source": "fixed",
                "type": "terminal_list",
                "default": [{"terminal": 1, "mode": "NO", "press_seconds": 1}],
                "enum_values": ["NO", "NC"],
                "enum_values_source": "fixed",
                "description": tr("test.env.relay.usb_relay.terminals.description"),
                "description_source": "fixed",
            },
        ]
    else:
        specs = [
            {
                "key": "ip",
                "label": tr("test.env.relay.snmp_pdu.ip.label"),
                "label_source": "fixed",
                "type": "string",
                "default": "",
                "loading": False,
                "description": tr("test.env.relay.snmp_pdu.ip.description"),
                "description_source": "fixed",
            },
            {
                "key": "port",
                "label": tr("test.env.relay.snmp_pdu.port.label"),
                "label_source": "fixed",
                "type": "int",
                "default": 1,
                "loading": False,
                "description": "",
                "description_source": "fixed",
            },
        ]
    return _apply_values(specs, config)


def _build_serial_row(
    *,
    config: dict[str, Any],
    env_options: dict[str, list[str]],
    is_loading: LoadingProbe,
    tr: Translator,
) -> dict[str, Any]:
    serial_type = str(config.get("type") or default_env_device_type("serial")).strip() or default_env_device_type("serial")
    return {
        "kind": "serial",
        "kind_source": "fixed",
        "label": tr("test.env.serial.label"),
        "label_source": "fixed",
        "type": serial_type,
        "type_source": "user",
        "typeLabel": tr("test.env.equipment.type.label"),
        "typeLabel_source": "fixed",
        "typeOptions": [
            {"value": "uart", "label": tr("test.env.serial.type.uart"), "label_source": "fixed"},
        ],
        "fields": _serial_fields(
            serial_type=serial_type,
            config=config,
            env_options=env_options,
            is_loading=is_loading,
            tr=tr,
        ),
    }


def _serial_fields(
    *,
    serial_type: str,
    config: dict[str, Any],
    env_options: dict[str, list[str]],
    is_loading: LoadingProbe,
    tr: Translator,
) -> list[dict[str, Any]]:
    normalized_type = str(serial_type or "").strip() or default_env_device_type("serial")
    serial_port_contract = env_field_contract("serial", normalized_type, "port")
    raw_fields = local_host_tool().serial_env_fields(
        device_type=normalized_type,
        config=config,
        port_options=list(env_options.get(f"serial:{normalized_type}:port", [])),
    )
    fields: list[dict[str, Any]] = []
    for raw_field in raw_fields:
        key = str(raw_field.get("key", ""))
        fields.append(
            {
                "key": key,
                "label": tr(f"test.env.serial.{key}.label") if key in {"port", "baud"} else key,
                "label_source": "fixed",
                "type": str(raw_field.get("type", "")),
                "default": raw_field.get("default", ""),
                "enum_values": list(raw_field.get("enum_values", []) or []),
                "enum_values_source": "dynamic" if str(raw_field.get("type", "")) == "enum" else "fixed",
                "value": raw_field.get("value", ""),
                "value_source": "user",
                "loading": is_loading("env", "serial", key) if key == "port" else False,
                "options_source": str(
                    serial_port_contract.options_source
                    if key == "port" and serial_port_contract is not None
                    else ""
                ),
                "description": tr("test.env.serial.port.description") if key == "port" else "",
                "description_source": "fixed",
            }
        )
    return fields


def _apply_values(specs: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for spec in specs:
        field = dict(spec)
        key = str(field["key"])
        if key == "terminals":
            field["value"] = _relay_terminal_values(config, field.get("default", []))
        else:
            field["value"] = config.get(key, field.get("default", ""))
        field["value_source"] = "user"
        field.setdefault("enum_values", [])
        field.setdefault("enum_values_source", "fixed")
        fields.append(field)
    return fields


def _relay_terminal_values(config: dict[str, Any], default: Any) -> list[dict[str, Any]]:
    raw = config.get("terminals")
    if isinstance(raw, list):
        values = [_normalize_terminal(item) for item in raw if isinstance(item, dict)]
        if values:
            return values
    if "mode" in config or "press_seconds" in config:
        return [
            _normalize_terminal(
                {
                    "terminal": config.get("terminal", 1),
                    "mode": config.get("mode", "NO"),
                    "press_seconds": config.get("press_seconds", 1),
                }
            )
        ]
    if isinstance(default, list):
        return [_normalize_terminal(item) for item in default if isinstance(item, dict)]
    return [{"terminal": 1, "mode": "NO", "press_seconds": 1}]


def _normalize_terminal(item: dict[str, Any]) -> dict[str, Any]:
    terminal = item.get("terminal", item.get("relay_port", item.get("channel", 1)))
    try:
        terminal_value = int(str(terminal).strip())
    except (TypeError, ValueError):
        terminal_value = 1
    mode = str(item.get("mode", "NO") or "NO").strip().upper()
    if mode not in {"NO", "NC"}:
        mode = "NO"
    press_seconds = item.get("press_seconds", 1)
    try:
        press_value: int | float = int(str(press_seconds).strip())
    except (TypeError, ValueError):
        press_value = 1
    return {"terminal": terminal_value, "mode": mode, "press_seconds": press_value}
