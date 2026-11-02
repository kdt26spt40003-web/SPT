# -*- coding: utf-8 -*-
"""
録画おそうじマネージャー
  録画フォルダを読み込み、「消してもよさそうな録画」をおすすめ順に表示するデスクトップアプリ。

  機能
    1. 再生回数（このアプリから再生した回数）を記録
    2. ジャンルごとの保存日数の制限（期限切れを削除候補に）
    3. ジャンル分け（授業 / 日常（プライベート） / 行事：体育祭・文化祭・校外学習）
    4. 1つ1つの録画の容量を表示（動画だけでなく画像も扱える）
    5. 録画の合計が上限（20MB）の80%を超えたら警告

  必要なもの: Python 3.8 以上（tkinter は標準で入っています）
  あると便利 : pip install send2trash  （削除したファイルをゴミ箱に送れるようになる）
"""
import datetime
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None

# ------------------------------------------------------------------ 設定
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".mts",
             ".m2ts", ".3gp", ".webm", ".flv", ".mpg", ".mpeg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic",
             ".heif", ".tif", ".tiff"}
MEDIA_EXT = VIDEO_EXT | IMAGE_EXT        # 一覧に表示するファイル
DATA_FILE = ".rokuga_kanri.json"        # 再生回数やジャンルを保存するファイル
MAX_BYTES = 20 * 1024 ** 2               # 録画の合計の上限（20MB）
WARN_RATIO = 0.80                       # 上限の80%で警告

GENRES = ["未分類", "授業", "日常（プライベート）",
          "行事：体育祭", "行事：文化祭", "行事：校外学習", "行事：その他"]

# ジャンルごとの保存日数（この日数を過ぎると削除候補になる）
DEFAULT_LIMITS = {"未分類": 60, "授業": 30, "日常（プライベート）": 90, "行事": 365}

# ファイル名・フォルダ名からジャンルを自動で推測するキーワード
KEYWORDS = [("体育祭", "行事：体育祭"), ("運動会", "行事：体育祭"),
            ("文化祭", "行事：文化祭"), ("学園祭", "行事：文化祭"),
            ("校外", "行事：校外学習"), ("遠足", "行事：校外学習"),
            ("修学旅行", "行事：校外学習"), ("授業", "授業"), ("講義", "授業")]


def limit_key(genre):
    return "行事" if genre.startswith("行事") else genre


def guess_genre(path):
    for word, genre in KEYWORDS:
        if word in path:
            return genre
    return "未分類"


def human_size(b):
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.2f} TB"


