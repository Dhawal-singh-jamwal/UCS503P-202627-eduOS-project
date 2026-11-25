"""Shared theme, fullscreen, and popup presentation for EduOS applications."""

import json
import os
from pathlib import Path
import tempfile
from tkinter import ttk


PALETTES = {
    'light': {
        'background': '#f4f7fb', 'surface': '#ffffff', 'sidebar': '#172033',
        'sidebar_hover': '#24314d', 'sidebar_muted': '#aebbd0', 'text': '#172033',
        'muted': '#667085', 'border': '#d8e0ec', 'primary': '#2563eb',
        'primary_hover': '#1d4ed8', 'success': '#15803d', 'warning': '#b45309',
        'danger': '#b42318', 'soft': '#e9eef7', 'soft_hover': '#dfe7f3',
        'alternate': '#f8fafc', 'danger_hover': '#fee4e2',
        'code_background': '#101828', 'code_foreground': '#f2f4f7',
    },
    'dark': {
        'background': '#0f172a', 'surface': '#182235', 'sidebar': '#0b1220',
        'sidebar_hover': '#26334d', 'sidebar_muted': '#9eacc2', 'text': '#f8fafc',
        'muted': '#a5b4c7', 'border': '#334155', 'primary': '#3b82f6',
        'primary_hover': '#60a5fa', 'success': '#4ade80', 'warning': '#fbbf24',
        'danger': '#fb7185', 'soft': '#243147', 'soft_hover': '#334155',
        'alternate': '#1d293d', 'danger_hover': '#4c1d2b',
        'code_background': '#090f1d', 'code_foreground': '#f2f4f7',
    },
}


def _set_palette(mode):
    palette = PALETTES[mode]
    global BACKGROUND, SURFACE, SIDEBAR, SIDEBAR_HOVER, TEXT, MUTED, BORDER
    global PRIMARY, PRIMARY_HOVER, SUCCESS, WARNING, DANGER, CODE_BACKGROUND, CODE_FOREGROUND
    BACKGROUND = palette['background']
    SURFACE = palette['surface']
    SIDEBAR = palette['sidebar']
    SIDEBAR_HOVER = palette['sidebar_hover']
    TEXT = palette['text']
    MUTED = palette['muted']
    BORDER = palette['border']
    PRIMARY = palette['primary']
    PRIMARY_HOVER = palette['primary_hover']
    SUCCESS = palette['success']
    WARNING = palette['warning']
    DANGER = palette['danger']
    CODE_BACKGROUND = palette['code_background']
    CODE_FOREGROUND = palette['code_foreground']
    return palette


_set_palette('light')

FONT = 'DejaVu Sans'
MONO_FONT = 'DejaVu Sans Mono'


def theme_file():
    base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    return base / 'eduos/theme.json'


def load_theme():
    try:
        value = json.loads(theme_file().read_text(encoding='utf-8'))
        mode = value.get('mode') if isinstance(value, dict) else None
        return mode if mode in PALETTES else 'light'
    except (OSError, ValueError, UnicodeError):
        return 'light'


