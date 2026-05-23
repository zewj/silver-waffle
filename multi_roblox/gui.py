"""Tkinter UI for the multi-instance manager."""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import servers
from .manager import InstanceManager


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Multi Roblox Manager")
        self.geometry("760x440")
        self.minsize(680, 360)

        self.manager = InstanceManager()
        self.manager.start()

        self._build_ui()
        self._refresh_tree()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-Tab>", lambda _e: self._cycle())

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Game (placeId or URL):").pack(side=tk.LEFT)
        self.place_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.place_var, width=42).pack(side=tk.LEFT, padx=6)

        ttk.Label(top, text="Label:").pack(side=tk.LEFT)
        self.label_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.label_var, width=14).pack(side=tk.LEFT, padx=6)

        ttk.Button(top, text="Launch", command=self._on_launch).pack(side=tk.LEFT, padx=4)

        cols = ("label", "place", "pid", "job")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for col, head, w in (
            ("label", "Label", 140),
            ("place", "Place ID", 120),
            ("pid", "PID", 80),
            ("job", "Server (jobId)", 380),
        ):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10)

        actions = ttk.Frame(self, padding=10)
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Focus", command=self._on_focus).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Cycle (Ctrl+Tab)", command=self._cycle).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Server Hop", command=self._on_hop).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Close Instance", command=self._on_close_instance).pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="Ready. Mutex held — extra clients can launch.")
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W, padding=(10, 4)).pack(fill=tk.X, side=tk.BOTTOM)

    def _selected_instance(self):
        sel = self.tree.selection()
        if not sel:
            return None
        idx = self.tree.index(sel[0])
        try:
            return self.manager.instances[idx]
        except IndexError:
            return None

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for inst in self.manager.instances:
            self.tree.insert(
                "", tk.END,
                values=(inst.label, inst.place_id, inst.pid or "-", inst.job_id or "-"),
            )

    def _set_status(self, msg):
        self.status_var.set(msg)

    def _run_async(self, fn, on_done=None):
        def worker():
            try:
                result = fn()
                err = None
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

    def _on_launch(self):
        place_id = servers.parse_place_id(self.place_var.get())
        if not place_id:
            messagebox.showwarning("Invalid", "Enter a numeric placeId or roblox.com /games/<id>/ URL.")
            return
        label = self.label_var.get().strip() or f"Instance {len(self.manager.instances) + 1}"
        self._set_status(f"Launching {label}…")
        self._run_async(
            lambda: self.manager.add_instance(label, place_id),
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
        inst = self._selected_instance()
        if not inst:
            return
        self._set_status(f"Server-hopping {inst.label}…")
        self._run_async(
            lambda: self.manager.server_hop(inst),
            on_done=lambda job: self._set_status(
                f"{inst.label} hopped to {job}" if job else f"{inst.label}: no eligible server found"
            ),
        )

    def _on_close_instance(self):
        inst = self._selected_instance()
        if not inst:
            return
        self.manager.close(inst)
        self._refresh_tree()
        self._set_status(f"Closed {inst.label}")

    def _on_close(self):
        if self.manager.instances and not messagebox.askyesno(
            "Quit", "Closing will terminate all managed Roblox instances. Continue?"
        ):
            return
        self.manager.shutdown()
        self.destroy()


def main():
    App().mainloop()