def delete_score(info, limits):
    """消してもよさそう度（0〜100）と理由を返す"""
    if info["protect"]:
        return 0, ["保護中"]
    score, reasons = 0, []
    age = info["age"]
    limit = limits.get(limit_key(info["genre"]), 60)

    # 2. 日にちの制限
    if age > limit:
        score += 40 + min(20, (age - limit) // 10)
        reasons.append(f"保存期限を{age - limit}日すぎている")

    # 1. 開いた回数（撮ったばかりの録画は対象外）
    if age >= 7:
        if info["opens"] == 0:
            score += 25
            reasons.append("一度も見ていない")
        elif info["opens"] <= 2:
            score += 10
            reasons.append("ほとんど見ていない")

    # 4. 容量が大きいほど消す効果が大きい
    gb = info["size"] / 1024 ** 3
    score += min(25, int(gb * 10))
    if gb >= 1:
        reasons.append("容量が大きい")

    # 行事は思い出なので少し残しやすくする
    if info["genre"].startswith("行事"):
        score -= 20
        reasons.append("行事（思い出）")

    return max(0, min(100, score)), reasons


def rank_label(score):
    if score >= 60:
        return "◎ 消してOK"
    if score >= 35:
        return "○ 検討"
    return "△ 残す"


def open_with_default_app(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# ------------------------------------------------------------------ アプリ本体
class App(tk.Tk):
    COLUMNS = [("name", "ファイル名", 280), ("genre", "ジャンル", 130),
               ("size", "容量", 80), ("date", "日付", 90), ("age", "経過日数", 70),
               ("opens", "再生回数", 70), ("score", "消してOK度", 80),
               ("rank", "判定", 90), ("reason", "理由", 260)]

    def __init__(self):
        super().__init__()
        self.title("録画おそうじマネージャー")
        self.geometry("1250x680")
        self.minsize(900, 500)
        self.folder = None
        self.data = {"files": {}, "limits": dict(DEFAULT_LIMITS), "extra": []}
        self.items = {}            # 相対パス（追加したファイルはフルパス） -> 表示用情報
        self.sort_col, self.sort_rev = "score", True
        self._build_ui()
        # フォルダを選ぶ前でも、前回「ファイルを追加」した録画を表示する
        self.load_data()
        if self.data["extra"]:
            self.after(100, self.scan)

    # ---------------- UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Ok.Horizontal.TProgressbar", background="#3a9d5d")
        style.configure("Warn.Horizontal.TProgressbar", background="#d9534f")
        style.configure("Treeview", rowheight=24)

        # フォルダ選択
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="📁 録画フォルダを選ぶ", command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="➕ ファイルを追加", command=self.add_files).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="🔄 再読み込み", command=self.scan).pack(side="left", padx=6)
        self.folder_label = ttk.Label(top, text="フォルダが選ばれていません")
        self.folder_label.pack(side="left", padx=8)

        # 容量バー
        cap = ttk.Frame(self, padding=(8, 0))
        cap.pack(fill="x")
        ttk.Label(cap, text=f"録画の合計（上限{human_size(MAX_BYTES)}）:").pack(side="left")
        self.cap_bar = ttk.Progressbar(cap, length=350, maximum=100,
                                       style="Ok.Horizontal.TProgressbar")
        self.cap_bar.pack(side="left", padx=6)
        self.cap_label = ttk.Label(cap, text="-")
        self.cap_label.pack(side="left")
        self.warn_label = tk.Label(cap, text="", fg="#d9534f", font=("", 10, "bold"))
        self.warn_label.pack(side="left", padx=10)

        # 絞り込み
        flt = ttk.Frame(self, padding=8)
        flt.pack(fill="x")
        ttk.Label(flt, text="ジャンル:").pack(side="left")
        self.filter_var = tk.StringVar(value="すべて")
        cb = ttk.Combobox(flt, textvariable=self.filter_var, state="readonly",
                          values=["すべて", "行事（すべて）"] + GENRES, width=18)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())
        self.only_cand = tk.BooleanVar(value=False)
        ttk.Checkbutton(flt, text="削除候補（◎○）だけ表示", variable=self.only_cand,
                        command=self.refresh_table).pack(side="left", padx=12)
        self.summary_label = ttk.Label(flt, text="")
        self.summary_label.pack(side="right")

        # 一覧
        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="extended")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            anchor = "e" if key in ("size", "age", "opens", "score") else "w"
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.tag_configure("del", background="#fde2e1")
        self.tree.tag_configure("maybe", background="#fff4d6")
        self.tree.tag_configure("keep", background="#ffffff")
        self.tree.tag_configure("protect", background="#e3f0ff")
        ysb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.play())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_selection_info())

        # 操作ボタン
        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        ttk.Button(bot, text="▶ 再生（回数+1）", command=self.play).pack(side="left")
        ttk.Separator(bot, orient="vertical").pack(side="left", fill="y", padx=8)
        self.genre_var = tk.StringVar(value=GENRES[1])
        ttk.Combobox(bot, textvariable=self.genre_var, values=GENRES,
                     state="readonly", width=18).pack(side="left")
        ttk.Button(bot, text="ジャンルを設定", command=self.set_genre).pack(side="left", padx=4)
        ttk.Separator(bot, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bot, text="🔒 保護 / 解除", command=self.toggle_protect).pack(side="left")
        ttk.Button(bot, text="⏱ 保存日数の設定", command=self.edit_limits).pack(side="left", padx=4)
        ttk.Button(bot, text="🗑 選択した録画を削除", command=self.delete_selected).pack(side="right")
        self.sel_label = ttk.Label(bot, text="")
        self.sel_label.pack(side="right", padx=10)

    # ---------------- データ保存
    def data_path(self):
        # フォルダを選んでいないときは、ホームフォルダに記録する
        return os.path.join(self.folder or os.path.expanduser("~"), DATA_FILE)

    def load_data(self):
        self.data = {"files": {}, "limits": dict(DEFAULT_LIMITS), "extra": []}
        try:
            with open(self.data_path(), encoding="utf-8") as f:
                loaded = json.load(f)
            self.data["files"] = loaded.get("files", {})
            self.data["limits"].update(loaded.get("limits", {}))
            self.data["extra"] = loaded.get("extra", [])
        except (OSError, ValueError):
            pass

    def save_data(self):
        if not self.folder and not self.data["extra"] and not os.path.exists(self.data_path()):
            return
        try:
            with open(self.data_path(), "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except OSError as e:
            messagebox.showerror("保存エラー", f"記録を保存できませんでした。\n{e}")

    # ---------------- 読み込み
    def choose_folder(self):
        folder = filedialog.askdirectory(title="録画が入っているフォルダを選んでください")
        if folder:
            self.folder = folder
            self.folder_label.config(text=folder)
            self.load_data()
            self.scan()

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="追加する録画・画像を選んでください",
            filetypes=[("動画・画像", " ".join("*" + e for e in sorted(MEDIA_EXT))),
                       ("動画ファイル", " ".join("*" + e for e in sorted(VIDEO_EXT))),
                       ("画像ファイル", " ".join("*" + e for e in sorted(IMAGE_EXT))),
                       ("すべてのファイル", "*.*")])
        if not paths:
            return
        known = {os.path.normcase(p) for p in self.data["extra"]}
        skipped = []
        for p in paths:
            p = os.path.normpath(p)
            if os.path.splitext(p)[1].lower() not in MEDIA_EXT:
                skipped.append(os.path.basename(p))
            elif self.in_folder(p) or os.path.normcase(p) in known:
                continue           # すでに一覧に入っている
            else:
                self.data["extra"].append(p)
                known.add(os.path.normcase(p))
        if skipped:
            messagebox.showinfo("お知らせ", "動画・画像ではないので追加しませんでした。\n\n" + "\n".join(skipped))
        self.scan()

    def in_folder(self, path):
        if not self.folder:
            return False
        try:
            return os.path.commonpath([os.path.abspath(self.folder), path]) == os.path.abspath(self.folder)
        except ValueError:     # ドライブが違う
            return False

    def scan(self):
        if not self.folder and not self.data["extra"]:
            return
        self.items.clear()
        today = datetime.date.today()

        def add_item(key, full):
            try:
                st = os.stat(full)
            except OSError:
                return
            meta = self.data["files"].setdefault(key, {
                "genre": guess_genre(key), "opens": 0,
                "last_opened": None, "protect": False})
            date = datetime.date.fromtimestamp(st.st_mtime)
            self.items[key] = {"rel": key, "full": full, "size": st.st_size,
                               "date": date, "age": (today - date).days,
                               "genre": meta["genre"], "opens": meta["opens"],
                               "protect": meta.get("protect", False)}

        if self.folder:
            for root, _dirs, names in os.walk(self.folder):
                for name in names:
                    if os.path.splitext(name)[1].lower() not in MEDIA_EXT:
                        continue
                    full = os.path.join(root, name)
                    add_item(os.path.relpath(full, self.folder), full)
        # 「ファイルを追加」した録画はフルパスで管理する
        for full in self.data["extra"]:
            if not self.in_folder(full):
                add_item(full, full)
        self.data["extra"] = [p for p in self.data["extra"] if p in self.items]
        # 消えたファイルの記録は掃除
        for rel in list(self.data["files"]):
            if rel not in self.items:
                del self.data["files"][rel]
        self.save_data()
        self.refresh_table()
        self.update_capacity(show_popup=True)

    def recalc(self, info):
        info["score"], info["reasons"] = delete_score(info, self.data["limits"])

    # ---------------- 表示
    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        f = self.filter_var.get()
        rows = []
        for info in self.items.values():
            self.recalc(info)
            if f == "行事（すべて）" and not info["genre"].startswith("行事"):
                continue
            if f not in ("すべて", "行事（すべて）") and info["genre"] != f:
                continue
            if self.only_cand.get() and info["score"] < 35:
                continue
            rows.append(info)

        keyfunc = {"name": lambda i: i["rel"], "genre": lambda i: i["genre"],
                   "size": lambda i: i["size"], "date": lambda i: i["date"],
                   "age": lambda i: i["age"], "opens": lambda i: i["opens"],
                   "score": lambda i: i["score"], "rank": lambda i: i["score"],
                   "reason": lambda i: len(i["reasons"])}[self.sort_col]
        rows.sort(key=keyfunc, reverse=self.sort_rev)

        for i in rows:
            s = i["score"]
            tag = "protect" if i["protect"] else ("del" if s >= 60 else "maybe" if s >= 35 else "keep")
            rank = "🔒 保護中" if i["protect"] else rank_label(s)
            self.tree.insert("", "end", iid=i["rel"], tags=(tag,), values=(
                i["rel"], i["genre"], human_size(i["size"]), i["date"].isoformat(),
                f"{i['age']}日", i["opens"], s, rank, "・".join(i["reasons"])))

        total = sum(i["size"] for i in self.items.values())
        cand = [i for i in self.items.values() if i["score"] >= 60]
        self.summary_label.config(
            text=f"録画 {len(self.items)}本 / 合計 {human_size(total)}　｜　"
                 f"◎消してOK {len(cand)}本（{human_size(sum(i['size'] for i in cand))} 空く）")
        self.update_selection_info()

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, col in ("size", "age", "score", "rank")
        self.refresh_table()

    def update_selection_info(self):
        sel = self.tree.selection()
        if sel:
            size = sum(self.items[r]["size"] for r in sel if r in self.items)
            self.sel_label.config(text=f"選択中 {len(sel)}本（{human_size(size)}）")
        else:
            self.sel_label.config(text="")

    def update_capacity(self, show_popup=False):
        if not self.folder and not self.data["extra"]:
            return
        used = sum(i["size"] for i in self.items.values())
        ratio = used / MAX_BYTES
        self.cap_bar["value"] = min(100, ratio * 100)
        self.cap_label.config(text=f"{ratio * 100:.1f}%（{human_size(used)} / "
                                   f"{human_size(MAX_BYTES)}、残り {human_size(max(0, MAX_BYTES - used))}）")
        if ratio >= WARN_RATIO:
            self.cap_bar.config(style="Warn.Horizontal.TProgressbar")
            self.warn_label.config(text="⚠ 容量が80%を超えています！")
            if show_popup:
                cand = [i for i in self.items.values() if i["score"] >= 60]
                messagebox.showwarning(
                    "容量の警告",
                    f"録画の合計が上限 {human_size(MAX_BYTES)} の {ratio * 100:.1f}% です（80%超え）。\n\n"
                    f"「◎ 消してOK」の録画が {len(cand)}本あります。\n"
                    f"全部消すと約 {human_size(sum(i['size'] for i in cand))} 空きます。")
        else:
            self.cap_bar.config(style="Ok.Horizontal.TProgressbar")
            self.warn_label.config(text="")

    # ---------------- 操作
    def selected(self):
        sel = [r for r in self.tree.selection() if r in self.items]
        if not sel:
            messagebox.showinfo("お知らせ", "一覧から録画を選んでください。")
        return sel

    def play(self):
        sel = self.selected()
        if not sel:
            return
        rel = sel[0]
        try:
            open_with_default_app(self.items[rel]["full"])
        except OSError as e:
            messagebox.showerror("再生エラー", str(e))
            return
        meta = self.data["files"][rel]
        meta["opens"] += 1
        meta["last_opened"] = datetime.datetime.now().isoformat(timespec="seconds")
        self.items[rel]["opens"] = meta["opens"]
        self.save_data()
        self.refresh_table()
        self.tree.selection_set(rel)

    def set_genre(self):
        sel = self.selected()
        for rel in sel:
            self.data["files"][rel]["genre"] = self.genre_var.get()
            self.items[rel]["genre"] = self.genre_var.get()
        if sel:
            self.save_data()
            self.refresh_table()

    def toggle_protect(self):
        sel = self.selected()
        for rel in sel:
            new = not self.items[rel]["protect"]
            self.data["files"][rel]["protect"] = new
            self.items[rel]["protect"] = new
        if sel:
            self.save_data()
            self.refresh_table()

    def delete_selected(self):
        sel = self.selected()
        if not sel:
            return
        protected = [r for r in sel if self.items[r]["protect"]]
        if protected:
            messagebox.showwarning("保護中", "保護中の録画が含まれています。先に保護を解除してください。")
            return
        size = sum(self.items[r]["size"] for r in sel)
        where = "ゴミ箱に移動" if send2trash else "完全に削除（元に戻せません）"
        names = "\n".join(sel[:10]) + ("\n…" if len(sel) > 10 else "")
        if not messagebox.askyesno("削除の確認",
                                   f"{len(sel)}本（{human_size(size)}）を{where}します。\n\n{names}\n\nよろしいですか？"):
            return
        errors = []
        for rel in sel:
            try:
                if send2trash:
                    send2trash(self.items[rel]["full"])
                else:
                    os.remove(self.items[rel]["full"])
                self.data["files"].pop(rel, None)
                if rel in self.data["extra"]:
                    self.data["extra"].remove(rel)
                self.items.pop(rel, None)
            except OSError as e:
                errors.append(f"{rel}: {e}")
        self.save_data()
        self.refresh_table()
        self.update_capacity()
        if errors:
            messagebox.showerror("削除できなかった録画", "\n".join(errors))

    def edit_limits(self):
        win = tk.Toplevel(self)
        win.title("保存日数の設定")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text="この日数を過ぎた録画を「削除候補」にします",
                  padding=10).grid(row=0, column=0, columnspan=3)
        vars_ = {}
        for r, key in enumerate(DEFAULT_LIMITS, start=1):
            ttk.Label(win, text=key, padding=(10, 4)).grid(row=r, column=0, sticky="w")
            v = tk.IntVar(value=self.data["limits"].get(key, DEFAULT_LIMITS[key]))
            ttk.Spinbox(win, from_=1, to=3650, textvariable=v, width=8).grid(row=r, column=1)
            ttk.Label(win, text="日").grid(row=r, column=2, padx=(4, 10))
            vars_[key] = v

        def ok():
            try:
                for k, v in vars_.items():
                    self.data["limits"][k] = max(1, int(v.get()))
            except (tk.TclError, ValueError):
                messagebox.showerror("入力エラー", "数字を入れてください。", parent=win)
                return
            self.save_data()
            self.refresh_table()
            win.destroy()

        ttk.Button(win, text="OK", command=ok).grid(row=len(vars_) + 1, column=0,
                                                   columnspan=3, pady=10)


if __name__ == "__main__":
    App().mainloop()
