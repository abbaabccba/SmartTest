"""Explicit text-markup conversion; callers own HTML output sanitization."""

import markdown

__all__ = ['render_markup']


def render_markup(source: str, *, source_format='markdown') -> str:
    """Return an HTML fragment without fetching resources or trusting its content."""
    if source_format == 'jira-wiki':
        from jira2markdown import convert

        # Normalize platform line endings before the Wiki parser identifies table rows.
        source = convert(source.replace('\r\n', '\n').replace('\r', '\n'))
    elif source_format != 'markdown':
        raise ValueError(f'Unsupported markup format: {source_format}')
    return markdown.markdown(source, extensions=['extra', 'sane_lists'])
