import { createElement, Sun, Moon, Copy, Check, Plus, Search, Paperclip, Send,
  Download, TrendingUp, TrendingDown, Menu, X, ChevronDown, GripVertical, ExternalLink } from 'lucide'

// One semantic mapping; Lucide ISC artwork is sized and colored by the public theme.
const icons = { sun: Sun, moon: Moon, copy: Copy, check: Check, add: Plus, search: Search,
  attach: Paperclip, send: Send, download: Download, 'trend-up': TrendingUp,
  'trend-down': TrendingDown, menu: Menu, close: X, 'chevron-down': ChevronDown,
  'drag': GripVertical, 'external-link': ExternalLink }

export function icon(name, attributes = {}) {
  return createElement(icons[name], { class: 'st-icon', 'stroke-width': 1.75,
    'aria-hidden': 'true', focusable: 'false', ...attributes }).outerHTML
}
