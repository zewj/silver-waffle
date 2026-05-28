"""PySide6 GUI for the multi-instance manager.

Single window, Fluent-ish dark/light theming, in-place table updates so
resize stays smooth, native DPI handling via Qt, plus a few real
animations (window fade-in, toast notifications, status-bar flash on
error). All the underlying logic — manager, accounts, antiafk, etc. —
is framework-agnostic and unchanged.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt,
    QTimer, Signal,
)
from PySide6.QtGui import QAction, QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFormLayout,
    QFrame, QGraphicsOpacityEffect, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMenuBar,
    QMessageBox, QPushButton, QRadioButton, QSizePolicy, QSpacerItem,
    QSplitter, QStatusBar, QTextEdit, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import browser_login, discord_bot, launcher, logging_setup, screenshot, servers, webhook, wipe
from .accounts import AccountStore
from .config import ConfigStore, Preset
from .manager import InstanceManager
from .styles import qss_for

log = logging.getLogger(__name__)

_NO_ACCOUNT = "(launcher's signed-in account)"
_STATS_REFRESH_MS = 2000

_AFK_ON = "● on"
_AFK_OFF = "○ off"
_STATUS_GLYPH = {
    "running": "● running",
    "starting": "◌ starting",
    "crashed": "✕ crashed",
    "closed": "■ closed",
}

# Treeview column indices for the running-instances table.
_COL_LABEL, _COL_ACCOUNT, _COL_PLACE, _COL_PID, _COL_STATUS, \
    _COL_AFK, _COL_CPU, _COL_RAM, _COL_JOB = range(9)


# ---------------------------------------------------------------------------
# Windows-only chrome helpers


def _apply_dark_title_bar(window, dark: bool) -> None:
    """Flip the OS-drawn title bar between dark and light on Windows.

    Qt doesn't restyle the system title bar even when its content is dark;
    `DwmSetWindowAttribute` is the supported way to do it. Tries the
    Win11/20H1+ attribute (20) then the older Win10 1809 attribute (19).
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        if not hwnd:
            return
        value = ctypes.c_int(1 if dark else 0)
        dwmapi = ctypes.windll.dwmapi
        for attr in (20, 19):
            try:
                dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value),
                )
            except OSError:
                continue
    except Exception:
        log.exception("could not apply dark title bar")


# ---------------------------------------------------------------------------
# Toast notification


class Toast(QFrame):
    """Bottom-right slide-in toast. Fades out after `duration_ms`."""

    def __init__(self, parent: QWidget, message: str, kind: str = "info",
                 duration_ms: int = 2800):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("kind", kind)
        # Dynamic property selectors (`QFrame#toast[kind="success"]`) only
        # apply after the style engine re-polishes the widget; without
        # this the colored left-border never appears.
        self.style().unpolish(self)
        self.style().polish(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)
        label = QLabel(message)
        label.setWordWrap(False)
        layout.addWidget(label)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._duration_ms = duration_ms
        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(180)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_out.setDuration(280)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self.deleteLater)

    def show_at(self, anchor_bottom_right: QPoint):
        self.adjustSize()
        size = self.sizeHint()
        x = anchor_bottom_right.x() - size.width() - 16
        y = anchor_bottom_right.y() - size.height() - 16
        # Slide up from 12 px below the final position.
        self.move(QPoint(x, y + 12))
        self.show()
        self.raise_()
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(220)
        slide.setStartValue(QPoint(x, y + 12))
        slide.setEndValue(QPoint(x, y))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        slide.start(QPropertyAnimation.DeleteWhenStopped)
        self._fade_in.start()
        QTimer.singleShot(self._duration_ms, self._fade_out.start)


# ---------------------------------------------------------------------------
# Main window


