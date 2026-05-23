"""Qt Style Sheets for the dark and light themes.

Kept as plain strings rather than .qss files so PyInstaller doesn't need a
data-file hook — embedding straight into the python module survives both
source runs and frozen builds without configuration.

The design is loosely Fluent: ~8px rounded corners on cards and inputs,
4px on buttons, a single accent (#0078d4), and a muted secondary text
color that the GUI uses for "hint" labels.
"""

# Accent color shared across themes.
ACCENT = "#0078d4"
ACCENT_HOVER = "#1888e0"
ACCENT_PRESSED = "#005a9e"

DARK_QSS = f"""
* {{
    font-family: "Segoe UI", system-ui, sans-serif;
    color: #ffffff;
}}

QMainWindow, QDialog, QWidget#central {{
    background-color: #1e1e1e;
}}

QFrame#headerFrame {{
    background-color: transparent;
    padding: 4px 0;
}}

QLabel#titleLabel {{
    font-size: 18px;
    font-weight: 600;
    color: #ffffff;
}}

QLabel#muted, QLabel#metaLabel, QLabel#hintLabel, QLabel#groupCaption {{
    color: #9a9a9a;
}}

QLabel#hintLabel {{
    font-size: 11px;
}}

QLabel#groupCaption {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    padding-left: 4px;
}}

QLabel#emptyState {{
    color: #7a7a7a;
    font-style: italic;
}}

QGroupBox {{
    background-color: #252525;
    border: 1px solid #333333;
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 10px;
    color: #d0d0d0;
    background-color: transparent;
    margin-left: 8px;
    top: 2px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
    background-color: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QLineEdit:hover, QComboBox:hover, QTextEdit:hover {{
    border-color: #4a4a4a;
}}

QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{
    border-color: {ACCENT};
}}

QComboBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: #2a2a2a;
    border: 1px solid #3a3a3a;
    selection-background-color: {ACCENT};
    outline: 0;
    padding: 4px;
}}

QPushButton {{
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    border-radius: 5px;
    padding: 6px 14px;
    color: #ffffff;
    min-height: 22px;
}}

QPushButton:hover {{
    background-color: #383838;
    border-color: #4a4a4a;
}}

QPushButton:pressed {{
    background-color: #232323;
}}

QPushButton:disabled {{
    color: #6a6a6a;
    background-color: #232323;
    border-color: #2a2a2a;
}}

QPushButton#primaryButton {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background-color: {ACCENT_PRESSED};
}}

QRadioButton {{
    spacing: 8px;
    padding: 2px 0;
}}

QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border-radius: 8px;
    border: 1px solid #5a5a5a;
    background-color: #2a2a2a;
}}

QRadioButton::indicator:hover {{
    border-color: #7a7a7a;
}}

QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QTreeWidget, QTreeView, QTableView {{
    background-color: #1e1e1e;
    alternate-background-color: #232323;
    border: 1px solid #2f2f2f;
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    gridline-color: #2a2a2a;
    outline: 0;
}}

QTreeWidget::item, QTreeView::item {{
    padding: 5px 4px;
}}

QTreeWidget::item:hover, QTreeView::item:hover {{
    background-color: #2a2a2a;
}}

QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}

QHeaderView::section {{
    background-color: #2a2a2a;
    color: #c0c0c0;
    border: none;
    border-right: 1px solid #1e1e1e;
    padding: 7px 6px;
    font-weight: 500;
}}

QHeaderView::section:last {{
    border-right: none;
}}

QMenuBar {{
    background-color: #1e1e1e;
    color: #ffffff;
    border-bottom: 1px solid #2a2a2a;
    padding: 2px 6px;
}}

QMenuBar::item {{
    background-color: transparent;
    padding: 5px 10px;
    border-radius: 4px;
}}

QMenuBar::item:selected {{
    background-color: #2d2d2d;
}}

QMenu {{
    background-color: #252525;
    border: 1px solid #3a3a3a;
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}

QMenu::separator {{
    height: 1px;
    background-color: #3a3a3a;
    margin: 4px 8px;
}}

QStatusBar {{
    background-color: #181818;
    color: #b0b0b0;
    border-top: 1px solid #2a2a2a;
}}

QStatusBar::item {{
    border: none;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: #3a3a3a;
    border-radius: 6px;
    min-height: 30px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: #4a4a4a;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
    height: 0;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 12px;
}}

QScrollBar::handle:horizontal {{
    background-color: #3a3a3a;
    border-radius: 6px;
    min-width: 30px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: #4a4a4a;
}}

QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 4px;
}}

QSplitter::handle:vertical {{
    height: 4px;
}}

QToolTip {{
    background-color: #2d2d2d;
    color: #ffffff;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 4px 8px;
}}

QFrame#toast {{
    background-color: #2d2d2d;
    border: 1px solid #3a3a3a;
    border-radius: 8px;
}}

QFrame#toast[kind="success"] {{
    border-left: 3px solid #2ea043;
}}

QFrame#toast[kind="error"] {{
    border-left: 3px solid #d73a49;
}}

QFrame#toast[kind="info"] {{
    border-left: 3px solid {ACCENT};
}}
"""

