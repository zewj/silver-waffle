"""Tkinter UI for the multi-instance manager."""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import servers
from .accounts import AccountStore
from .manager import InstanceManager

_NO_ACCOUNT = "(launcher's signed-in account)"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Multi Roblox Manager")
        self.geometry("820x520")
        self.minsize(720, 420)

        self.store = AccountStore()
        self.manager = InstanceManager()
        self.manager.start()

        self._build_ui()
        self._refresh_accounts_dropdown()
        self._refresh_tree()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<Control-Tab>", lambda _e: self._cycle())

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Game (placeId or URL):").pack(side=tk.LEFT)
        self.place_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.place_var, width=36).pack(side=tk.LEFT, padx=6)

        ttk.Label(top, text="Account:").pack(side=tk.LEFT)
        self.account_var = tk.StringVar()
        self.account_combo = ttk.Combobox(top, textvariable=self.account_var, width=22, state="readonly")
        self.account_combo.pack(side=tk.LEFT, padx=6)

        ttk.Label(top, text="Label:").pack(side=tk.LEFT)
        self.label_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.label_var, width=12).pack(side=tk.LEFT, padx=6)

        ttk.Button(top, text="Launch", command=self._on_launch).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Accounts…", command=self._open_account_manager).pack(side=tk.LEFT, padx=4)

        cols = ("label", "account", "place", "pid", "job")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for col, head, w in (
            ("label", "Label", 130),
            ("account", "Account", 160),
            ("place", "Place ID", 110),
            ("pid", "PID", 70),
            ("job", "Server (jobId)", 320),
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

    def _refresh_accounts_dropdown(self):
        labels = [_NO_ACCOUNT] + [a.label() for a in self.store]
        self.account_combo["values"] = labels
        if self.account_var.get() not in labels:
            self.account_var.set(labels[0])

    def _resolve_selected_account(self):
        choice = self.account_var.get()
        if not choice or choice == _NO_ACCOUNT:
            return None
        return next((a for a in self.store if a.label() == choice), None)

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
                values=(
                    inst.label,
                    inst.account.label() if inst.account else _NO_ACCOUNT,
                    inst.place_id,
                    inst.pid or "-",
                    inst.job_id or "-",
                ),
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

    def _on_launch(self):
        place_id = servers.parse_place_id(self.place_var.get())
        if not place_id:
            messagebox.showwarning("Invalid", "Enter a numeric placeId or roblox.com /games/<id>/ URL.")
            return
        account = self._resolve_selected_account()
        label = self.label_var.get().strip() or (account.label() if account else f"Instance {len(self.manager.instances) + 1}")
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

    def _open_account_manager(self):
        AccountManager(self, self.store, on_change=self._refresh_accounts_dropdown)

    def _on_close(self):
        if self.manager.instances and not messagebox.askyesno(
            "Quit", "Closing will terminate all managed Roblox instances. Continue?"
        ):
            return
        self.manager.shutdown()
        self.destroy()


class AccountManager(tk.Toplevel):
    """Add/remove accounts. Cookies are validated then DPAPI-encrypted at rest."""

    def __init__(self, parent, store: AccountStore, on_change=None):
        super().__init__(parent)
        self.title("Accounts")
        self.geometry("560x420")
        self.transient(parent)
        self.store = store
        self.on_change = on_change

        cols = ("nickname", "username", "user_id")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for col, head, w in (("nickname", "Nickname", 160), ("username", "Username", 200), ("user_id", "User ID", 120)):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=w, anchor=tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        form = ttk.Frame(self, padding=(10, 0))
        form.pack(fill=tk.X)
        ttk.Label(form, text="Nickname (optional):").grid(row=0, column=0, sticky=tk.W)
        self.nickname_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.nickname_var, width=24).grid(row=0, column=1, sticky=tk.W, padx=6)
        ttk.Label(form, text=".ROBLOSECURITY cookie:").grid(row=1, column=0, sticky=tk.NW, pady=(6, 0))
        self.cookie_text = tk.Text(form, height=4, width=60, wrap=tk.WORD)
        self.cookie_text.grid(row=1, column=1, sticky=tk.W, padx=6, pady=(6, 0))

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Add / Update", command=self._on_add).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Remove Selected", command=self._on_remove).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT, padx=4)

        self.status_var = tk.StringVar(value="Paste the .ROBLOSECURITY cookie value (no _|WARNING wrapper needed; both forms are accepted).")
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W, padding=(10, 4), wraplength=540, justify=tk.LEFT).pack(fill=tk.X, side=tk.BOTTOM)

        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for acc in self.store:
            self.tree.insert("", tk.END, iid=str(acc.user_id),
                             values=(acc.nickname, acc.username, acc.user_id))
        if self.on_change:
            self.on_change()

    def _on_add(self):
        cookie = self.cookie_text.get("1.0", tk.END).strip()
        if not cookie:
            messagebox.showwarning("Missing", "Paste your .ROBLOSECURITY cookie.")
            return
        nickname = self.nickname_var.get().strip()
        self.status_var.set("Validating cookie with Roblox…")
        self.update_idletasks()
        try:
            acc = self.store.add_or_update(cookie, nickname=nickname)
        except Exception as e:
            messagebox.showerror("Validation failed", str(e))
            self.status_var.set(f"Error: {e}")
            return
        self.cookie_text.delete("1.0", tk.END)
        self.nickname_var.set("")
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


def main():
    App().mainloop()
