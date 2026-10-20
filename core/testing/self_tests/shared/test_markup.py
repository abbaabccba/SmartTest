from concurrent.futures import ThreadPoolExecutor

import pytest

from core.markup import render_markup


def test_explicit_wiki_tables_multiline_links_and_colors():
    source = '||Name||Result||\n|A|one\\\\two\nthree|\n\n[https://example.com] {color:#ff0000}red{color}'
    html = render_markup(source, source_format='jira-wiki')
    assert '<table>' in html and '<td>one<br>two<br>three</td>' in html
    assert '<a href="https://example.com">' in html
    assert '<font color="#ff0000">red</font>' in html


def test_markdown_documents_do_not_share_parser_state_across_threads():
    sources = [f'[link][ref]\n\n[ref]: https://example.com/{index}' for index in range(24)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(render_markup, sources))
    for index, html in enumerate(results):
        assert html == f'<p><a href="https://example.com/{index}">link</a></p>'
    assert render_markup('[link][ref]') == '<p>[link][ref]</p>'
    assert render_markup('*bold*', source_format='jira-wiki') == '<p><strong>bold</strong></p>'
    assert render_markup('*italic*') == '<p><em>italic</em></p>'
    with pytest.raises(ValueError, match='Unsupported markup format'):
        render_markup('text', source_format='automatic')


def test_wiki_documents_convert_independently_across_threads():
    def convert(index):
        return render_markup(f'*document {index}* [https://example.com/{index}]', source_format='jira-wiki')

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(convert, range(16)))
    for index, html in enumerate(results):
        assert f'<strong>document {index}</strong>' in html
        assert f'href="https://example.com/{index}"' in html


@pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
@pytest.mark.parametrize('table', [
    '| |Notes|\n|Result|Pass|\n|Details|one\\\\two|\n',
    '||Name||Result||\n|A|one\ntwo|\n',
    'Before table\n\n||Name||Result||\n|A|[https://example.com]|\n\nAfter {color:red}red{color}',
])
def test_wiki_tables_preserve_structure_for_platform_line_endings(newline, table):
    expected = render_markup(table, source_format='jira-wiki')
    result = render_markup(table.replace('\n', newline), source_format='jira-wiki')
    assert '<table>' in expected and '<td>' in expected
    assert result == expected
