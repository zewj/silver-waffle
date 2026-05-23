"""Tkinter UI for the multi-instance manager."""
import logging
import os
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import browser_login, launcher, logging_setup, servers, theme
from .accounts import AccountStore
from .config import ConfigStore, Preset
from .manager import InstanceManager

log = logging.getLogger(__name__)

_NO_ACCOUNT = "(launcher's signed-in account)"
_STATS_REFRESH_MS = 1500
_AFK_COL_INDEX = "#6"  # 1-based Treeview column id for the Anti-AFK cell

# Unicode glyphs used as status/AFK indicators.
_STATUS_GLYPH = {
    "running":  "● running",
    "starting": "◌ starting",
    "crashed":  "✕ crashed",
    "closed":   "■ closed",
}
_AFK_ON = "● on"
_AFK_OFF = "○ off"


class _Tooltip:
    """Lightweight tooltip for any ttk widget. Auto-hides on click / leave."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except Exception:
            return
        tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        ttk.Label(tw, text=self.text, padding=(8, 4), style="Tooltip.TLabel").pack()
        self._tip = tw

    def _hide(self, _=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Multi Roblox Manager")
        self.geometry("1080x700")
        self.minsize(940, 560)

        self.log_path = logging_setup.setup()
        log.info("Multi Roblox Manager starting; log file at %s", self.log_path)

        self.store = AccountStore()
        self.config = ConfigStore()
        self.manager = InstanceManager()
        self.manager.LAUNCH_COOLDOWN = self.config.cfg.launch_cooldown
        self.manager.launch_mode = self.config.cfg.launch_mode
        self.manager.start()

        self.roblox_version = launcher.detect_version() or "unknown"
        log.info("detected Roblox version: %s", self.roblox_version)

        self._build_menubar()
        self._build_ui()
        self._apply_theme(self.config.cfg.theme, persist=False)
        self._refresh_accounts_dropdown()
        self._refresh_presets()
        self._refresh_tree()
        self._schedule_stats_refresh()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-Tab>", lambda _e: self._cycle())
        self.bind_all("<Control-l>", lambda _e: self._focus_place_entry())

    # ---- layout ----------------------------------------------------------

    def _build_menubar(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open Logs Folder", command=self._open_logs)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        accounts_menu = tk.Menu(menubar, tearoff=False)
        accounts_menu.add_command(label="Manage Accounts…", command=self._open_account_manager)
        menubar.add_cascade(label="Accounts", menu=accounts_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Toggle Dark / Light", command=self._on_toggle_theme)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self["menu"] = menubar

    def _build_ui(self):
        # ---- header strip (title + detected version + spacer) ----
        header = ttk.Frame(self, padding=(14, 10, 14, 4))
        header.pack(fill=tk.X)
        title = ttk.Label(header, text="Multi Roblox Manager",
                          font=("Segoe UI", 14, "bold"))
        title.pack(side=tk.LEFT)
        self.header_meta_var = tk.StringVar(value=f"Roblox {self.roblox_version}")
        ttk.Label(header, textvariable=self.header_meta_var,
                  foreground="#7a7a7a").pack(side=tk.LEFT, padx=(12, 0))

        # ---- Launch card ----
        launch = ttk.LabelFrame(self, text="Launch", padding=12)
        launch.pack(fill=tk.X, padx=14, pady=(4, 6))

        ttk.Label(launch, text="Game").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        self.place_var = tk.StringVar()
        self.place_entry = ttk.Entry(launch, textvariable=self.place_var)
        self.place_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12), pady=(0, 4))
        _Tooltip(self.place_entry,
                 "placeId (e.g. 920587237) or a roblox.com /games/<id>/… URL")

        ttk.Label(launch, text="Account").grid(row=0, column=2, sticky=tk.W, padx=(0, 8), pady=(0, 4))
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(launch, textvariable=self.account_var,
                                          width=24, state="readonly")
        self.account_combo.grid(row=0, column=3, sticky=tk.EW, pady=(0, 4))

        ttk.Label(launch, text="Label").grid(row=1, column=0, sticky=tk.W, padx=(0, 8))
        self.label_var = tk.StringVar()
        self.label_entry = ttk.Entry(launch, textvariable=self.label_var)
        self.label_entry.grid(row=1, column=1, sticky=tk.EW, padx=(0, 12))

        action_btns = ttk.Frame(launch)
        action_btns.grid(row=1, column=2, columnspan=2, sticky=tk.W)
        launch_btn = ttk.Button(action_btns, text="Launch", command=self._on_launch, width=12)
        launch_btn.pack(side=tk.LEFT, padx=(0, 6))
        _Tooltip(launch_btn, "Launch a new Roblox instance (Enter in any launch field)")
        save_btn = ttk.Button(action_btns, text="Save as Preset", command=self._on_save_preset, width=14)
        save_btn.pack(side=tk.LEFT)
        _Tooltip(save_btn, "Remember this label/place/account combo across restarts")

        mode_row = ttk.Frame(launch)
        mode_row.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(10, 0))
        ttk.Label(mode_row, text="Launch via:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value=self.config.cfg.launch_mode)
        proto_radio = ttk.Radiobutton(
            mode_row, text="Roblox launcher (recommended)",
            variable=self.mode_var, value="protocol",
            command=self._on_mode_changed,
        )
        proto_radio.pack(side=tk.LEFT, padx=(10, 0))
        _Tooltip(proto_radio,
                 "Goes through RobloxPlayerLauncher.exe — stable, handles updates, Hyperion-friendly")
        direct_radio = ttk.Radiobutton(
            mode_row, text="Direct (skip launcher)",
            variable=self.mode_var, value="direct",
            command=self._on_mode_changed,
        )
        direct_radio.pack(side=tk.LEFT, padx=(10, 0))
        _Tooltip(direct_radio,
                 "Spawns RobloxPlayerBeta.exe directly — faster start, skips update check")

        launch.columnconfigure(1, weight=2)
        launch.columnconfigure(3, weight=1)

        # ---- Body: running instances | saved presets ----
        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 4))

        live_frame = ttk.LabelFrame(body, text="Running instances", padding=8)
        body.add(live_frame, weight=3)

        tree_wrap = ttk.Frame(live_frame)
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        cols = ("label", "account", "place", "pid", "status", "afk", "cpu", "ram", "job")
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings",
                                 selectmode="extended")
        for col, head, w, anchor in (
            ("label",   "Label",         120, tk.W),
            ("account", "Account",       140, tk.W),
            ("place",   "Place",          90, tk.W),
            ("pid",     "PID",            60, tk.W),
            ("status",  "Status",        100, tk.W),
            ("afk",     "Anti-AFK",       80, tk.CENTER),
            ("cpu",     "CPU %",          60, tk.E),
            ("ram",     "RAM MB",         70, tk.E),
            ("job",     "Server (jobId)", 220, tk.W),
        ):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor=anchor)
        tree_scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        tree_scroll.grid(row=0, column=1, sticky=tk.NS)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)

        # Empty-state hint, overlaid on the tree when nothing's running.
        self.empty_state = ttk.Label(
            tree_wrap,
            text=("No instances running.\n\n"
                  "Paste a placeId or game URL above, pick an account, click Launch."),
            anchor="center", justify="center",
            font=("Segoe UI", 10, "italic"),
            foreground="#7a7a7a",
        )

        live_hint = ttk.Label(
            live_frame,
            text="Ctrl/Shift-click for multi-select • Click the Anti-AFK cell to toggle that row",
            foreground="#7a7a7a", font=("Segoe UI", 9),
        )
        live_hint.pack(fill=tk.X, pady=(6, 0))

        self.tree.bind("<Button-1>", self._on_tree_click)

        # Presets pane (now a Treeview for consistency).
        preset_frame = ttk.LabelFrame(body, text="Saved presets", padding=8)
        body.add(preset_frame, weight=2)

        preset_wrap = ttk.Frame(preset_frame)
        preset_wrap.pack(fill=tk.BOTH, expand=True)
        self.preset_tree = ttk.Treeview(
            preset_wrap,
            columns=("label", "place", "account"),
            show="headings", selectmode="browse",
        )
        for col, head, w in (
            ("label", "Label", 130),
            ("place", "Place", 90),
            ("account", "Account", 130),
        ):
            self.preset_tree.heading(col, text=head)
            self.preset_tree.column(col, width=w, anchor=tk.W)
        preset_scroll = ttk.Scrollbar(preset_wrap, orient=tk.VERTICAL, command=self.preset_tree.yview)
        self.preset_tree.configure(yscrollcommand=preset_scroll.set)
        self.preset_tree.grid(row=0, column=0, sticky=tk.NSEW)
        preset_scroll.grid(row=0, column=1, sticky=tk.NS)
        preset_wrap.rowconfigure(0, weight=1)
        preset_wrap.columnconfigure(0, weight=1)
        self.preset_tree.bind("<Double-1>", lambda _e: self._on_launch_preset())

        preset_btns = ttk.Frame(preset_frame)
        preset_btns.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(preset_btns, text="Launch", command=self._on_launch_preset).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(preset_btns, text="Launch All", command=self._on_launch_all_presets).pack(side=tk.LEFT, padx=4)
        ttk.Button(preset_btns, text="Remove", command=self._on_remove_preset).pack(side=tk.LEFT, padx=4)

        # ---- Grouped action bar ----
        bar = ttk.Frame(self, padding=(14, 6, 14, 6))
        bar.pack(fill=tk.X)

        def _sep():
            ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        win_group = ttk.Frame(bar)
        win_group.pack(side=tk.LEFT)
        ttk.Label(win_group, text="Window", foreground="#7a7a7a", font=("Segoe UI", 9)).pack(anchor=tk.W)
        win_btns = ttk.Frame(win_group)
        win_btns.pack()
        ttk.Button(win_btns, text="Focus", command=self._on_focus, width=8).pack(side=tk.LEFT, padx=2)
        cycle_btn = ttk.Button(win_btns, text="Cycle", command=self._cycle, width=8)
        cycle_btn.pack(side=tk.LEFT, padx=2)
        _Tooltip(cycle_btn, "Rotate focus to the next running instance (Ctrl+Tab)")

        _sep()

        srv_group = ttk.Frame(bar)
        srv_group.pack(side=tk.LEFT)
        ttk.Label(srv_group, text="Server", foreground="#7a7a7a", font=("Segoe UI", 9)).pack(anchor=tk.W)
        srv_btns = ttk.Frame(srv_group)
        srv_btns.pack()
        hop_btn = ttk.Button(srv_btns, text="Hop", command=self._on_hop, width=10)
        hop_btn.pack(side=tk.LEFT, padx=2)
        _Tooltip(hop_btn,
                 "Pick a fresh public server and hop the selected instance(s) "
                 "without the close-and-reopen flash")

        _sep()

        afk_group = ttk.Frame(bar)
        afk_group.pack(side=tk.LEFT)
        ttk.Label(afk_group, text="Anti-AFK", foreground="#7a7a7a", font=("Segoe UI", 9)).pack(anchor=tk.W)
        afk_btns = ttk.Frame(afk_group)
        afk_btns.pack()
        toggle_afk = ttk.Button(afk_btns, text="Toggle", command=self._on_toggle_antiafk, width=8)
        toggle_afk.pack(side=tk.LEFT, padx=2)
        _Tooltip(toggle_afk, "Toggle Anti-AFK on the selected instance(s)")
        ttk.Button(afk_btns, text="Enable All",
                   command=lambda: self._on_set_antiafk_all(True), width=10).pack(side=tk.LEFT, padx=2)
        ttk.Button(afk_btns, text="Disable All",
                   command=lambda: self._on_set_antiafk_all(False), width=10).pack(side=tk.LEFT, padx=2)

        _sep()

        inst_group = ttk.Frame(bar)
        inst_group.pack(side=tk.LEFT)
        ttk.Label(inst_group, text="Instance", foreground="#7a7a7a", font=("Segoe UI", 9)).pack(anchor=tk.W)
        close_btn = ttk.Button(inst_group, text="Close", command=self._on_close_instance, width=8)
        close_btn.pack(padx=2)
        _Tooltip(close_btn, "Terminate the selected instance(s)")

        # ---- Status footer ----
        self.status_var = tk.StringVar(value=(
            f"Ready • {self.roblox_version} • mutex held • per-account ticket auth & profile isolation enabled"
        ))
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W,
                  padding=(14, 6)).pack(fill=tk.X, side=tk.BOTTOM)

    # ---- helpers ---------------------------------------------------------

    def _focus_place_entry(self):
        try:
            self.place_entry.focus_set()
            self.place_entry.select_range(0, tk.END)
        except Exception:
            pass

    def _refresh_accounts_dropdown(self):
        labels = [_NO_ACCOUNT] + [a.label() for a in self.store]
        self.account_combo["values"] = labels
        if self.account_var.get() not in labels:
            self.account_var.set(labels[0])

    def _refresh_presets(self):
        self.preset_tree.delete(*self.preset_tree.get_children())
        for p in self.config.cfg.presets:
            account = self.store.find(p.account_user_id) if p.account_user_id else None
            acc_label = account.label() if account else (
                "[missing account]" if p.account_user_id else "(signed-in)"
            )
            self.preset_tree.insert("", tk.END, values=(p.label, p.place_id, acc_label))

    def _resolve_selected_account(self):
        choice = self.account_var.get()
        if not choice or choice == _NO_ACCOUNT:
            return None
        return next((a for a in self.store if a.label() == choice), None)

    def _selected_instances(self):
        out = []
        for iid in self.tree.selection():
            idx = self.tree.index(iid)
            try:
                out.append(self.manager.instances[idx])
            except IndexError:
                continue
        return out

    def _selected_instance(self):
        sel = self._selected_instances()
        return sel[0] if sel else None

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for inst in self.manager.instances:
            s = inst.last_sample
            cpu = f"{s.cpu_percent:.0f}" if s.alive else "—"
            ram = f"{s.rss_mb:.0f}" if s.alive else "—"
            tags = ("crashed",) if inst.status == "crashed" else ()
            status_text = _STATUS_GLYPH.get(inst.status, inst.status)
            self.tree.insert(
                "", tk.END,
                values=(
                    inst.label,
                    inst.account.label() if inst.account else _NO_ACCOUNT,
                    inst.place_id,
                    inst.pid or "—",
                    status_text,
                    _AFK_ON if inst.antiafk_on else _AFK_OFF,
                    cpu,
                    ram,
                    inst.job_id or "—",
                ),
                tags=tags,
            )

        # Toggle the empty-state hint over the tree.
        if not self.manager.instances:
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_state.place_forget()

    def _schedule_stats_refresh(self):
        try:
            self.manager.refresh_stats()
            self._refresh_tree()
        except Exception:
            log.exception("stats refresh failed")
        self.after(_STATS_REFRESH_MS, self._schedule_stats_refresh)

    def _apply_theme(self, requested: str, persist: bool = True) -> None:
        # Both tree views and the LabelFrames are ttk widgets, so sv-ttk
        # handles them. No native widgets to recolor any more (preset
        # Listbox was replaced by a Treeview in this UI pass).
        applied = theme.apply(self, requested, native_widgets=None)
        self.tree.tag_configure("crashed", foreground=theme.crashed_fg(applied))
        if persist:
            self.config.cfg.theme = applied
            self.config.save()

    def _on_toggle_theme(self):
        if not theme.is_available():
            messagebox.showinfo(
                "Theme",
                "Dark mode needs sv-ttk. Install with:\n  pip install sv-ttk",
            )
            return
        new = "light" if self.config.cfg.theme == "dark" else "dark"
        self._apply_theme(new)
        self._set_status(f"Theme: {new}.")

    def _open_logs(self):
        try:
            os.startfile(str(self.log_path.parent))
        except Exception as e:
            log.exception("could not open log folder")
            messagebox.showerror("Logs", f"Could not open {self.log_path.parent}: {e}")

    def _show_about(self):
        messagebox.showinfo(
            "About Multi Roblox Manager",
            "Multi Roblox Manager\n\n"
            f"Roblox client detected: {self.roblox_version}\n"
            f"Log folder: {self.log_path.parent}\n\n"
            "Holds the Roblox singleton mutex so multiple clients can run, "
            "with per-account ticket auth, profile isolation, server hop, and Anti-AFK.",
        )

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _run_async(self, fn, on_done=None):
        def worker():
            try:
                result, err = fn(), None
            except Exception as e:
                result, err = None, e
            self.after(0, lambda: self._finish(result, err, on_done))
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, result, err, on_done):
        if err:
            messagebox.showerror("Error", str(err))
            self._set_status(f"Error: {err}")
        elif on_done:
            on_done(result)
        self._refresh_tree()

    # ---- live instance actions ------------------------------------------

    def _on_launch(self):
        place_id = servers.parse_place_id(self.place_var.get())
        if not place_id:
            messagebox.showwarning("Invalid", "Enter a numeric placeId or roblox.com /games/<id>/ URL.")
            return
        account = self._resolve_selected_account()
        label = self.label_var.get().strip() or (account.label() if account else f"Instance {len(self.manager.instances) + 1}")
        self.config.remember(place_id)
        self._set_status(f"Launching {label}…")
        self._run_async(
            lambda: self.manager.add_instance(label, place_id, account=account),
            on_done=lambda inst: self._set_status(
                f"{inst.label} launched (pid={inst.pid})" if inst.pid else f"{inst.label}: launcher started, pid not detected"
            ),
        )

    def _on_focus(self):
        inst = self._selected_instance()
        if not inst:
            return
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
            on_done=lambda results: self._set_status(
                "Hop done: " + ", ".join(f"{lbl}→{j}" for lbl, j in results)
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
        targets = self._selected_instances()
        if not targets:
            return
        for inst in targets:
            self._toggle_antiafk_for(inst)

    def _on_set_antiafk_all(self, enabled):
        count = self.manager.set_antiafk_all(enabled)
        self._set_status(
            f"Anti-AFK {'enabled' if enabled else 'disabled'} on {count} instance(s)."
        )
        self._refresh_tree()

    def _on_tree_click(self, event):
        # Make clicking inside the AFK column toggle that single row, without
        # disturbing the multi-row selection used for bulk hop/close actions.
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        if col != _AFK_COL_INDEX:
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        idx = self.tree.index(row_id)
        try:
            inst = self.manager.instances[idx]
        except IndexError:
            return
        self._toggle_antiafk_for(inst)
        return "break"

    # ---- preset actions --------------------------------------------------

    def _on_save_preset(self):
        place_id = servers.parse_place_id(self.place_var.get())
        if not place_id:
            messagebox.showwarning("Invalid", "Enter a placeId or URL to save.")
            return
        account = self._resolve_selected_account()
        label = self.label_var.get().strip() or (account.label() if account else f"Preset {len(self.config.cfg.presets) + 1}")
        self.config.upsert(Preset(
            label=label, place_id=place_id,
            account_user_id=account.user_id if account else None,
        ))
        self._refresh_presets()
        self._set_status(f"Saved preset '{label}'.")

    def _selected_preset_index(self):
        sel = self.preset_tree.selection()
        if not sel:
            return None
        return self.preset_tree.index(sel[0])

    def _launch_preset(self, preset: Preset):
        account = self.store.find(preset.account_user_id) if preset.account_user_id else None
        if preset.account_user_id and account is None:
            self._set_status(f"Preset '{preset.label}': saved account missing; falling back to signed-in.")
        self._set_status(f"Launching {preset.label}…")
        self._run_async(
            lambda: self.manager.add_instance(preset.label, preset.place_id, account=account),
            on_done=lambda inst: self._set_status(
                f"{inst.label} launched (pid={inst.pid})" if inst.pid else f"{inst.label}: pid not detected"
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
        if messagebox.askyesno("Remove", f"Remove preset '{preset.label}'?"):
            self.config.remove(idx)
            self._refresh_presets()

    # ---- lifecycle -------------------------------------------------------

    def _on_mode_changed(self):
        mode = self.mode_var.get()
        self.manager.launch_mode = mode
        self.config.cfg.launch_mode = mode
        self.config.save()
        self._set_status(
            "Launches will go through RobloxPlayerLauncher." if mode == "protocol"
            else "Launches will spawn RobloxPlayerBeta directly."
        )

    def _open_account_manager(self):
        AccountManager(
            self, self.store,
            on_change=lambda: (self._refresh_accounts_dropdown(), self._refresh_presets()),
            theme_name=self.config.cfg.theme,
        )

    def _on_close(self):
        if self.manager.instances and not messagebox.askyesno(
            "Quit", "Closing will terminate all managed Roblox instances. Continue?"
        ):
            return
        self.manager.shutdown()
        self.destroy()


class AccountManager(tk.Toplevel):
    """Add/remove accounts. Cookies are validated then DPAPI-encrypted at rest."""

    def __init__(self, parent, store: AccountStore, on_change=None,
                 theme_name: str = "dark"):
        super().__init__(parent)
        self.title("Accounts")
        self.geometry("620x480")
        self.transient(parent)
        self.store = store
        self.on_change = on_change
        self._theme_name = theme_name

        header = ttk.Frame(self, padding=(14, 12, 14, 4))
        header.pack(fill=tk.X)
        ttk.Label(header, text="Manage Roblox accounts",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)

        cols = ("nickname", "username", "user_id", "proxy")
        tree_wrap = ttk.Frame(self, padding=(14, 4, 14, 4))
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="browse")
        for col, head, w in (
            ("nickname", "Nickname", 140),
            ("username", "Username", 160),
            ("user_id", "User ID", 90),
            ("proxy", "Proxy", 180),
        ):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor=tk.W)
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        tree_wrap.rowconfigure(0, weight=1)
        tree_wrap.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        form = ttk.LabelFrame(self, text="Add or update account", padding=10)
        form.pack(fill=tk.X, padx=14, pady=(8, 4))

        ttk.Label(form, text="Nickname (optional):").grid(row=0, column=0, sticky=tk.W)
        self.nickname_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.nickname_var, width=22).grid(row=0, column=1, sticky=tk.W, padx=6)
        ttk.Label(form, text="Proxy URL (optional):").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        self.proxy_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.proxy_var, width=30).grid(row=0, column=3, sticky=tk.W, padx=6)

        ttk.Label(form, text=".ROBLOSECURITY cookie:").grid(row=1, column=0, sticky=tk.NW, pady=(8, 0))
        self.cookie_text = tk.Text(form, height=4, width=64, wrap=tk.WORD)
        self.cookie_text.grid(row=1, column=1, columnspan=3, sticky=tk.EW, padx=6, pady=(8, 0))
        form.columnconfigure(3, weight=1)

        btns = ttk.Frame(self, padding=(14, 4, 14, 8))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Add / Update", command=self._on_add).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btns, text="Sign in with Browser…", command=self._on_browser_login).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Update Proxy", command=self._on_update_proxy).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Remove Selected", command=self._on_remove).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=(4, 0))

        self.status_var = tk.StringVar(value=(
            "Proxy URL (e.g. http://user:pass@host:port or socks5://host:port) is applied to Roblox auth calls only;"
            " game-client traffic still routes directly unless you also have a system-wide proxy."
        ))
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W,
                  padding=(14, 6), wraplength=590, justify=tk.LEFT,
                  foreground="#7a7a7a").pack(fill=tk.X, side=tk.BOTTOM)

        # sv-ttk theming is global once set, but the title bar of this
        # Toplevel needs its own DwmSetWindowAttribute call, and the
        # Text widget needs explicit colors since it's not a ttk widget.
        theme.apply(self, self._theme_name, native_widgets=[self.cookie_text])
        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for acc in self.store:
            self.tree.insert("", tk.END, iid=str(acc.user_id),
                             values=(acc.nickname, acc.username, acc.user_id, acc.proxy or "—"))
        if self.on_change:
            self.on_change()

    def _on_select_row(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        acc = self.store.find(int(sel[0]))
        if acc:
            self.nickname_var.set(acc.nickname)
            self.proxy_var.set(acc.proxy)

    def _on_update_proxy(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Select an account row first.")
            return
        user_id = int(sel[0])
        self.store.update_proxy(user_id, self.proxy_var.get().strip())
        self.status_var.set("Proxy updated.")
        self._refresh()

    def _on_add(self):
        cookie = self.cookie_text.get("1.0", tk.END).strip()
        if not cookie:
            messagebox.showwarning("Missing", "Paste your .ROBLOSECURITY cookie.")
            return
        nickname = self.nickname_var.get().strip()
        proxy = self.proxy_var.get().strip()
        self.status_var.set("Validating cookie with Roblox…")
        self.update_idletasks()
        try:
            acc = self.store.add_or_update(cookie, nickname=nickname, proxy=proxy)
        except Exception as e:
            log.exception("account validation failed")
            messagebox.showerror("Validation failed", str(e))
            self.status_var.set(f"Error: {e}")
            return
        self.cookie_text.delete("1.0", tk.END)
        self.nickname_var.set("")
        self.proxy_var.set("")
        self.status_var.set(f"Saved {acc.label()} (user {acc.user_id}).")
        self._refresh()

    def _on_remove(self):
        sel = self.tree.selection()
        if not sel:
            return
        user_id = int(sel[0])
        acc = self.store.find(user_id)
        if acc and messagebox.askyesno("Remove", f"Remove {acc.label()}?"):
            self.store.remove(user_id)
            self.status_var.set(f"Removed {acc.label()}.")
            self._refresh()

    def _on_browser_login(self):
        if not browser_login.is_available():
            messagebox.showinfo("pywebview required", browser_login.install_hint())
            return
        nickname = self.nickname_var.get().strip()
        proxy = self.proxy_var.get().strip()
        self.status_var.set("Opening Roblox sign-in window… complete sign-in (password, QR, or passkey).")
        self.update_idletasks()

        def worker():
            cookie = browser_login.harvest_via_subprocess()
            self.after(0, lambda: self._finish_browser_login(cookie, nickname, proxy))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_browser_login(self, cookie, nickname, proxy):
        if not cookie:
            self.status_var.set("Sign-in cancelled or failed before a cookie was captured.")
            return
        try:
            acc = self.store.add_or_update(cookie, nickname=nickname, proxy=proxy)
        except Exception as e:
            log.exception("account validation after browser login failed")
            messagebox.showerror("Validation failed", str(e))
            self.status_var.set(f"Error: {e}")
            return
        self.nickname_var.set("")
        self.proxy_var.set("")
        self.status_var.set(f"Signed in as {acc.label()} (user {acc.user_id}).")
        self._refresh()


def main():
    App().mainloop()
