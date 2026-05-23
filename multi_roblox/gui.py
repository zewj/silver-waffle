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
    QAbstractItemView, QApplication, QComboBox, QDialog, QFormLayout,
    QFrame, QGraphicsOpacityEffect, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMenuBar,
    QMessageBox, QPushButton, QRadioButton, QSizePolicy, QSpacerItem,
    QSplitter, QStatusBar, QTextEdit, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import browser_login, launcher, logging_setup, servers
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
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

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
        widths = [130, 140, 90, 60, 100, 90, 70, 80, 220]
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
        for i, w in enumerate((130, 90, 130)):
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
        # Anchor to the bottom-right of the central widget.
        anchor = self.mapToGlobal(QPoint(self.width(), self.height() - self.statusBar().height()))
        anchor = self.mapFromGlobal(anchor)
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

    def _on_accounts_changed(self):
        self._refresh_accounts_dropdown()
        self._refresh_presets()

    def _open_logs(self):
        try:
            os.startfile(str(self.log_path.parent))
        except Exception as e:
            log.exception("could not open log folder")
            QMessageBox.critical(self, "Logs", f"Could not open {self.log_path.parent}: {e}")

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
        self.proxy_edit.setPlaceholderText("http://user:pass@host:port or socks5://host:port")
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