def save_theme(mode):
    if mode not in PALETTES:
        raise ValueError('Theme must be light or dark.')
    destination = theme_file()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.theme-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({'mode': mode}, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_theme(root, mode=None):
    """Install the persisted or requested EduOS visual theme."""
    mode = mode if mode in PALETTES else load_theme()
    palette = _set_palette(mode)
    root._eduos_theme = mode
    root.configure(bg=BACKGROUND)
    # Host-side safety tests intentionally provide a minimal tkinter stub.
    if not hasattr(ttk, 'Style'):
        return mode
    style = ttk.Style(root)
    style.theme_use('clam')

    style.configure('.', font=(FONT, 10), background=BACKGROUND, foreground=TEXT)
    style.configure('TFrame', background=BACKGROUND)
    style.configure('Surface.TFrame', background=SURFACE)
    style.configure('Card.TFrame', background=SURFACE, relief='flat')
    style.configure('Sidebar.TFrame', background=SIDEBAR)
    style.configure('TLabel', background=BACKGROUND, foreground=TEXT)
    style.configure('Surface.TLabel', background=SURFACE, foreground=TEXT)
    style.configure('Title.TLabel', background=BACKGROUND, foreground=TEXT, font=(FONT, 25, 'bold'))
    style.configure('PageTitle.TLabel', background=BACKGROUND, foreground=TEXT, font=(FONT, 18, 'bold'))
    style.configure('Heading.TLabel', background=BACKGROUND, foreground=TEXT, font=(FONT, 13, 'bold'))
    style.configure('Subtitle.TLabel', background=BACKGROUND, foreground=MUTED, font=(FONT, 10))
    style.configure('CardTitle.TLabel', background=SURFACE, foreground=TEXT, font=(FONT, 13, 'bold'))
    style.configure('Info.TLabel', background=SURFACE, foreground=MUTED, font=(FONT, 10))
    style.configure('Metric.TLabel', background=SURFACE, foreground=TEXT, font=(FONT, 23, 'bold'))
    style.configure('SidebarTitle.TLabel', background=SIDEBAR, foreground='#ffffff', font=(FONT, 17, 'bold'))
    style.configure('SidebarMuted.TLabel', background=SIDEBAR, foreground=palette['sidebar_muted'], font=(FONT, 9))
    for name, colour in (('Success', SUCCESS), ('Warning', WARNING), ('Danger', DANGER)):
        style.configure(f'{name}.TLabel', background=SURFACE, foreground=colour, font=(FONT, 10, 'bold'))

    style.configure('TButton', padding=(13, 8), relief='flat', borderwidth=1,
                    background=SURFACE, foreground=TEXT)
    style.map('TButton', background=[('active', palette['soft']), ('pressed', palette['soft_hover'])],
              foreground=[('disabled', MUTED)])
    style.configure('Primary.TButton', background=PRIMARY, foreground='#ffffff', borderwidth=0)
    style.map('Primary.TButton', background=[('active', PRIMARY_HOVER), ('pressed', PRIMARY)])
    style.configure('Danger.TButton', foreground=DANGER, background=SURFACE)
    style.map('Danger.TButton', background=[('active', palette['danger_hover'])])
    style.configure('Nav.TButton', anchor='w', padding=(16, 12), borderwidth=0,
                    background=SIDEBAR, foreground='#dce5f3')
    style.map('Nav.TButton', background=[('active', SIDEBAR_HOVER)])
    style.configure('Selected.Nav.TButton', anchor='w', padding=(16, 12), borderwidth=0,
                    background=PRIMARY, foreground='#ffffff')

    style.configure('TEntry', padding=(9, 7), fieldbackground=SURFACE, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
    style.configure('TCombobox', padding=(8, 6), fieldbackground=SURFACE,
                    background=SURFACE, foreground=TEXT)
    style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE,
                    foreground=TEXT, rowheight=34, borderwidth=0)
    style.configure('Treeview.Heading', background=palette['soft'], foreground=TEXT,
                    font=(FONT, 10, 'bold'), padding=(8, 9), relief='flat')
    style.map('Treeview', background=[('selected', PRIMARY)], foreground=[('selected', '#ffffff')])
    style.configure('TNotebook', background=BACKGROUND, borderwidth=0, tabmargins=(0, 0, 0, 8))
    style.configure('TNotebook.Tab', background=palette['soft'], foreground=MUTED,
                    padding=(18, 10), borderwidth=0, font=(FONT, 10, 'bold'))
    style.map('TNotebook.Tab', background=[('selected', SURFACE), ('active', palette['soft_hover'])],
              foreground=[('selected', PRIMARY), ('active', TEXT)])
    style.layout('Navigation.TNotebook.Tab', [])
    style.configure('Navigation.TNotebook', background=BACKGROUND, borderwidth=0)
    style.configure('TPanedwindow', background=BACKGROUND, sashwidth=6)
    style.configure('TSeparator', background=BORDER)
    refresh_classic_widgets(root)
    return mode


def configure_tree_tags(tree):
    """Add reusable zebra/status tags to a Treeview."""
    try:
        mode = getattr(tree.winfo_toplevel(), '_eduos_theme', load_theme())
    except (AttributeError, TypeError):
        mode = load_theme()
    palette = PALETTES.get(mode, PALETTES['light'])
    tree.tag_configure('even', background=SURFACE)
    tree.tag_configure('odd', background=palette['alternate'])
    tree.tag_configure('success', foreground=SUCCESS)
    tree.tag_configure('warning', foreground=WARNING)
    tree.tag_configure('danger', foreground=DANGER)


def refresh_classic_widgets(widget):
    """Update classic Tk widgets that do not inherit ttk style changes."""
    try:
        widget_class = widget.winfo_class()
    except (AttributeError, TypeError):
        return
    try:
        if widget_class == 'Canvas':
            widget.configure(background=BACKGROUND)
        elif widget_class == 'Text' and getattr(widget, '_eduos_code', False):
            code_widget(widget)
        elif widget_class in ('Text', 'Listbox'):
            widget.configure(background=SURFACE, foreground=TEXT, insertbackground=TEXT,
                             selectbackground=PRIMARY, selectforeground='#ffffff')
        elif widget_class == 'Treeview':
            configure_tree_tags(widget)
    except Exception:
        pass
    try:
        children = widget.winfo_children()
    except (AttributeError, TypeError):
        children = ()
    for child in children:
        refresh_classic_widgets(child)


def toggle_theme(root):
    """Toggle and atomically persist this Linux user's preference."""
    current = getattr(root, '_eduos_theme', load_theme())
    mode = 'dark' if current == 'light' else 'light'
    save_theme(mode)
    install_theme(root, mode)
    return mode


def theme_action(mode):
    return 'Use Light Mode' if mode == 'dark' else 'Use Dark Mode'


def set_fullscreen(window, enabled):
    """Keep XFCE panels below an active fullscreen dashboard."""
    enabled = bool(enabled)
    window.attributes('-topmost', enabled)
    window.attributes('-fullscreen', enabled)
    if enabled:
        window.lift()
    return enabled


def toggle_fullscreen(window):
    return set_fullscreen(window, not bool(window.attributes('-fullscreen')))


def protect_popup(window, parent):
    """Keep a popup above its fullscreen parent without exposing XFCE panels."""
    try:
        root = parent.winfo_toplevel()
    except AttributeError:
        root = parent
    try:
        window.transient(parent)
    except (AttributeError, TypeError):
        pass

    def reassert(_event=None):
        try:
            fullscreen = bool(root.attributes('-fullscreen'))
            if fullscreen:
                root.attributes('-fullscreen', True)
                root.attributes('-topmost', True)
            window.attributes('-topmost', fullscreen)
            window.lift()
        except Exception:
            pass

    try:
        window.bind('<Map>', reassert, add='+')
        window.after_idle(reassert)
    except (AttributeError, TypeError):
        pass
    return window


def code_widget(widget):
    """Apply the shared editor palette to a classic Tk Text widget."""
    widget.configure(background=CODE_BACKGROUND, foreground=CODE_FOREGROUND,
                     insertbackground='#ffffff', selectbackground=PRIMARY,
                     selectforeground='#ffffff', relief='flat', borderwidth=0,
                     padx=12, pady=10, font=(MONO_FONT, 11))
    widget._eduos_code = True
    return widget
