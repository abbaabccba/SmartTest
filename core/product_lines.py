from dataclasses import dataclass


@dataclass(frozen=True)
class ProductLine:
    name: str
    jira_project_keys: tuple[str, ...]
    confluence_business_label: str
    confluence_space_key: str = ""
    confluence_url: str = ""
    project_grouping: str = ""


PRODUCT_LINES = (
    ProductLine(
        "China Operator",
        ("IPTV",),
        "国内运营商",
        "DOPL",
        "https://confluence.amlogic.com/display/DOPL/Project+Space",
    ),
    ProductLine(
        "Smart Device",
        ("SH",),
        "智能设备",
        "SDPL",
        "https://confluence.amlogic.com/display/SDPL/Project+Space",
    ),
    ProductLine(
        "TV",
        ("TV",),
        "TV",
        "TV",
        "https://confluence.amlogic.com/display/TV/Project+Space",
        "launch_os",
    ),
    ProductLine(
        "Global Operator & STB",
        ("OTT",),
        "海外运营商",
        "OOPL",
        "https://confluence.amlogic.com/display/OOPL/Project+Space",
    ),
)
(
    CHINA_OPERATOR_BUSINESS,
    SMART_DEVICE_BUSINESS,
    TV_BUSINESS,
    GLOBAL_OPERATOR_STB_BUSINESS,
) = PRODUCT_LINES
WIRELESS_CONNECTION = ProductLine(
    "Wireless Connection",
    (),
    "互联",
    "WIRELESS",
)
DASHBOARD_PRODUCT_LINES = (*PRODUCT_LINES, WIRELESS_CONNECTION)
PERSONNEL_PRODUCT_LINES = tuple(line.name for line in DASHBOARD_PRODUCT_LINES)
PRODUCT_LINE_BY_NAME = {line.name: line for line in DASHBOARD_PRODUCT_LINES}
PRODUCT_LINE_BY_JIRA_PROJECT = {
    project_key: line for line in PRODUCT_LINES for project_key in line.jira_project_keys
}
PRODUCT_LINE_BY_CONFLUENCE_LABEL = {
    line.confluence_business_label: line for line in DASHBOARD_PRODUCT_LINES
}
PRODUCT_LINE_BY_CONFLUENCE_SPACE = {
    line.confluence_space_key: line for line in DASHBOARD_PRODUCT_LINES
}


def product_line_name(value: str) -> str:
    line = PRODUCT_LINE_BY_NAME.get(value) or PRODUCT_LINE_BY_CONFLUENCE_SPACE.get(value)
    if line is None and value.endswith(' Business'):
        line = PRODUCT_LINE_BY_NAME.get(value.removesuffix(' Business'))
    return line.name if line else value


def confluence_space_key(value: str) -> str:
    line = PRODUCT_LINE_BY_NAME.get(value)
    return line.confluence_space_key if line else value