class MainWindow(QMainWindow):
    # Carries (result, error, on_done callback) from worker threads back to
    # the GUI thread. Qt makes the slot run on the main thread automatically.
    _async_done = Signal(object, object, object)
    # Emitted by the manager's stats-poll thread when a PID dies. Connected
    # to a slot below so the actual webhook POST runs on the GUI thread.
    _crashed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Multi Roblox Manager")
        self.resize(1120, 720)
        self.setMinimumSize(960, 580)

        self.log_path = logging_setup.setup()
        log.info("Multi Roblox Manager (Qt) starting; log file at %s", self.log_path)

        self.store = AccountStore()
        self.config = ConfigStore()
        self.manager = InstanceManager()
        self.manager.LAUNCH_COOLDOWN = self.config.cfg.launch_cooldown
        self.manager.launch_mode = self.config.cfg.launch_mode
        self.manager.account_isolation = self.config.cfg.account_isolation
        # Fire-and-forget Discord webhook when an instance crashes. The
        # manager calls this from its 2s stats poll thread, so the slot
        # has to be thread-safe — emitting a queued signal is the simplest
        # way to keep all webhook bookkeeping on the GUI thread.
        self.manager.on_crash_callback = self._crashed.emit
        self._crashed.connect(self._on_instance_crashed)
        self.manager.start()

        self.roblox_version = launcher.detect_version() or "unknown"
        log.info("detected Roblox version: %s", self.roblox_version)

        # Stable instance-id → QTreeWidgetItem cache so the periodic
        # stats refresh updates rows in place instead of rebuilding the
        # whole tree (eliminates resize-time stutter).
        self._tree_items: dict[int, QTreeWidgetItem] = {}
        self._preset_items: dict[int, QTreeWidgetItem] = {}

        self._build_menubar()
        self._build_ui()
        self._async_done.connect(self._finish_async)

        # Apply theme + paint the OS title bar.
        self._apply_theme(self.config.cfg.theme, persist=False)

        self._refresh_accounts_dropdown()
        self._refresh_presets()
        self._refresh_tree()

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(_STATS_REFRESH_MS)
        self._stats_timer.timeout.connect(self._on_stats_tick)
        self._stats_timer.start()

        # Discord bot, started on first launch if enabled in config.
        self.bot: Optional[discord_bot.ManagerBot] = None
        if self.config.cfg.bot_enabled and self.config.cfg.bot_token:
            QTimer.singleShot(200, self._auto_start_bot)

        # Subtle fade-in on first paint so the unstyled flash before QSS
        # applies doesn't show through.
        self.setWindowOpacity(0.0)
        QTimer.singleShot(0, self._fade_in)

    # ---- menu / chrome ---------------------------------------------------

    def _build_menubar(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        act_logs = QAction("Open Logs Folder", self)
        act_logs.triggered.connect(self._open_logs)
        file_menu.addAction(act_logs)
        file_menu.addSeparator()
        # "Nuke everything we ever wrote on disk and exit." Confirmed with
        # an explicit type-to-confirm dialog so it can't fire accidentally.
        act_wipe = QAction("Wipe All App Data…", self)
        act_wipe.triggered.connect(self._on_wipe_data)
        file_menu.addAction(act_wipe)
        file_menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        accounts_menu = bar.addMenu("&Accounts")
        act_acc = QAction("Manage Accounts…", self)
        act_acc.triggered.connect(self._open_account_manager)
        accounts_menu.addAction(act_acc)

        view_menu = bar.addMenu("&View")
        act_theme = QAction("Toggle Dark / Light", self)
        act_theme.setShortcut("Ctrl+T")
        act_theme.triggered.connect(self._on_toggle_theme)
        view_menu.addAction(act_theme)

        settings_menu = bar.addMenu("&Settings")
        act_webhook = QAction("Notifications…", self)
        act_webhook.triggered.connect(self._open_webhook_settings)
        settings_menu.addAction(act_webhook)
        act_bot = QAction("Discord Bot…", self)
        act_bot.triggered.connect(self._open_bot_settings)
        settings_menu.addAction(act_bot)
        settings_menu.addSeparator()
        # Checkable toggle for the LOCALAPPDATA isolation. When OFF,
        # launches from the user's browser (Play button on roblox.com)
        # see the same on-disk state as managed launches — useful when
        # mixing manager + browser launches confused account routing.
        self.act_isolation = QAction("Per-account isolation", self, checkable=True)
        self.act_isolation.setChecked(self.config.cfg.account_isolation)
        self.act_isolation.triggered.connect(self._on_toggle_isolation)
        settings_menu.addAction(self.act_isolation)

        help_menu = bar.addMenu("&Help")
        act_about = QAction("About", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 4)
        root.setSpacing(8)

        # ---- header strip ----
        header = QFrame()
        header.setObjectName("headerFrame")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(12)
        title = QLabel("Multi Roblox Manager")
        title.setObjectName("titleLabel")
        hl.addWidget(title)
        self.meta_label = QLabel(f"Roblox {self.roblox_version}")
        self.meta_label.setObjectName("metaLabel")
        hl.addWidget(self.meta_label)
        hl.addStretch(1)
        root.addWidget(header)

        # ---- launch card ----
        root.addWidget(self._build_launch_card())

        # ---- body splitter ----
        self.body = QSplitter(Qt.Horizontal)
        self.body.setHandleWidth(6)
        self.body.setChildrenCollapsible(False)
        root.addWidget(self.body, 1)
        self.body.addWidget(self._build_running_pane())
        self.body.addWidget(self._build_presets_pane())
        self.body.setStretchFactor(0, 3)
        self.body.setStretchFactor(1, 2)

        # ---- action bar ----
        root.addWidget(self._build_action_bar())

        # ---- status bar ----
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel(
            f"Ready • Roblox {self.roblox_version} • mutex held • "
            "per-account ticket auth & profile isolation enabled"
        )
        sb.addWidget(self._status_label, 1)

    def _build_launch_card(self) -> QGroupBox:
        card = QGroupBox("Launch")
        grid = QGridLayout(card)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(12, 18, 12, 12)

        # Row 0: Game | Account
        grid.addWidget(QLabel("Game"), 0, 0)
        self.place_edit = QLineEdit()
        self.place_edit.setPlaceholderText("placeId or roblox.com/games/<id>/… URL")
        self.place_edit.setToolTip(
            "Numeric placeId (e.g. 920587237) or a roblox.com /games/<id>/… URL"
        )
        self.place_edit.returnPressed.connect(self._on_launch)
        grid.addWidget(self.place_edit, 0, 1)

        grid.addWidget(QLabel("Account"), 0, 2)
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(220)
        grid.addWidget(self.account_combo, 0, 3)

        # Row 1: Label | buttons
        grid.addWidget(QLabel("Label"), 1, 0)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("optional")
        self.label_edit.returnPressed.connect(self._on_launch)
        grid.addWidget(self.label_edit, 1, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.launch_btn = QPushButton("Launch")
        self.launch_btn.setObjectName("primaryButton")
        self.launch_btn.clicked.connect(self._on_launch)
        self.launch_btn.setShortcut("Ctrl+Return")
        self.launch_btn.setToolTip("Launch a new Roblox instance (Ctrl+Enter)")
        btn_row.addWidget(self.launch_btn)
        save_btn = QPushButton("Save Preset")
        save_btn.clicked.connect(self._on_save_preset)
        save_btn.setToolTip("Remember this label/place/account combo across restarts")
        btn_row.addWidget(save_btn)
        btn_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(btn_row)
        grid.addWidget(wrap, 1, 2, 1, 2)

        # Row 2: launch mode
        mode_row = QHBoxLayout()
        mode_row.setSpacing(14)
        mode_row.addWidget(QLabel("Launch via:"))
        self.proto_radio = QRadioButton("Roblox launcher (recommended)")
        self.proto_radio.setToolTip(
            "Goes through RobloxPlayerLauncher.exe — stable, handles updates, "
            "Hyperion-friendly"
        )
        self.direct_radio = QRadioButton("Direct (skip launcher)")
        self.direct_radio.setToolTip(
            "Spawns RobloxPlayerBeta.exe directly — faster start, skips update check"
        )
        (self.proto_radio if self.config.cfg.launch_mode == "protocol"
         else self.direct_radio).setChecked(True)
        self.proto_radio.toggled.connect(
            lambda checked: checked and self._on_mode_changed("protocol")
        )
        self.direct_radio.toggled.connect(
            lambda checked: checked and self._on_mode_changed("direct")
        )
        mode_row.addWidget(self.proto_radio)
        mode_row.addWidget(self.direct_radio)
        mode_row.addStretch(1)
        mode_wrap = QWidget()
        mode_wrap.setLayout(mode_row)
        grid.addWidget(mode_wrap, 2, 0, 1, 4)

        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(3, 1)
        return card

    def _build_running_pane(self) -> QWidget:
        box = QGroupBox("Running instances")
        wrap = QVBoxLayout(box)
        wrap.setContentsMargins(10, 18, 10, 10)
        wrap.setSpacing(6)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(9)
        self.tree.setHeaderLabels([
            "Label", "Account", "Place", "PID", "Status",
            "Anti-AFK", "CPU %", "RAM MB", "Server (jobId)",
        ])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        header = self.tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.Interactive)
        # Sized so the eight informational columns fit in the default
        # splitter weight (~60% of 1120px); jobId stretches to fill
        # whatever's left and grows when the user drags the splitter.
        widths = [100, 110, 85, 55, 90, 75, 55, 65]  # 9th col stretches
        for i, w in enumerate(widths):
            self.tree.setColumnWidth(i, w)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        wrap.addWidget(self.tree, 1)

        # Empty-state overlay, shown when no instances are running.
        self.empty_state = QLabel(
            "No instances running.\n\n"
            "Paste a placeId or game URL above, pick an account, click Launch."
        )
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignCenter)
        self.empty_state.setParent(self.tree.viewport())
        self.empty_state.hide()
        # Reposition the overlay when the tree resizes.
        self.tree.viewport().installEventFilter(self)

        hint = QLabel(
            "Ctrl/Shift-click for multi-select • "
            "Click the Anti-AFK cell to toggle that row"
        )
        hint.setObjectName("hintLabel")
        wrap.addWidget(hint)
        return box

    def _build_presets_pane(self) -> QWidget:
        box = QGroupBox("Saved presets")
        wrap = QVBoxLayout(box)
        wrap.setContentsMargins(10, 18, 10, 10)
        wrap.setSpacing(8)

        self.preset_tree = QTreeWidget()
        self.preset_tree.setColumnCount(3)
        self.preset_tree.setHeaderLabels(["Label", "Place", "Account"])
        self.preset_tree.setRootIsDecorated(False)
        self.preset_tree.setAlternatingRowColors(True)
        self.preset_tree.setUniformRowHeights(True)
        self.preset_tree.header().setStretchLastSection(True)
        # Account column stretches; the first two are sized for the right
        # pane's ~40% splitter weight.
        for i, w in enumerate((120, 85)):
            self.preset_tree.setColumnWidth(i, w)
        self.preset_tree.itemDoubleClicked.connect(lambda _i, _c: self._on_launch_preset())
        wrap.addWidget(self.preset_tree, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        b1 = QPushButton("Launch")
        b1.clicked.connect(self._on_launch_preset)
        b2 = QPushButton("Launch All")
        b2.clicked.connect(self._on_launch_all_presets)
        b3 = QPushButton("Remove")
        b3.clicked.connect(self._on_remove_preset)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addWidget(b3)
        row.addStretch(1)
        wrap.addLayout(row)
        return box

    def _build_action_bar(self) -> QWidget:
        wrap = QFrame()
        wrap.setObjectName("actionBar")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        def make_group(caption: str, *buttons: QPushButton) -> QWidget:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            cap = QLabel(caption)
            cap.setObjectName("groupCaption")
            v.addWidget(cap)
            h = QHBoxLayout()
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            for b in buttons:
                h.addWidget(b)
            v.addLayout(h)
            return box

        def sep() -> QFrame:
            line = QFrame()
            line.setFrameShape(QFrame.VLine)
            line.setFrameShadow(QFrame.Plain)
            line.setStyleSheet("color: #3a3a3a;")
            return line

        focus_btn = QPushButton("Focus")
        focus_btn.clicked.connect(self._on_focus)
        cycle_btn = QPushButton("Cycle")
        cycle_btn.setShortcut("Ctrl+Tab")
        cycle_btn.setToolTip("Rotate focus to the next running instance (Ctrl+Tab)")
        cycle_btn.clicked.connect(self._cycle)
        layout.addWidget(make_group("WINDOW", focus_btn, cycle_btn))

        layout.addWidget(sep())

        hop_btn = QPushButton("Hop")
        hop_btn.setToolTip(
            "Pick a fresh public server and hop the selected instance(s) "
            "without the close-and-reopen flash"
        )
        hop_btn.clicked.connect(self._on_hop)
        layout.addWidget(make_group("SERVER", hop_btn))

        layout.addWidget(sep())

        afk_toggle = QPushButton("Toggle")
        afk_toggle.setToolTip("Toggle Anti-AFK on the selected instance(s)")
        afk_toggle.clicked.connect(self._on_toggle_antiafk)
        afk_on = QPushButton("Enable All")
        afk_on.clicked.connect(lambda: self._on_set_antiafk_all(True))
        afk_off = QPushButton("Disable All")
        afk_off.clicked.connect(lambda: self._on_set_antiafk_all(False))
        layout.addWidget(make_group("ANTI-AFK", afk_toggle, afk_on, afk_off))

        layout.addWidget(sep())

        close_btn = QPushButton("Close")
        close_btn.setToolTip("Terminate the selected instance(s)")
        close_btn.clicked.connect(self._on_close_instance)
        layout.addWidget(make_group("INSTANCE", close_btn))

        layout.addStretch(1)
        return wrap

    # ---- theming ---------------------------------------------------------

    def _apply_theme(self, requested: str, persist: bool = True) -> None:
        theme = requested if requested in ("dark", "light") else "dark"
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss_for(theme))
        _apply_dark_title_bar(self, dark=(theme == "dark"))
        if persist:
            self.config.cfg.theme = theme
            self.config.save()
        # Per-cell foreground colors (e.g. crashed rows in red) are set
        # imperatively in _fill_row, so they don't auto-update when QSS
        # changes — refresh the tree to pick up the new theme's colors.
        if hasattr(self, "tree"):
            self._refresh_tree()
        log.info("theme applied: %s", theme)

    def _on_toggle_theme(self):
        new = "light" if self.config.cfg.theme == "dark" else "dark"
        self._apply_theme(new)
        self._set_status(f"Theme: {new}.")

    # ---- fade in ---------------------------------------------------------

    def _fade_in(self):
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    # ---- helpers ---------------------------------------------------------

    def _set_status(self, msg: str, kind: str = "info"):
        self._status_label.setText(msg)
        if kind == "error":
            self._toast(msg, kind="error", duration_ms=4500)

    def _toast(self, message: str, kind: str = "info", duration_ms: int = 2800):
        toast = Toast(self, message, kind=kind, duration_ms=duration_ms)
        # Bottom-right of the main window, just above the status bar.
        # Toast is a child widget so this is already in local coords.
        anchor = QPoint(self.width(), self.height() - self.statusBar().height())
        toast.show_at(anchor)

    def _refresh_accounts_dropdown(self):
        current = self.account_combo.currentText()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem(_NO_ACCOUNT)
        for acc in self.store:
            self.account_combo.addItem(acc.label())
        idx = self.account_combo.findText(current)
        self.account_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.account_combo.blockSignals(False)

    def _resolve_selected_account(self):
        choice = self.account_combo.currentText()
        if not choice or choice == _NO_ACCOUNT:
            return None
        return next((a for a in self.store if a.label() == choice), None)

    # ---- running-instances tree ----------------------------------------

    def _selected_instances(self):
        out = []
        wanted = {id(i): i for i in self.manager.instances}
        for item in self.tree.selectedItems():
            inst_id = item.data(0, Qt.UserRole)
            inst = wanted.get(inst_id)
            if inst is not None:
                out.append(inst)
        return out

    def _refresh_tree(self):
        wanted: dict[int, "Instance"] = {id(i): i for i in self.manager.instances}

        # Remove rows for instances that no longer exist.
        for inst_id in list(self._tree_items.keys()):
            if inst_id not in wanted:
                item = self._tree_items.pop(inst_id)
                idx = self.tree.indexOfTopLevelItem(item)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)

        # Insert / update.
        for inst_id, inst in wanted.items():
            item = self._tree_items.get(inst_id)
            if item is None:
                item = QTreeWidgetItem()
                item.setData(0, Qt.UserRole, inst_id)
                self.tree.addTopLevelItem(item)
                self._tree_items[inst_id] = item
            self._fill_row(item, inst)

        # Empty-state overlay.
        if not wanted:
            self.empty_state.show()
            self._position_empty_state()
        else:
            self.empty_state.hide()

    def _fill_row(self, item: QTreeWidgetItem, inst):
        s = inst.last_sample
        item.setText(_COL_LABEL, inst.label)
        item.setText(_COL_ACCOUNT, inst.account.label() if inst.account else _NO_ACCOUNT)
        item.setText(_COL_PLACE, str(inst.place_id))
        item.setText(_COL_PID, str(inst.pid) if inst.pid else "—")
        item.setText(_COL_STATUS, _STATUS_GLYPH.get(inst.status, inst.status))
        item.setText(_COL_AFK, _AFK_ON if inst.antiafk_on else _AFK_OFF)
        item.setText(_COL_CPU, f"{s.cpu_percent:.0f}" if s.alive else "—")
        item.setText(_COL_RAM, f"{s.rss_mb:.0f}" if s.alive else "—")
        item.setText(_COL_JOB, inst.job_id or "—")
        # Right-align the numeric columns.
        for col in (_COL_CPU, _COL_RAM):
            item.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)
        item.setTextAlignment(_COL_AFK, Qt.AlignCenter)
        # Red text for crashed rows; clear otherwise.
        red = QColor("#ff5e5e") if self.config.cfg.theme == "dark" else QColor("#c81e1e")
        default_color = QColor("#ffffff") if self.config.cfg.theme == "dark" else QColor("#1a1a1a")
        color = red if inst.status == "crashed" else default_color
        for col in range(9):
            item.setForeground(col, color)

    def _position_empty_state(self):
        vp = self.tree.viewport()
        self.empty_state.setGeometry(0, 0, vp.width(), vp.height())

    def eventFilter(self, obj, event):
        if obj is self.tree.viewport() and self.empty_state.isVisible():
            from PySide6.QtCore import QEvent
            if event.type() in (QEvent.Resize, QEvent.Show):
                self._position_empty_state()
        return super().eventFilter(obj, event)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        if column != _COL_AFK:
            return
        inst_id = item.data(0, Qt.UserRole)
        inst = next((i for i in self.manager.instances if id(i) == inst_id), None)
        if inst:
            self._toggle_antiafk_for(inst)

    # ---- presets tree ----------------------------------------------------

    def _refresh_presets(self):
        self.preset_tree.clear()
        self._preset_items.clear()
        for idx, p in enumerate(self.config.cfg.presets):
            account = self.store.find(p.account_user_id) if p.account_user_id else None
            acc_label = account.label() if account else (
                "[missing account]" if p.account_user_id else "(signed-in)"
            )
            item = QTreeWidgetItem([p.label, str(p.place_id), acc_label])
            item.setData(0, Qt.UserRole, idx)
            self.preset_tree.addTopLevelItem(item)
            self._preset_items[idx] = item

    def _selected_preset_index(self) -> Optional[int]:
        items = self.preset_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.UserRole)

    # ---- background work / async dispatch ------------------------------

    def _run_async(self, fn, on_done=None):
        def worker():
            try:
                result, err = fn(), None
            except Exception as e:
                log.exception("async worker raised")
                result, err = None, e
            self._async_done.emit(result, err, on_done)
        threading.Thread(target=worker, daemon=True).start()

    def _finish_async(self, result, err, on_done):
        if err:
            # Rate limit is common enough that we shouldn't pop a modal —
            # a toast is plenty since the user can just try again later.
            from .auth import RateLimitError
            if isinstance(err, RateLimitError):
                self._set_status(str(err), kind="error")
            else:
                self._set_status(f"Error: {err}", kind="error")
                QMessageBox.critical(self, "Error", str(err))
        elif on_done:
            on_done(result)
        self._refresh_tree()

    def _on_stats_tick(self):
        try:
            self.manager.refresh_stats()
            self._refresh_tree()
        except Exception:
            log.exception("stats refresh failed")
        # Surface the rate-limit countdown directly in the status bar so
        # the user knows "wait, then click" instead of "click, see error,
        # click again, see error".
        remaining = self.manager.rate_limited_seconds_remaining
        if remaining > 0:
            self._set_status(
                f"Rate-limited by Roblox • {int(remaining + 1)}s until next "
                f"launch / hop allowed • use Focus to switch already-running accounts"
            )

    def _on_instance_crashed(self, inst):
        """Slot for the _crashed signal. Runs on the GUI thread."""
        self._toast(f"{inst.label} crashed", kind="error", duration_ms=4500)
        cfg = self.config.cfg
        if cfg.webhook_on_crash and cfg.webhook_url:
            webhook.post_crash(cfg.webhook_url, inst)

    # ---- actions: launch / hop / focus / close ------------------------

    def _on_launch(self):
        place_id = servers.parse_place_id(self.place_edit.text())
        if not place_id:
            QMessageBox.warning(
                self, "Invalid game",
                "Enter a numeric placeId or roblox.com /games/<id>/ URL.",
            )
            return
        account = self._resolve_selected_account()
        label = (self.label_edit.text().strip()
                 or (account.label() if account else f"Instance {len(self.manager.instances) + 1}"))
        self.config.remember(place_id)
        self._set_status(f"Launching {label}…")
        self._run_async(
            lambda: self.manager.add_instance(label, place_id, account=account),
            on_done=lambda inst: (
                self._set_status(
                    f"{inst.label} launched (pid={inst.pid})" if inst.pid
                    else f"{inst.label}: launcher started, pid not detected"
                ),
                self._toast(f"Launched {inst.label}", kind="success"),
            ),
        )

    def _on_focus(self):
        targets = self._selected_instances()
        if not targets:
            return
        inst = targets[0]
        ok = self.manager.focus(inst)
        self._set_status(f"Focused {inst.label}" if ok else f"Could not focus {inst.label}")

    def _cycle(self):
        target = self.manager.cycle_focus()
        if target:
            self._set_status(f"Focused {target.label}")

    def _on_hop(self):
        targets = self._selected_instances()
        if not targets:
            return
        self._set_status(f"Server-hopping {len(targets)} instance(s)…")

        def run_all():
            results = []
            for inst in targets:
                try:
                    job = self.manager.server_hop(inst)
                    results.append((inst.label, job or "(no server)"))
                except Exception as e:
                    log.exception("hop failed for %s", inst.label)
                    results.append((inst.label, f"error: {e}"))
            return results

        self._run_async(
            run_all,
            on_done=lambda results: (
                self._set_status(
                    "Hop done: " + ", ".join(f"{lbl}→{j}" for lbl, j in results)
                ),
                self._toast(f"Hopped {len(results)} instance(s)", kind="success"),
            ),
        )

    def _on_close_instance(self):
        targets = self._selected_instances()
        if not targets:
            return
        for inst in targets:
            self.manager.close(inst)
        self._refresh_tree()
        self._set_status(f"Closed {len(targets)} instance(s).")

    def _toggle_antiafk_for(self, inst):
        new_state = self.manager.set_antiafk(inst, not inst.antiafk_on)
        self._set_status(f"Anti-AFK {'on' if new_state else 'off'} for {inst.label}.")
        self._refresh_tree()

    def _on_toggle_antiafk(self):
        for inst in self._selected_instances():
            self._toggle_antiafk_for(inst)

    def _on_set_antiafk_all(self, enabled: bool):
        count = self.manager.set_antiafk_all(enabled)
        self._set_status(
            f"Anti-AFK {'enabled' if enabled else 'disabled'} on {count} instance(s)."
        )
        self._refresh_tree()

    # ---- preset actions ------------------------------------------------

    def _on_save_preset(self):
        place_id = servers.parse_place_id(self.place_edit.text())
        if not place_id:
            QMessageBox.warning(self, "Invalid", "Enter a placeId or URL to save.")
            return
        account = self._resolve_selected_account()
        label = (self.label_edit.text().strip()
                 or (account.label() if account else f"Preset {len(self.config.cfg.presets) + 1}"))
        self.config.upsert(Preset(
            label=label, place_id=place_id,
            account_user_id=account.user_id if account else None,
        ))
        self._refresh_presets()
        self._set_status(f"Saved preset '{label}'.")
        self._toast(f"Saved preset '{label}'", kind="success")

    def _launch_preset(self, preset: Preset):
        account = self.store.find(preset.account_user_id) if preset.account_user_id else None
        if preset.account_user_id and account is None:
            self._set_status(f"Preset '{preset.label}': saved account missing; using signed-in.")
        self._set_status(f"Launching {preset.label}…")
        self._run_async(
            lambda: self.manager.add_instance(preset.label, preset.place_id, account=account),
            on_done=lambda inst: self._set_status(
                f"{inst.label} launched (pid={inst.pid})" if inst.pid
                else f"{inst.label}: pid not detected"
            ),
        )

    def _on_launch_preset(self):
        idx = self._selected_preset_index()
        if idx is None:
            return
        self._launch_preset(self.config.cfg.presets[idx])

    def _on_launch_all_presets(self):
        presets = list(self.config.cfg.presets)
        if not presets:
            return
        self._set_status(f"Launching {len(presets)} preset(s)…")

        def run_all():
            results = []
            for p in presets:
                account = self.store.find(p.account_user_id) if p.account_user_id else None
                try:
                    inst = self.manager.add_instance(p.label, p.place_id, account=account)
                    results.append((p.label, inst.pid))
                except Exception as e:
                    log.exception("preset launch failed: %s", p.label)
                    results.append((p.label, f"error: {e}"))
            return results

        self._run_async(
            run_all,
            on_done=lambda results: self._set_status(
                "Launched: " + ", ".join(f"{lbl}({pid})" for lbl, pid in results)
            ),
        )

    def _on_remove_preset(self):
        idx = self._selected_preset_index()
        if idx is None:
            return
        preset = self.config.cfg.presets[idx]
        if QMessageBox.question(
            self, "Remove", f"Remove preset '{preset.label}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.config.remove(idx)
        self._refresh_presets()
        self._set_status(f"Removed preset '{preset.label}'.")

    # ---- mode / accounts / lifecycle ----------------------------------

    def _on_mode_changed(self, mode: str):
        self.manager.launch_mode = mode
        self.config.cfg.launch_mode = mode
        self.config.save()
        self._set_status(
            "Launches will go through RobloxPlayerLauncher."
            if mode == "protocol"
            else "Launches will spawn RobloxPlayerBeta directly."
        )

    def _open_account_manager(self):
        dlg = AccountManagerDialog(self, self.store)
        dlg.account_changed.connect(self._on_accounts_changed)
        dlg.exec()

    def _open_webhook_settings(self):
        dlg = WebhookSettingsDialog(self, self.config)
        dlg.exec()

    def _on_toggle_isolation(self, checked: bool):
        self.manager.account_isolation = checked
        self.config.cfg.account_isolation = checked
        self.config.save()
        if checked:
            self._set_status(
                "Per-account isolation ON — alts write to per-account dirs; "
                "browser Play button may launch the wrong account."
            )
        else:
            self._set_status(
                "Per-account isolation OFF — browser launches work normally; "
                "alts share Roblox state on disk."
            )

    def _open_bot_settings(self):
        dlg = BotSettingsDialog(self, self.config)
        dlg.exec()

    # ---- bot lifecycle --------------------------------------------------

    def _auto_start_bot(self):
        try:
            self.start_bot()
        except Exception as e:
            log.exception("auto-start bot failed")
            self._toast(f"Bot failed to start: {e}", kind="error", duration_ms=5000)

    def start_bot(self) -> None:
        """Start (or restart) the Discord bot using the persisted config."""
        cfg = self.config.cfg
        if self.bot is not None:
            self.stop_bot()
        if not discord_bot.is_available():
            raise RuntimeError(discord_bot.install_hint())
        if not cfg.bot_token:
            raise RuntimeError("Bot token is empty.")
        if not cfg.bot_user_ids:
            raise RuntimeError(
                "Authorized Discord user IDs are empty — refusing to start a "
                "bot that anyone in the server could command. Add at least "
                "your own Discord user ID."
            )
        self.bot = discord_bot.ManagerBot(
            token=cfg.bot_token,
            manager=self.manager,
            allowed_user_ids=cfg.bot_user_ids,
            screenshot_fn=screenshot.capture_window_png,
        )
        self.bot.start()
        self._set_status("Discord bot started.")

    def stop_bot(self) -> None:
        if self.bot is None:
            return
        try:
            self.bot.stop()
        finally:
            self.bot = None
        self._set_status("Discord bot stopped.")

    def _on_accounts_changed(self):
        self._refresh_accounts_dropdown()
        self._refresh_presets()

    def _open_logs(self):
        try:
            os.startfile(str(self.log_path.parent))
        except Exception as e:
            log.exception("could not open log folder")
            QMessageBox.critical(self, "Logs", f"Could not open {self.log_path.parent}: {e}")

    def _on_wipe_data(self):
        """Custom dialog with a "also reset Roblox sign-in" opt-in.

        QMessageBox doesn't support checkboxes, so we hand-build a QDialog
        with the warning text + the checkbox + Yes/No buttons. On confirm:
          1. Stop the Discord bot.
          2. Terminate every Roblox client we tracked + release the mutex.
          3. Delete %APPDATA%\\MultiRobloxManager\\ (junction-safe).
          4. If the checkbox is on: also clear the Roblox client's local
             sign-in state (LocalStorage + HKCU\\Software\\Roblox\\
             RobloxStudioBrowser). This is what un-sticks the "browser
             launches the wrong account" problem.
          5. Quit.
        """
        root = wipe.app_root()
        dlg = QDialog(self)
        dlg.setWindowTitle("Wipe all app data")
        dlg.setMinimumWidth(560)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        warn = QLabel(
            f"<b>This will permanently delete:</b><br>"
            f"&nbsp;&nbsp;• {root}<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;accounts.json (encrypted cookies)<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;config.json (presets, webhook, bot token)<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;logs\\<br>"
            f"&nbsp;&nbsp;&nbsp;&nbsp;data\\&lt;user_id&gt;\\ (per-account profile dirs)<br><br>"
            f"It will also terminate every managed Roblox instance and the "
            f"Discord bot (if running), then close this app."
        )
        warn.setWordWrap(True)
        v.addWidget(warn)

        also_reset = QCheckBox(
            "Also reset Roblox sign-in on this machine "
            "(fixes 'browser launches the wrong account')"
        )
        also_reset.setChecked(False)
        v.addWidget(also_reset)

        sub = QLabel(
            "With the checkbox on we also delete "
            "<code>%LOCALAPPDATA%\\Roblox\\LocalStorage\\</code> and "
            "<code>HKCU\\Software\\Roblox\\RobloxStudioBrowser</code> — the "
            "auth state Roblox writes after a managed launch. <b>You'll need "
            "to sign in to Roblox again via your browser afterwards.</b> "
            "Your Roblox install, graphics settings, and keybinds are not "
            "touched."
        )
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        v.addWidget(sub)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        confirm_btn = QPushButton("Wipe")
        confirm_btn.setObjectName("primaryButton")
        confirm_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(confirm_btn)
        v.addLayout(btn_row)

        if dlg.exec() != QDialog.Accepted:
            return
        reset_login = also_reset.isChecked()

        # Stop the bot and shut down managed instances before deleting.
        try:
            self.stop_bot()
        except Exception:
            log.exception("bot shutdown during wipe failed")
        try:
            self.manager.shutdown()
        except Exception:
            log.exception("manager shutdown during wipe failed")

        summary = wipe.wipe()
        login_summary = wipe.reset_roblox_login_state() if reset_login else None

        # Combine summaries for the user-facing report.
        all_errors = list(summary["errors"])
        all_removed = list(summary["removed"])
        if login_summary is not None:
            all_errors.extend(login_summary["errors"])
            all_removed.extend(login_summary["removed"])

        if all_errors:
            QMessageBox.warning(
                self, "Wipe finished with errors",
                "Some items could not be removed (see log):\n\n"
                + "\n".join(f"  • {p}" if isinstance(p, str) else f"  • {p[0]}"
                            for p in all_errors[:10])
            )
        else:
            msg = (
                f"App data removed ({len(summary['removed'])} path(s), "
                f"{summary['junctions_removed']} junction(s))."
            )
            if reset_login:
                msg += (
                    f"\nRoblox sign-in state also cleared "
                    f"({len(login_summary['removed'])} item(s)). "
                    "Sign in to Roblox via your browser before launching again."
                )
            QMessageBox.information(self, "Wipe complete", msg)

        QApplication.quit()

    def _show_about(self):
        QMessageBox.about(
            self, "About Multi Roblox Manager",
            "<b>Multi Roblox Manager</b><br><br>"
            f"Roblox client detected: {self.roblox_version}<br>"
            f"Log folder: {self.log_path.parent}<br><br>"
            "Holds the Roblox singleton mutex so multiple clients can run, "
            "with per-account ticket auth, profile isolation, server hop, and Anti-AFK.",
        )

    def closeEvent(self, event):
        if self.manager.instances:
            ans = QMessageBox.question(
                self, "Quit",
                "Closing will terminate all managed Roblox instances. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                event.ignore()
                return
        try:
            self.stop_bot()
        except Exception:
            log.exception("bot shutdown failed")
        try:
            self.manager.shutdown()
        except Exception:
            log.exception("manager shutdown failed")
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Account manager dialog


class AccountManagerDialog(QDialog):
    """Add / remove Roblox accounts. Cookies are validated then DPAPI-encrypted."""

    account_changed = Signal()

    def __init__(self, parent: MainWindow, store: AccountStore):
        super().__init__(parent)
        self.setWindowTitle("Accounts")
        self.resize(640, 500)
        self.store = store

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Manage Roblox accounts")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Nickname", "Username", "User ID", "Proxy"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        for i, w in enumerate((140, 160, 100, 180)):
            self.tree.setColumnWidth(i, w)
        self.tree.itemSelectionChanged.connect(self._on_select_row)
        layout.addWidget(self.tree, 1)

        form_box = QGroupBox("Add or update account")
        fl = QGridLayout(form_box)
        fl.setVerticalSpacing(8)
        fl.setHorizontalSpacing(10)
        fl.setContentsMargins(12, 18, 12, 12)

        fl.addWidget(QLabel("Nickname (optional)"), 0, 0)
        self.nickname_edit = QLineEdit()
        fl.addWidget(self.nickname_edit, 0, 1)
        fl.addWidget(QLabel("Proxy URL (optional)"), 0, 2)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText(
            "http(s)://user:pass@host:port or socks5://user:pass@host:port"
        )
        fl.addWidget(self.proxy_edit, 0, 3)
        fl.setColumnStretch(1, 1)
        fl.setColumnStretch(3, 1)

        fl.addWidget(QLabel(".ROBLOSECURITY cookie"), 1, 0, Qt.AlignTop)
        self.cookie_edit = QTextEdit()
        self.cookie_edit.setFixedHeight(80)
        fl.addWidget(self.cookie_edit, 1, 1, 1, 3)
        layout.addWidget(form_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.add_btn = QPushButton("Add / Update")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self._on_add)
        self.browser_btn = QPushButton("Sign in with Browser…")
        self.browser_btn.clicked.connect(self._on_browser_login)
        self.proxy_btn = QPushButton("Update Proxy")
        self.proxy_btn.clicked.connect(self._on_update_proxy)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._on_remove)
        for b in (self.add_btn, self.browser_btn, self.proxy_btn, self.remove_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel(
            "Proxy URL is applied to Roblox auth calls only; "
            "game-client traffic still routes directly unless you also have a "
            "system-wide proxy."
        )
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._refresh()
        # Dark title bar for this Toplevel too. winId is only valid after show.
        QTimer.singleShot(
            0, lambda: _apply_dark_title_bar(self, parent.config.cfg.theme == "dark"),
        )

    def _refresh(self):
        self.tree.clear()
        for acc in self.store:
            item = QTreeWidgetItem([acc.nickname, acc.username, str(acc.user_id), acc.proxy or "—"])
            item.setData(0, Qt.UserRole, acc.user_id)
            self.tree.addTopLevelItem(item)
        self.account_changed.emit()

    def _on_select_row(self):
        items = self.tree.selectedItems()
        if not items:
            return
        user_id = items[0].data(0, Qt.UserRole)
        acc = self.store.find(user_id)
        if acc:
            self.nickname_edit.setText(acc.nickname)
            self.proxy_edit.setText(acc.proxy)

    def _on_update_proxy(self):
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "Select", "Select an account row first.")
            return
        user_id = items[0].data(0, Qt.UserRole)
        self.store.update_proxy(user_id, self.proxy_edit.text().strip())
        self.status_label.setText("Proxy updated.")
        self._refresh()

    def _on_add(self):
        cookie = self.cookie_edit.toPlainText().strip()
        if not cookie:
            QMessageBox.warning(self, "Missing", "Paste your .ROBLOSECURITY cookie.")
            return
        nickname = self.nickname_edit.text().strip()
        proxy = self.proxy_edit.text().strip()
        self.status_label.setText("Validating cookie with Roblox…")
        QApplication.processEvents()
        try:
            acc = self.store.add_or_update(cookie, nickname=nickname, proxy=proxy)
        except Exception as e:
            log.exception("account validation failed")
            QMessageBox.critical(self, "Validation failed", str(e))
            self.status_label.setText(f"Error: {e}")
            return
        self.cookie_edit.clear()
        self.nickname_edit.clear()
        self.proxy_edit.clear()
        self.status_label.setText(f"Saved {acc.label()} (user {acc.user_id}).")
        self._refresh()

    def _on_remove(self):
        items = self.tree.selectedItems()
        if not items:
            return
        user_id = items[0].data(0, Qt.UserRole)
        acc = self.store.find(user_id)
        if acc and QMessageBox.question(
            self, "Remove", f"Remove {acc.label()}?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.store.remove(user_id)
            self.status_label.setText(f"Removed {acc.label()}.")
            self._refresh()

    def _on_browser_login(self):
        if not browser_login.is_available():
            QMessageBox.information(self, "pywebview required", browser_login.install_hint())
            return
        nickname = self.nickname_edit.text().strip()
        proxy = self.proxy_edit.text().strip()
        self.status_label.setText(
            "Opening Roblox sign-in window… complete sign-in (password, QR, or passkey)."
        )
        QApplication.processEvents()

        def worker():
            cookie = browser_login.harvest_via_subprocess()
            # Schedule the rest on the GUI thread.
            QTimer.singleShot(
                0, lambda: self._finish_browser_login(cookie, nickname, proxy),
            )
        threading.Thread(target=worker, daemon=True).start()

    def _finish_browser_login(self, cookie: Optional[str], nickname: str, proxy: str):
        if not cookie:
            self.status_label.setText("Sign-in cancelled or failed before a cookie was captured.")
            return
        try:
            acc = self.store.add_or_update(cookie, nickname=nickname, proxy=proxy)
        except Exception as e:
            log.exception("account validation after browser login failed")
            QMessageBox.critical(self, "Validation failed", str(e))
            self.status_label.setText(f"Error: {e}")
            return
        self.nickname_edit.clear()
        self.proxy_edit.clear()
        self.status_label.setText(f"Signed in as {acc.label()} (user {acc.user_id}).")
        self._refresh()


# ---------------------------------------------------------------------------
# Notifications dialog


class WebhookSettingsDialog(QDialog):
    """Configure the Discord webhook fired on instance crash."""

    def __init__(self, parent: MainWindow, config: ConfigStore):
        super().__init__(parent)
        self.setWindowTitle("Notifications")
        self.resize(560, 280)
        self.config = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Discord notifications")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        explainer = QLabel(
            "Fires a Discord embed when any managed instance is detected to have "
            "crashed (PID disappeared). To get a webhook URL: in Discord, "
            "<i>Server Settings → Integrations → Webhooks → New Webhook</i>, "
            "then <i>Copy Webhook URL</i>."
        )
        explainer.setObjectName("muted")
        explainer.setWordWrap(True)
        layout.addWidget(explainer)

        box = QGroupBox("Webhook")
        gl = QGridLayout(box)
        gl.setContentsMargins(12, 18, 12, 12)
        gl.setVerticalSpacing(8)
        gl.setHorizontalSpacing(10)

        gl.addWidget(QLabel("Webhook URL"), 0, 0)
        self.url_edit = QLineEdit(self.config.cfg.webhook_url)
        self.url_edit.setPlaceholderText("https://discord.com/api/webhooks/<id>/<token>")
        gl.addWidget(self.url_edit, 0, 1)
        gl.setColumnStretch(1, 1)

        self.enable_radio_on = QRadioButton("Notify on crash")
        self.enable_radio_off = QRadioButton("Disabled")
        (self.enable_radio_on if self.config.cfg.webhook_on_crash
         else self.enable_radio_off).setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self.enable_radio_on)
        radio_row.addWidget(self.enable_radio_off)
        radio_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(radio_row)
        gl.addWidget(wrap, 1, 0, 1, 2)

        layout.addWidget(box)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.test_btn = QPushButton("Send Test")
        self.test_btn.clicked.connect(self._on_test)
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

        # Dark title bar to match the rest of the app.
        QTimer.singleShot(
            0, lambda: _apply_dark_title_bar(self, parent.config.cfg.theme == "dark"),
        )

    def _validated_url(self) -> Optional[str]:
        try:
            return webhook.validate_url(self.url_edit.text())
        except ValueError as e:
            self.status_label.setText(str(e))
            return None

    def _on_test(self):
        url = self._validated_url()
        if url is None:
            return
        if not url:
            self.status_label.setText("Paste a webhook URL first.")
            return
        self.test_btn.setEnabled(False)
        self.status_label.setText("Posting test notification…")
        QApplication.processEvents()

        # Run the network call on a worker thread so the UI doesn't hang
        # while requests connects. Back to the GUI thread via QTimer.
        def worker():
            ok, msg = webhook.post_test_sync(url)
            QTimer.singleShot(0, lambda: self._finish_test(ok, msg))
        threading.Thread(target=worker, daemon=True).start()

    def _finish_test(self, ok: bool, msg: str):
        self.test_btn.setEnabled(True)
        if ok:
            self.status_label.setText("Sent. Check the Discord channel.")
        else:
            self.status_label.setText(f"Failed: {msg}")

    def _on_save(self):
        url = self._validated_url()
        if url is None:
            return  # validation error already set on the status label
        self.config.cfg.webhook_url = url
        self.config.cfg.webhook_on_crash = self.enable_radio_on.isChecked()
        self.config.save()
        self.accept()


