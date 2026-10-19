"""Product-line customizations shared by weekly scope and Confluence auditing."""

from core.product_lines import PRODUCT_LINES, PRODUCT_LINE_BY_NAME


PRODUCT_LINE_AUDIT_RULES = {
    PRODUCT_LINES[0].name: {
        'manual_project_urls': (
            'https://confluence.amlogic.com/display/DOPL/3.+Yak918-Test+Information',
            'https://confluence.amlogic.com/display/DOPL/3.+Turtle128-Test+Information',
            'https://confluence.amlogic.com/display/DOPL/3.+Owl422-Test+Information',
        ),
        'child_page_update_rule_ids': ('environment.setup',),
    },
    PRODUCT_LINES[1].name: {'exclude_support_mode_b': True},
}


def manual_project_product_line(url):
    return next(PRODUCT_LINE_BY_NAME[name] for name, rules in PRODUCT_LINE_AUDIT_RULES.items()
                if url in rules.get('manual_project_urls', ()))
