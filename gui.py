"""
ISO 34504 → SAGA converter — simple GUI.
Run: python3 gui.py
"""
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

ENV_PATH = Path(__file__).resolve().parent / ".env"


def _read_api_key() -> str:
    """API key from the environment or the .env next to this script ('' if unset)."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                val = line.partition("=")[2].strip()
                if val and "your-key-here" not in val:
                    return val
    return ""


def _save_api_key(key: str):
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    for i, line in enumerate(lines):
        if line.strip().startswith("ANTHROPIC_API_KEY="):
            lines[i] = f"ANTHROPIC_API_KEY={key}"
            break
    else:
        lines.append(f"ANTHROPIC_API_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ISO 34504 → SAGA Converter")
        self.geometry("700x520")
        self.resizable(True, True)
        self._build()

    def _build(self):
        pad = {"padx": 10, "pady": 5}

        # ── Input folders ──────────────────────────────────────────────────
        frm_in = tk.LabelFrame(self, text="Input folder(s)")
        frm_in.pack(fill="x", **pad)

        self.folder_list = tk.Listbox(frm_in, height=4, selectmode=tk.EXTENDED)
        self.folder_list.pack(side="left", fill="x", expand=True, padx=5, pady=5)

        btn_frm = tk.Frame(frm_in)
        btn_frm.pack(side="right", padx=5)
        tk.Button(btn_frm, text="Add folder", command=self._add_folder).pack(fill="x", pady=2)
        tk.Button(btn_frm, text="Remove selected", command=self._remove_folder).pack(fill="x", pady=2)

        # ── Output ─────────────────────────────────────────────────────────
        frm_out = tk.LabelFrame(self, text="Output CSV")
        frm_out.pack(fill="x", **pad)

        self.out_var = tk.StringVar(value="(auto: named after input folder)")
        tk.Entry(frm_out, textvariable=self.out_var, width=55).pack(side="left", padx=5, pady=5)
        tk.Button(frm_out, text="Browse", command=self._browse_out).pack(side="left")

        # ── Options ────────────────────────────────────────────────────────
        frm_opt = tk.Frame(self)
        frm_opt.pack(fill="x", **pad)

        self.dry_run = tk.BooleanVar()
        tk.Checkbutton(frm_opt, text="Dry run (no Haiku calls)", variable=self.dry_run).pack(side="left")

        self.verify = tk.BooleanVar(value=True)
        tk.Checkbutton(frm_opt, text="Verify prompts", variable=self.verify).pack(side="left", padx=20)

        self.verbose = tk.BooleanVar(value=True)
        tk.Checkbutton(frm_opt, text="Verbose", variable=self.verbose).pack(side="left")

        self.include_ego_action = tk.BooleanVar(value=False)
        tk.Checkbutton(frm_opt, text="Include ego-action scenarios",
                       variable=self.include_ego_action).pack(side="left", padx=20)

        # ── Run button ─────────────────────────────────────────────────────
        self.run_btn = tk.Button(self, text="▶  Convert", bg="#4f46e5", fg="white",
                                  font=("", 12, "bold"), command=self._run)
        self.run_btn.pack(fill="x", padx=10, pady=5)

        # ── Progress + log ─────────────────────────────────────────────────
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=10)

        self.log = scrolledtext.ScrolledText(self, height=14, font=("Courier", 10))
        self.log.pack(fill="both", expand=True, padx=10, pady=5)
        self.log.config(state="disabled")

    # ── Folder management ──────────────────────────────────────────────────

    def _add_folder(self):
        d = filedialog.askdirectory(title="Select batch folder")
        if d and d not in self.folder_list.get(0, tk.END):
            self.folder_list.insert(tk.END, d)

    def _remove_folder(self):
        for i in reversed(self.folder_list.curselection()):
            self.folder_list.delete(i)

    def _browse_out(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                          filetypes=[("CSV", "*.csv")])
        if p:
            self.out_var.set(p)

    # ── Run ────────────────────────────────────────────────────────────────

    def _log(self, text: str):
        self.log.config(state="normal")
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)
        self.log.config(state="disabled")

    def _ensure_api_key(self) -> bool:
        """Prompt for the Anthropic API key if not configured. Returns True if usable."""
        if _read_api_key():
            return True
        key = simpledialog.askstring(
            "Anthropic API key required",
            "Paste your Anthropic API key (starts with sk-ant-...).\n"
            "It will be saved to the .env file next to the converter\n"
            "so you only have to do this once.",
            parent=self, show="*",
        )
        if not key or not key.strip():
            messagebox.showwarning(
                "No API key",
                "Conversion needs an Anthropic API key.\n"
                "Tip: check 'Dry run' to test without one.",
            )
            return False
        _save_api_key(key.strip())
        self._log("API key saved to .env")
        return True

    def _run(self):
        folders = list(self.folder_list.get(0, tk.END))
        if not folders:
            messagebox.showerror("No input", "Add at least one folder.")
            return

        if not self.dry_run.get() and not self._ensure_api_key():
            return

        self.run_btn.config(state="disabled")
        self.progress.start()
        self.log.config(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.config(state="disabled")

        threading.Thread(target=self._run_bg, args=(folders,), daemon=True).start()

    def _run_bg(self, folders):
        script = Path(__file__).parent / "converter.py"
        input_arg = ":".join(folders)

        out_val = self.out_var.get()
        out_arg = [] if out_val.startswith("(auto") else ["--output", out_val]

        cmd = [sys.executable, str(script), "--input", input_arg] + out_arg
        if self.dry_run.get():            cmd.append("--dry-run")
        if self.verify.get():             cmd.append("--verify")
        if self.include_ego_action.get(): cmd.append("--include-ego-action")
        if self.verbose.get():            cmd.append("-v")

        self._log(f"Running: {' '.join(cmd)}\n")

        env = os.environ.copy()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, env=env, cwd=str(script.parent))
        for line in proc.stdout:
            self.after(0, self._log, line.rstrip())
        proc.wait()

        self.after(0, self._done, proc.returncode)

    def _done(self, rc):
        self.progress.stop()
        self.run_btn.config(state="normal")
        if rc == 0:
            self._log("\n✓ Done.")
            messagebox.showinfo("Done", "Conversion complete. Check the CSV in the converter folder.")
        else:
            self._log(f"\n✗ Exited with code {rc}")
            messagebox.showerror("Error", "Conversion failed — see log above.")


if __name__ == "__main__":
    App().mainloop()