LIGHT_QSS = f"""
* {{
    font-family: "Segoe UI", system-ui, sans-serif;
    color: #1a1a1a;
}}

QMainWindow, QDialog, QWidget#central {{
    background-color: #f3f3f3;
}}

QFrame#headerFrame {{
    background-color: transparent;
    padding: 4px 0;
}}

QLabel#titleLabel {{
    font-size: 18px;
    font-weight: 600;
    color: #1a1a1a;
}}

QLabel#muted, QLabel#metaLabel, QLabel#hintLabel, QLabel#groupCaption {{
    color: #707070;
}}

QLabel#hintLabel {{ font-size: 11px; }}

QLabel#groupCaption {{
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    padding-left: 4px;
}}

QLabel#emptyState {{
    color: #8a8a8a;
    font-style: italic;
}}

QGroupBox {{
    background-color: #ffffff;
    border: 1px solid #e1e1e1;
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 10px;
    color: #404040;
    background-color: transparent;
    margin-left: 8px;
    top: 2px;
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {{
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QLineEdit:hover, QComboBox:hover, QTextEdit:hover {{ border-color: #b0b0b0; }}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}

QComboBox::drop-down {{ border: none; width: 20px; }}

QComboBox QAbstractItemView {{
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    selection-background-color: {ACCENT};
    outline: 0;
    padding: 4px;
}}

QPushButton {{
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 5px;
    padding: 6px 14px;
    color: #1a1a1a;
    min-height: 22px;
}}

QPushButton:hover {{
    background-color: #f5f5f5;
    border-color: #b0b0b0;
}}

QPushButton:pressed {{ background-color: #eaeaea; }}

QPushButton:disabled {{
    color: #b0b0b0;
    background-color: #f5f5f5;
    border-color: #e1e1e1;
}}

QPushButton#primaryButton {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}

QPushButton#primaryButton:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{ background-color: {ACCENT_PRESSED}; }}

QRadioButton {{ spacing: 8px; padding: 2px 0; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid #b0b0b0; background-color: #ffffff;
}}
QRadioButton::indicator:hover {{ border-color: #707070; }}
QRadioButton::indicator:checked {{
    background-color: {ACCENT}; border-color: {ACCENT};
}}

QTreeWidget, QTreeView, QTableView {{
    background-color: #ffffff;
    alternate-background-color: #f7f7f7;
    border: 1px solid #e1e1e1;
    border-radius: 6px;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    gridline-color: #ececec;
    outline: 0;
}}

QTreeWidget::item, QTreeView::item {{ padding: 5px 4px; }}
QTreeWidget::item:hover, QTreeView::item:hover {{ background-color: #f0f0f0; }}
QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {ACCENT}; color: #ffffff;
}}

QHeaderView::section {{
    background-color: #f3f3f3;
    color: #404040;
    border: none;
    border-right: 1px solid #ffffff;
    padding: 7px 6px;
    font-weight: 500;
}}

QHeaderView::section:last {{ border-right: none; }}

QMenuBar {{
    background-color: #ffffff;
    color: #1a1a1a;
    border-bottom: 1px solid #e1e1e1;
    padding: 2px 6px;
}}

QMenuBar::item {{
    background-color: transparent;
    padding: 5px 10px;
    border-radius: 4px;
}}

QMenuBar::item:selected {{ background-color: #eaeaea; }}

QMenu {{
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
    padding: 4px;
}}

QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background-color: #e1e1e1; margin: 4px 8px; }}

QStatusBar {{
    background-color: #ebebeb;
    color: #404040;
    border-top: 1px solid #d0d0d0;
}}

QStatusBar::item {{ border: none; }}

QScrollBar:vertical {{ background-color: transparent; width: 12px; }}
QScrollBar::handle:vertical {{
    background-color: #c0c0c0; border-radius: 6px;
    min-height: 30px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #a0a0a0; }}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent; border: none; height: 0;
}}

QScrollBar:horizontal {{ background-color: transparent; height: 12px; }}
QScrollBar::handle:horizontal {{
    background-color: #c0c0c0; border-radius: 6px;
    min-width: 30px; margin: 2px;
}}

QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}

QToolTip {{
    background-color: #ffffff;
    color: #1a1a1a;
    border: 1px solid #d0d0d0;
    border-radius: 4px;
    padding: 4px 8px;
}}

QFrame#toast {{
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 8px;
}}

QFrame#toast[kind="success"] {{ border-left: 3px solid #2ea043; }}
QFrame#toast[kind="error"]   {{ border-left: 3px solid #d73a49; }}
QFrame#toast[kind="info"]    {{ border-left: 3px solid {ACCENT}; }}
"""


def qss_for(theme: str) -> str:
    return LIGHT_QSS if theme == "light" else DARK_QSS