# ---------------------------------------------------------------------------
# Discord bot dialog


class BotSettingsDialog(QDialog):
    """Configure the Discord bot — token, authorized users, start/stop."""

    def __init__(self, parent: MainWindow, config: ConfigStore):
        super().__init__(parent)
        self.setWindowTitle("Discord Bot")
        self.resize(640, 460)
        self.config = config
        self._main = parent

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel("Discord bot — remote screenshots & control")
        title.setObjectName("titleLabel")
        layout.addWidget(title)

        intro = QLabel(
            "Create a bot at <a href='https://discord.com/developers/applications'>"
            "discord.com/developers/applications</a>, invite it to your server, then "
            "paste its <b>bot token</b> (Bot tab → Reset Token) below. Add your "
            "own Discord user ID to the allowlist — without it the bot refuses "
            "every command.<br><br>"
            "Commands: <code>!instances</code>, "
            "<code>!screenshot [all | &lt;label&gt; | &lt;index&gt;]</code>, "
            "<code>!ping</code>."
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        layout.addWidget(intro)

        if not discord_bot.is_available():
            warn = QLabel(discord_bot.install_hint())
            warn.setObjectName("muted")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        cfg = config.cfg
        box = QGroupBox("Bot configuration")
        gl = QGridLayout(box)
        gl.setContentsMargins(12, 18, 12, 12)
        gl.setVerticalSpacing(8)
        gl.setHorizontalSpacing(10)

        gl.addWidget(QLabel("Bot token"), 0, 0)
        self.token_edit = QLineEdit(cfg.bot_token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("paste from Discord developer portal")
        gl.addWidget(self.token_edit, 0, 1)
        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda on: self.token_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password,
            )
        )
        gl.addWidget(show_btn, 0, 2)

        gl.addWidget(QLabel("Authorized user IDs"), 1, 0, Qt.AlignTop)
        self.ids_edit = QTextEdit()
        self.ids_edit.setPlaceholderText(
            "One Discord user ID per line. Right-click your username in "
            "Discord (with Developer Mode on) → Copy User ID."
        )
        self.ids_edit.setFixedHeight(110)
        self.ids_edit.setPlainText("\n".join(cfg.bot_user_ids))
        gl.addWidget(self.ids_edit, 1, 1, 1, 2)

        self.enable_radio_on = QRadioButton("Bot enabled (start with app)")
        self.enable_radio_off = QRadioButton("Disabled")
        (self.enable_radio_on if cfg.bot_enabled
         else self.enable_radio_off).setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self.enable_radio_on)
        radio_row.addWidget(self.enable_radio_off)
        radio_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(radio_row)
        gl.addWidget(wrap, 2, 0, 1, 3)

        gl.setColumnStretch(1, 1)
        layout.addWidget(box)

        self.status_label = QLabel(self._status_text())
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.start_btn = QPushButton("Save & Start")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.clicked.connect(self._on_save_start)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Close")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        QTimer.singleShot(
            0, lambda: _apply_dark_title_bar(self, parent.config.cfg.theme == "dark"),
        )

    def _status_text(self) -> str:
        if self._main.bot is None:
            return "Status: stopped."
        return "Status: running."

    def _parsed_ids(self) -> list[str]:
        raw = self.ids_edit.toPlainText()
        out = []
        for chunk in raw.replace(",", "\n").splitlines():
            s = chunk.strip()
            if not s:
                continue
            if not s.isdigit():
                raise ValueError(f"User ID {s!r} is not numeric.")
            out.append(s)
        return out

    def _persist(self, enabled: bool):
        cfg = self.config.cfg
        cfg.bot_token = self.token_edit.text().strip()
        cfg.bot_user_ids = self._parsed_ids()
        cfg.bot_enabled = enabled
        self.config.save()

    def _on_save_start(self):
        try:
            self._persist(enabled=self.enable_radio_on.isChecked())
        except ValueError as e:
            self.status_label.setText(f"Error: {e}")
            return
        try:
            self._main.start_bot()
        except Exception as e:
            self.status_label.setText(f"Error: {e}")
            return
        self.status_label.setText("Status: running. Try !ping in your server.")

    def _on_stop(self):
        try:
            self._main.stop_bot()
        finally:
            # Keep auto-start preference but mark the running state stopped.
            self.config.cfg.bot_enabled = False
            self.config.save()
            self.enable_radio_off.setChecked(True)
            self.status_label.setText("Status: stopped.")


# ---------------------------------------------------------------------------
# Entry point


def run(scale: float = 1.0):
    # Per-monitor DPI handling: PassThrough means Qt receives fractional
    # scale factors as-is instead of rounding to the nearest integer, which
    # gives crisper rendering on 125% / 150% / 175% displays.
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough,
        )
    except Exception:
        pass

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Multi Roblox Manager")
    app.setOrganizationName("MultiRobloxManager")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


def main():
    run()
