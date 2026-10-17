"""A consistent charcoal palette for ttk widgets and native Tk surfaces."""

from tkinter import ttk

BACKGROUND = '#202226'
TEXT = '#e8eaed'
MUTED_TEXT = '#a9b0ba'
ROW_BACKGROUND = '#25282d'
ALTERNATE_BACKGROUND = '#2d3137'
CATEGORY_BACKGROUND = '#393e46'

_SURFACE = '#2a2d33'
_FIELD = '#1c1e22'
_BORDER = '#454b55'
_HOVER = '#3b414b'
_ACCENT = '#416b97'
_ACCENT_HOVER = '#507eae'
_SELECTION = '#365b80'

MENU_STYLE = dict(background=_SURFACE, foreground=TEXT, activebackground=_SELECTION,
                  activeforeground=TEXT, disabledforeground=MUTED_TEXT, selectcolor=TEXT,
                  borderwidth=1)


def configure_theme(root):
    """Apply the application theme before creating widgets and dialogs."""
    root.configure(background=BACKGROUND)
    _configure_tk(root)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('DejaVu Sans', 10), background=BACKGROUND, foreground=TEXT,
                    bordercolor=_BORDER, lightcolor=_BORDER, darkcolor=_BORDER,
                    troughcolor=_FIELD, focuscolor=_ACCENT, selectbackground=_SELECTION,
                    selectforeground=TEXT)
    style.map('.', foreground=[('disabled', MUTED_TEXT)])
    for name in ('TFrame', 'TLabel', 'TLabelframe', 'TLabelframe.Label', 'TNotebook'):
        style.configure(name, background=BACKGROUND, foreground=TEXT)
    style.configure('Heading.TLabel', font=('DejaVu Sans', 12, 'bold'))
    style.configure('TSeparator', background=_BORDER)

    style.configure('TButton', background=_SURFACE, foreground=TEXT, padding=(10, 6),
                    bordercolor=_BORDER, lightcolor=_SURFACE, darkcolor=_SURFACE)
    style.map('TButton', background=[('disabled', _SURFACE), ('pressed', _SELECTION), ('active', _HOVER)],
              foreground=[('disabled', MUTED_TEXT)], bordercolor=[('focus', _ACCENT)],
              lightcolor=[('pressed', _SELECTION)], darkcolor=[('pressed', _SELECTION)])
    style.configure('Toolbar.TButton', padding=(8, 5))
    style.configure('Primary.TButton', background=_ACCENT, lightcolor=_ACCENT, darkcolor=_ACCENT)
    style.map('Primary.TButton', background=[('disabled', _SURFACE), ('pressed', _SELECTION),
                                            ('active', _ACCENT_HOVER)])

    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=_FIELD, foreground=TEXT, background=_SURFACE,
                        insertcolor=TEXT, arrowcolor=TEXT, padding=(5, 4),
                        selectbackground=_SELECTION, selectforeground=TEXT)
        style.map(name, fieldbackground=[('disabled', _SURFACE), ('readonly', _SURFACE)],
                  foreground=[('disabled', MUTED_TEXT)],
                  background=[('disabled', _SURFACE), ('active', _HOVER)],
                  bordercolor=[('focus', _ACCENT)], lightcolor=[('focus', _ACCENT)],
                  darkcolor=[('focus', _ACCENT)], arrowcolor=[('disabled', MUTED_TEXT)])
    style.map('TCombobox', selectbackground=[('readonly', _SURFACE)],
              selectforeground=[('readonly', TEXT)])
    style.configure('Toolbar.TCombobox', padding=(4, 5))

    style.configure('Treeview', background=ROW_BACKGROUND, fieldbackground=ROW_BACKGROUND,
                    foreground=TEXT, rowheight=25)
    style.map('Treeview', background=[('selected', _SELECTION)],
              foreground=[('disabled', MUTED_TEXT), ('selected', TEXT)])
    style.configure('Treeview.Heading', background=_SURFACE, foreground=TEXT,
                    font=('DejaVu Sans', 10, 'bold'), relief='flat', padding=(6, 5))
    style.map('Treeview.Heading', background=[('pressed', _SELECTION), ('active', _HOVER)])
    style.configure('TNotebook.Tab', background=_SURFACE, foreground=MUTED_TEXT, padding=(14, 8))
    style.map('TNotebook.Tab', background=[('selected', CATEGORY_BACKGROUND), ('active', _HOVER)],
              foreground=[('disabled', MUTED_TEXT), ('selected', TEXT), ('active', TEXT)],
              padding=[('selected', (14, 8))],
              lightcolor=[('selected', _BORDER), ('!selected', _BORDER)])
    style.configure('Portfolio.TNotebook', tabposition='wn')
    style.configure('Portfolio.TNotebook.Tab', padding=(12, 10), width=13, anchor='w')
    for direction in ('Horizontal', 'Vertical'):
        style.configure(f'{direction}.TScrollbar', background=_BORDER, troughcolor=BACKGROUND,
                        arrowcolor=TEXT, gripcount=0)
        style.map(f'{direction}.TScrollbar', background=[('pressed', _ACCENT), ('active', _HOVER)],
                  arrowcolor=[('disabled', MUTED_TEXT)])
        style.configure(f'{direction}.TProgressbar', background=_ACCENT, troughcolor=_FIELD,
                        lightcolor=_ACCENT, darkcolor=_ACCENT, bordercolor=_BORDER)


def _configure_tk(root):
    options = dict(background=BACKGROUND, foreground=TEXT, activeBackground=_HOVER,
                   activeForeground=TEXT, disabledForeground=MUTED_TEXT, insertBackground=TEXT,
                   selectBackground=_SELECTION, selectForeground=TEXT,
                   highlightBackground=BACKGROUND, highlightColor=_BORDER)
    for name, value in options.items():
        root.option_add(f'*{name}', value)
    for name, value in MENU_STYLE.items():
        root.option_add(f'*Menu.{name}', value)
    for name, value in (('background', _FIELD), ('foreground', TEXT),
                        ('selectBackground', _SELECTION), ('selectForeground', TEXT)):
        root.option_add(f'*TCombobox*Listbox.{name}', value)
