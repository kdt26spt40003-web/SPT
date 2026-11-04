# -*- coding: utf-8 -*-
"""
録画おそうじマネージャー
  録画フォルダを読み込み、「消してもよさそうな録画」をおすすめ順に表示するデスクトップアプリ。

  機能
    1. 再生回数（このアプリから再生した回数）を記録
    2. ジャンルごとの保存日数の制限（期限切れを削除候補に）
    3. ジャンル分け（授業 / 日常（プライベート） / 行事：体育祭・文化祭・校外学習）
    4. 1つ1つの録画の容量を表示（動画だけでなく画像も扱える）
    5. 容量の警告は2段階（80%で黄色「そろそろ注意」、100%で赤「上限を超えています」）
    6. 削除した録画はアプリのゴミ箱へ（元に戻せる・30日たつと自動で完全に削除）

  必要なもの: Python 3.8 以上（tkinter は標準で入っています）
  あると便利 : pip install pillow opencv-python  （録画の最初の画面をサムネイル表示できる）
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

try:
    import cv2
except ImportError:
    cv2 = None

# ------------------------------------------------------------------ 設定
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".mts",
             ".m2ts", ".3gp", ".webm", ".flv", ".mpg", ".mpeg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic",
             ".heif", ".tif", ".tiff"}
MEDIA_EXT = VIDEO_EXT | IMAGE_EXT        # 一覧に表示するファイル
DATA_FILE = ".rokuga_kanri.json"        # 再生回数やジャンルを保存するファイル
TRASH_DIR = ".rokuga_gomibako"          # アプリのゴミ箱（録画と同じ場所に作るフォルダ）
TRASH_DAYS = 30                         # ゴミ箱に入れて この日数たつと自動で完全に削除
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


# アプリの色（ここを変えると全体の色が変わる）
COLORS = {
    "bg": "#f4f6fb",          # 画面の背景（ほんのり青みのある白）
    "surface": "#ffffff",     # 一覧・入力欄の背景
    "text": "#2d3142",        # 文字
    "subtext": "#6b7280",     # 補足の文字
    "border": "#d6dbe8",      # 枠線
    "heading": "#e8ecf7",     # 一覧の見出し
    "accent": "#4a6cf7",      # メインの色（青紫）
    "accent_dark": "#3654d6",
    "select": "#cdd8ff",      # 選んだ行
    "danger": "#e5484d",      # 削除・上限オーバー
    "row_del": "#fde8e8",     # ◎ 消してOK の行
    "row_maybe": "#fff6dc",   # ○ 検討 の行
    "row_keep": "#ffffff",    # △ 残す の行
    "row_protect": "#e6f0ff", # 🔒 保護中 の行
    "bar_ok": "#34b37a",      # 容量バー（余裕あり）
    "bar_warn": "#f2b705",    # 容量バー（80%〜）
    "bar_trough": "#e3e7f0",  # 容量バーの空いている部分
    "warn_bg": "#fff1c2",     # 「そろそろ注意」の背景
    "warn_fg": "#7a5a00",
    "preview_bg": "#1f2330",  # プレビューの背景
}
FONT = ("Yu Gothic UI", 10)

THUMB_SIZE = (96, 54)        # 一覧に出す小さいサムネイル
PREVIEW_SIZE = (320, 180)    # 右側のプレビュー


def first_frame(path):
    """動画なら最初の画面、画像ならその画像を PIL.Image で返す（読めなければ None）"""
    if Image is None:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in IMAGE_EXT:
            img = Image.open(path)
            img.load()
            return img.convert("RGB")
        if ext in VIDEO_EXT and cv2 is not None:
            cap = cv2.VideoCapture(path)
            try:
                ok, frame = cap.read()
                # 最初が真っ黒なときは、少し先（1秒後）の画面を使う
                if ok and frame.mean() < 8:
                    cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
                    ok2, frame2 = cap.read()
                    if ok2:
                        frame = frame2
            finally:
                cap.release()
            if ok:
                return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    except Exception:
        pass
    return None


def video_duration(path):
    """動画の長さ（秒）を返す。画像や読めない動画は None"""
    if cv2 is None or os.path.splitext(path)[1].lower() not in VIDEO_EXT:
        return None
    try:
        cap = cv2.VideoCapture(path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        finally:
            cap.release()
        if fps > 0 and frames > 0:
            return frames / fps
    except Exception:
        pass
    return None


def human_duration(sec):
    if sec is None:
        return "-"
    sec = int(round(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fit_image(img, size):
    """縦横比を保ったまま size に収め、余白を黒でうめる"""
    img = img.copy()
    img.thumbnail(size)
    canvas = Image.new("RGB", size, "black")
    canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    return canvas


# ------------------------------------------------------------------ アプリ本体
class App(tk.Tk):
    COLUMNS = [("name", "ファイル名", 280), ("genre", "ジャンル", 130),
               ("size", "容量", 80), ("duration", "長さ", 70),
               ("date", "日付", 90), ("age", "経過日数", 70),
               ("opens", "再生回数", 70), ("score", "消してOK度", 80),
               ("rank", "判定", 90), ("reason", "理由", 260)]
    # 並び順メニュー: (表示名, 列, 大きい順か)
    ORDERS = [("消してOK度が高い順", "score", True), ("容量が大きい順", "size", True),
              ("容量が小さい順", "size", False), ("長さが長い順", "duration", True), ("古い順", "age", True),
              ("新しい順", "age", False), ("再生回数が少ない順", "opens", False)]

    def __init__(self):
        super().__init__()
        self.title("録画おそうじマネージャー")
        self.geometry("1250x680")
        self.minsize(900, 500)
        self.folder = None
        self.data = {"files": {}, "limits": dict(DEFAULT_LIMITS), "extra": [], "trash": []}
        self.items = {}            # 相対パス（追加したファイルはフルパス） -> 表示用情報
        self.sort_col, self.sort_rev = "score", True
        self.frames = {}           # (フルパス, 更新時刻) -> 最初の画面（PIL.Image / None）
        self.thumbs = {}           # (フルパス, 更新時刻) -> 一覧用の小さい画像
        self.thumb_queue = []      # これからサムネイルを作る行
        self.durations = {}        # (フルパス, 更新時刻) -> 動画の長さ（秒）
        self.preview_photo = None
        self.trash_fill = None     # ゴミ箱の画面が開いているとき、その一覧を更新する関数
        self._build_ui()
        # フォルダを選ぶ前でも、前回「ファイルを追加」した録画やゴミ箱を表示する
        self.load_data()
        if self.has_anything():
            self.after(100, self.scan)

    # ---------------- UI
    def _apply_colors(self, style):
        c = COLORS
        self.configure(bg=c["bg"])
        self.option_add("*Toplevel.background", c["bg"])
        self.option_add("*TCombobox*Listbox.background", c["surface"])
        self.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", FONT)

        style.configure(".", background=c["bg"], foreground=c["text"], font=FONT,
                        bordercolor=c["border"], lightcolor=c["bg"], darkcolor=c["border"],
                        troughcolor=c["bar_trough"], focuscolor=c["accent"])
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Sub.TLabel", foreground=c["subtext"])
        style.configure("Title.TLabel", font=(FONT[0], 10, "bold"))
        style.configure("TCheckbutton", background=c["bg"])
        style.map("TCheckbutton", background=[("active", c["bg"])],
                  indicatorcolor=[("selected", c["accent"]), ("!selected", c["surface"])])

        # ボタン：ふつう / メイン（青紫） / 削除（赤文字）
        style.configure("TButton", background=c["surface"], foreground=c["text"],
                        bordercolor=c["border"], lightcolor=c["surface"], darkcolor=c["surface"],
                        padding=(10, 4))
        style.map("TButton", background=[("pressed", c["heading"]), ("active", c["heading"])])
        style.configure("Accent.TButton", background=c["accent"], foreground="white",
                        bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"])
        style.map("Accent.TButton", background=[("pressed", c["accent_dark"]),
                                                ("active", c["accent_dark"])])
        style.configure("Danger.TButton", foreground=c["danger"])

        style.configure("TCombobox", fieldbackground=c["surface"], background=c["surface"],
                        arrowcolor=c["text"])
        style.map("TCombobox", fieldbackground=[("readonly", c["surface"])],
                  selectbackground=[("readonly", c["surface"])],
                  selectforeground=[("readonly", c["text"])])
        style.configure("TEntry", fieldbackground=c["surface"])
        style.configure("TSpinbox", fieldbackground=c["surface"], arrowcolor=c["text"])

        # 一覧
        style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"],
                        foreground=c["text"], bordercolor=c["border"],
                        rowheight=THUMB_SIZE[1] + 6 if Image else 26)
        style.map("Treeview", background=[("selected", c["select"])],
                  foreground=[("selected", c["text"])])
        style.configure("Treeview.Heading", background=c["heading"], foreground=c["text"],
                        font=(FONT[0], 10, "bold"), relief="flat", padding=(4, 4))
        style.map("Treeview.Heading", background=[("active", c["select"])])

        # 容量バー
        for name, color in (("Ok", c["bar_ok"]), ("Warn", c["bar_warn"]), ("Over", c["danger"])):
            style.configure(f"{name}.Horizontal.TProgressbar", background=color,
                            troughcolor=c["bar_trough"], bordercolor=c["border"],
                            lightcolor=color, darkcolor=color, thickness=14)

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._apply_colors(style)

        # フォルダ選択
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="📁 録画フォルダを選ぶ", style="Accent.TButton",
                   command=self.choose_folder).pack(side="left")
        ttk.Button(top, text="➕ ファイルを追加", style="Accent.TButton",
                   command=self.add_files).pack(side="left", padx=(6, 0))
        ttk.Button(top, text="🔄 再読み込み", command=self.scan).pack(side="left", padx=6)
        self.folder_label = ttk.Label(top, text="フォルダが選ばれていません", style="Sub.TLabel")
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
        self.warn_label = tk.Label(cap, text="", font=(FONT[0], 10, "bold"),
                                   bg=COLORS["bg"], padx=6, pady=1)
        self.warn_label.pack(side="left", padx=10)
        self.warn_bg = COLORS["bg"]    # 警告がないときの背景色

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
        ttk.Label(flt, text="並び順:").pack(side="left")
        self.order_var = tk.StringVar(value=self.ORDERS[0][0])
        ob = ttk.Combobox(flt, textvariable=self.order_var, state="readonly",
                          values=[o[0] for o in self.ORDERS], width=20)
        ob.pack(side="left", padx=4)
        ob.bind("<<ComboboxSelected>>", lambda e: self.set_order())
        self.summary_label = ttk.Label(flt, text="")
        self.summary_label.pack(side="right")

        # 一覧
        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        # 右側：選んだ録画の最初の画面を大きく表示
        side = ttk.Frame(mid, padding=(8, 0, 0, 0))
        side.pack(side="right", fill="y")
        ttk.Label(side, text="最初の画面", style="Title.TLabel").pack(anchor="w")
        # 画像がないときも大きさが変わらないよう、空の画像を置いておく
        self.blank_preview = tk.PhotoImage(master=self, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1])
        self.preview_label = tk.Label(side, bg=COLORS["preview_bg"], fg="#c9cede", compound="center",
                                      image=self.blank_preview)
        self.preview_label.pack(pady=4)
        self.preview_name = ttk.Label(side, text="", wraplength=PREVIEW_SIZE[0], justify="left")
        self.preview_name.pack(anchor="w")
        self.show_preview(None)

        # 一覧＋スクロールバー（列が多いので横にもスクロールできるようにする）
        tbl = ttk.Frame(mid)
        tbl.pack(side="left", fill="both", expand=True)
        tbl.rowconfigure(0, weight=1)
        tbl.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(tbl, columns=[c[0] for c in self.COLUMNS],
                                 show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="画面")
        self.tree.column("#0", width=THUMB_SIZE[0] + 24 if Image else 0,
                         stretch=False, anchor="center")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_by(k))
            anchor = "e" if key in ("size", "duration", "age", "opens", "score") else "w"
            self.tree.column(key, width=width, minwidth=40, anchor=anchor,
                             stretch=(key == "reason"))
        self.tree.tag_configure("del", background=COLORS["row_del"])
        self.tree.tag_configure("maybe", background=COLORS["row_maybe"])
        self.tree.tag_configure("keep", background=COLORS["row_keep"])
        self.tree.tag_configure("protect", background=COLORS["row_protect"])
        ysb = ttk.Scrollbar(tbl, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(tbl, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda e: self.play())
        self.tree.bind("<F2>", lambda e: self.rename())
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())

        # 操作ボタン
        # ボタンが多いので2段に分ける（上：録画の操作 / 下：削除とゴミ箱）
        # 窓を小さくしてもボタンが隠れないよう、一覧より先に下へ固定する
        bot = ttk.Frame(self, padding=(8, 8, 8, 6))
        bot.pack(side="bottom", fill="x", before=mid)
        bot2 = ttk.Frame(self, padding=(8, 0, 8, 8))
        bot2.pack(side="bottom", fill="x", before=bot)
        ttk.Button(bot, text="▶ 再生（回数+1）", command=self.play).pack(side="left")
        ttk.Button(bot, text="✏ 名前を変える", command=self.rename).pack(side="left", padx=4)
        ttk.Separator(bot, orient="vertical").pack(side="left", fill="y", padx=8)
        self.genre_var = tk.StringVar(value=GENRES[1])
        ttk.Combobox(bot, textvariable=self.genre_var, values=GENRES,
                     state="readonly", width=18).pack(side="left")
        ttk.Button(bot, text="ジャンルを設定", command=self.set_genre).pack(side="left", padx=4)
        ttk.Separator(bot, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bot, text="🔒 保護 / 解除", command=self.toggle_protect).pack(side="left")
        ttk.Button(bot, text="⏱ 保存日数の設定", command=self.edit_limits).pack(side="left", padx=4)
        self.sel_label = ttk.Label(bot, text="", style="Sub.TLabel")
        self.sel_label.pack(side="right", padx=10)

        ttk.Button(bot2, text="🗑 ゴミ箱に入れる", command=self.delete_selected).pack(side="left")
        ttk.Button(bot2, text="✖ この動画を消す", style="Danger.TButton",
                   command=self.delete_now).pack(side="left", padx=4)
        ttk.Button(bot2, text="↩ ゴミ箱の中身を戻す", command=self.restore_all).pack(side="right")
        ttk.Button(bot2, text="🧹 ゴミ箱を空にする", style="Danger.TButton",
                   command=self.empty_trash).pack(side="right", padx=4)
        self.trash_btn = ttk.Button(bot2, text="♻ ゴミ箱を開く", command=self.open_trash)
        self.trash_btn.pack(side="right")

    # ---------------- データ保存
    def data_path(self):
        # フォルダを選んでいないときは、ホームフォルダに記録する
        return os.path.join(self.folder or os.path.expanduser("~"), DATA_FILE)

    def load_data(self):
        self.data = {"files": {}, "limits": dict(DEFAULT_LIMITS), "extra": [], "trash": []}
        try:
            with open(self.data_path(), encoding="utf-8") as f:
                loaded = json.load(f)
            self.data["files"] = loaded.get("files", {})
            self.data["limits"].update(loaded.get("limits", {}))
            self.data["extra"] = loaded.get("extra", [])
            self.data["trash"] = loaded.get("trash", [])
        except (OSError, ValueError):
            pass

    def has_anything(self):
        return bool(self.folder or self.data["extra"] or self.data["trash"])

    def save_data(self):
        if not self.has_anything() and not os.path.exists(self.data_path()):
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
        skipped, added, too_big = [], [], []
        used = self.used_bytes()
        for p in paths:
            p = os.path.normpath(p)
            if os.path.splitext(p)[1].lower() not in MEDIA_EXT:
                skipped.append(os.path.basename(p))
            elif self.in_folder(p) or os.path.normcase(p) in known:
                continue           # すでに一覧に入っている
            else:
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                # 上限を超えてしまう録画は入れない
                if used + size > MAX_BYTES:
                    too_big.append((os.path.basename(p), size))
                    continue
                used += size
                self.data["extra"].append(p)
                known.add(os.path.normcase(p))
                added.append(p)
        if skipped:
            messagebox.showinfo("お知らせ", "動画・画像ではないので追加しませんでした。\n\n" + "\n".join(skipped))
        if too_big:
            names = "\n".join(f"{n}（{human_size(s)}）" for n, s in too_big[:10])
            if len(too_big) > 10:
                names += "\n…"
            messagebox.showerror(
                "容量オーバー",
                f"上限 {human_size(MAX_BYTES)} を超えてしまうので、次の録画は追加できませんでした。\n"
                f"（今の空き：{human_size(max(0, MAX_BYTES - used))}）\n\n{names}\n\n"
                "「◎ 消してOK」の録画をゴミ箱に入れて、ゴミ箱を空にしてから追加してください。\n"
                f"（ゴミ箱の中の録画も容量を使っています：{human_size(self.trash_bytes())}）")
        self.scan()
        # 追加した録画を選んで、最初の画面をプレビューに出す
        added = [p for p in added if self.tree.exists(p)]
        if added:
            self.tree.selection_set(added)
            self.tree.see(added[0])
            self.tree.focus(added[0])

    def in_folder(self, path):
        if not self.folder:
            return False
        try:
            return os.path.commonpath([os.path.abspath(self.folder), path]) == os.path.abspath(self.folder)
        except ValueError:     # ドライブが違う
            return False

    def scan(self, popup=True):
        if not self.has_anything():
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
            dkey = (full, st.st_mtime)
            if dkey not in self.durations:
                self.durations[dkey] = video_duration(full)
            self.items[key] = {"rel": key, "full": full, "size": st.st_size, "mtime": st.st_mtime,
                               "duration": self.durations[dkey],
                               "date": date, "age": (today - date).days,
                               "genre": meta["genre"], "opens": meta["opens"],
                               "protect": meta.get("protect", False)}

        if self.folder:
            for root, dirs, names in os.walk(self.folder):
                if TRASH_DIR in dirs:
                    dirs.remove(TRASH_DIR)       # ゴミ箱の中は一覧に出さない
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
        self.purge_trash()
        self.save_data()
        self.refresh_table()
        self.update_capacity(show_popup=popup)

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
                   "duration": lambda i: i["duration"] if i["duration"] is not None else -1,
                   "age": lambda i: i["age"], "opens": lambda i: i["opens"],
                   "score": lambda i: i["score"], "rank": lambda i: i["score"],
                   "reason": lambda i: len(i["reasons"])}[self.sort_col]
        rows.sort(key=keyfunc, reverse=self.sort_rev)

        self.thumb_queue = []
        for i in rows:
            s = i["score"]
            tag = "protect" if i["protect"] else ("del" if s >= 60 else "maybe" if s >= 35 else "keep")
            rank = "🔒 保護中" if i["protect"] else rank_label(s)
            thumb = self.thumbs.get(self.frame_key(i))
            self.tree.insert("", "end", iid=i["rel"], tags=(tag,), image=thumb or "", values=(
                i["rel"], i["genre"], human_size(i["size"]), human_duration(i["duration"]),
                i["date"].isoformat(),
                f"{i['age']}日", i["opens"], s, rank, "・".join(i["reasons"])))
            if thumb is None and Image is not None:
                self.thumb_queue.append(i["rel"])
        # サムネイル作りは少しずつ行い、画面が固まらないようにする
        if self.thumb_queue:
            self.after(10, self.make_thumbs)

        total = sum(i["size"] for i in self.items.values())
        cand = [i for i in self.items.values() if i["score"] >= 60]
        self.summary_label.config(
            text=f"録画 {len(self.items)}本 / 合計 {human_size(total)}　｜　"
                 f"◎消してOK {len(cand)}本（{human_size(sum(i['size'] for i in cand))} 空く）")
        self.update_selection_info()

    # ---------------- 最初の画面（サムネイル・プレビュー）
    @staticmethod
    def frame_key(info):
        return info["full"], info["mtime"]

    def get_frame(self, info):
        key = self.frame_key(info)
        if key not in self.frames:
            self.frames[key] = first_frame(info["full"])
        return self.frames[key]

    def make_thumbs(self):
        for _ in range(3):
            if not self.thumb_queue:
                return
            rel = self.thumb_queue.pop(0)
            info = self.items.get(rel)
            if info is None or not self.tree.exists(rel):
                continue
            key = self.frame_key(info)
            if key not in self.thumbs:
                img = self.get_frame(info)
                self.thumbs[key] = ImageTk.PhotoImage(fit_image(img, THUMB_SIZE)) if img else ""
            self.tree.item(rel, image=self.thumbs[key])
        self.after(1, self.make_thumbs)

    def show_preview(self, info):
        if info is None:
            text = "録画を選ぶと\nここに最初の画面が出ます"
            if Image is None:
                text = "サムネイル表示には\npip install pillow opencv-python\nが必要です"
            self.preview_label.config(image=self.blank_preview, text=text)
            self.preview_name.config(text="")
            return
        img = self.get_frame(info)
        if img is None:
            self.preview_label.config(image=self.blank_preview, text="（画面を読み込めませんでした）")
        else:
            self.preview_photo = ImageTk.PhotoImage(fit_image(img, PREVIEW_SIZE))
            self.preview_label.config(image=self.preview_photo, text="")
        length = f"・長さ {human_duration(info['duration'])}" if info["duration"] is not None else ""
        self.preview_name.config(text=f"{os.path.basename(info['full'])}\n"
                                      f"{human_size(info['size'])}{length}・{info['date'].isoformat()}")

    def on_select(self):
        self.update_selection_info()
        sel = self.tree.selection()
        self.show_preview(self.items.get(sel[0]) if sel else None)

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, col in ("size", "duration", "age", "score", "rank")
        # 見出しクリックでの並び順をメニューにも反映する
        for name, c, rev in self.ORDERS:
            if (c, rev) == (self.sort_col, self.sort_rev):
                self.order_var.set(name)
                break
        else:
            self.order_var.set("")
        self.refresh_table()

    def set_order(self):
        for name, col, rev in self.ORDERS:
            if name == self.order_var.get():
                self.sort_col, self.sort_rev = col, rev
                break
        self.refresh_table()

    def update_selection_info(self):
        sel = self.tree.selection()
        if sel:
            size = sum(self.items[r]["size"] for r in sel if r in self.items)
            self.sel_label.config(text=f"選択中 {len(sel)}本（{human_size(size)}）")
        else:
            self.sel_label.config(text="")

    def update_capacity(self, show_popup=False):
        used = self.used_bytes()
        trash = self.trash_bytes()
        ratio = used / MAX_BYTES
        self.cap_bar["value"] = min(100, ratio * 100)
        self.cap_label.config(text=f"{ratio * 100:.1f}%（{human_size(used)} / "
                                   f"{human_size(MAX_BYTES)}、残り {human_size(max(0, MAX_BYTES - used))}"
                                   + (f"、うちゴミ箱 {human_size(trash)}" if trash else "") + "）")
        n = len(self.data["trash"])
        self.trash_btn.config(text=f"♻ ゴミ箱を開く（{n}）" if n else "♻ ゴミ箱を開く")
        # 警告は2段階：80%で黄色「そろそろ注意」、100%で赤「上限を超えています」
        if ratio >= 1.0:
            level = "over"
            self.cap_bar.config(style="Over.Horizontal.TProgressbar")
            msg = "上限を超えています！" if ratio > 1.0 else "上限に達しています！"
            self.warn_label.config(text=f" ⛔ {msg} ", bg=COLORS["danger"], fg="white")
        elif ratio >= WARN_RATIO:
            level = "warn"
            self.cap_bar.config(style="Warn.Horizontal.TProgressbar")
            self.warn_label.config(text=" ⚠ そろそろ注意 ", bg=COLORS["warn_bg"], fg=COLORS["warn_fg"])
        else:
            level = None
            self.cap_bar.config(style="Ok.Horizontal.TProgressbar")
            self.warn_label.config(text="", bg=self.warn_bg)
        if show_popup and level:
            cand = [i for i in self.items.values() if i["score"] >= 60]
            hint = (f"「◎ 消してOK」の録画が {len(cand)}本あります。\n"
                    f"全部消すと約 {human_size(sum(i['size'] for i in cand))} 空きます。")
            if trash:
                hint += f"\nゴミ箱を空にすると {human_size(trash)} 空きます。"
            if level == "over":
                messagebox.showerror(
                    "上限を超えています",
                    f"録画の合計が上限 {human_size(MAX_BYTES)} の {ratio * 100:.1f}% です。\n"
                    f"{human_size(max(0, used - MAX_BYTES))} オーバーしています。新しい録画は追加できません。\n\n{hint}")
            else:
                messagebox.showwarning(
                    "そろそろ注意",
                    f"録画の合計が上限 {human_size(MAX_BYTES)} の {ratio * 100:.1f}% です（80%超え）。\n"
                    f"残りは {human_size(MAX_BYTES - used)} です。\n\n{hint}")

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

    def rename(self):
        sel = self.selected()
        if not sel:
            return
        rel = sel[0]
        full = self.items[rel]["full"]
        folder, filename = os.path.split(full)
        base, ext = os.path.splitext(filename)

        win = tk.Toplevel(self)
        win.title("名前を変える")
        win.transient(self)
        win.resizable(False, False)
        win.grab_set()
        ttk.Label(win, text=f"今の名前：{filename}", padding=(10, 10, 10, 4)).grid(
            row=0, column=0, columnspan=3, sticky="w")
        name_var = tk.StringVar(value=base)
        entry = ttk.Entry(win, textvariable=name_var, width=40)
        entry.grid(row=1, column=0, columnspan=2, padx=(10, 2), sticky="we")
        ttk.Label(win, text=ext).grid(row=1, column=2, padx=(0, 10), sticky="w")
        entry.focus_set()
        entry.select_range(0, "end")

        def ok(_e=None):
            new_base = name_var.get().strip()
            bad = set('\\/:*?"<>|')
            if not new_base:
                messagebox.showerror("入力エラー", "名前を入れてください。", parent=win)
                return
            if bad & set(new_base) or new_base.endswith("."):
                messagebox.showerror("入力エラー",
                                     '名前に次の文字は使えません：\\ / : * ? " < > |', parent=win)
                return
            if new_base == base:
                win.destroy()
                return
            new_full = os.path.join(folder, new_base + ext)
            # 大文字・小文字だけの変更は同じファイルなので重複扱いにしない
            if os.path.exists(new_full) and os.path.normcase(new_full) != os.path.normcase(full):
                messagebox.showerror("名前が重なっています",
                                     f"「{new_base + ext}」はすでにあります。別の名前にしてください。",
                                     parent=win)
                return
            try:
                os.rename(full, new_full)
            except OSError as e:
                messagebox.showerror("名前を変えられませんでした", str(e), parent=win)
                return
            # 再生回数・ジャンル・保護の記録を新しい名前に引き継ぐ
            if rel in self.data["extra"]:
                new_key = new_full
                self.data["extra"][self.data["extra"].index(rel)] = new_full
            else:
                new_key = os.path.relpath(new_full, self.folder)
            meta = self.data["files"].pop(rel, None)
            if meta is not None:
                self.data["files"][new_key] = meta
            win.destroy()
            self.save_data()
            self.scan(popup=False)
            if self.tree.exists(new_key):
                self.tree.selection_set(new_key)
                self.tree.see(new_key)

        btns = ttk.Frame(win, padding=10)
        btns.grid(row=2, column=0, columnspan=3, sticky="e")
        ttk.Button(btns, text="キャンセル", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="OK", command=ok).pack(side="right", padx=6)
        win.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())

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

    def selected_unprotected(self):
        sel = self.selected()
        if any(self.items[r]["protect"] for r in sel):
            messagebox.showwarning("保護中", "保護中の録画が含まれています。先に保護を解除してください。")
            return []
        return sel

    def delete_now(self):
        """ゴミ箱を通さず、すぐに完全に削除する"""
        sel = self.selected_unprotected()
        if not sel:
            return
        size = sum(self.items[r]["size"] for r in sel)
        names = "\n".join(sel[:10]) + ("\n…" if len(sel) > 10 else "")
        if not messagebox.askyesno(
                "完全に削除",
                f"{len(sel)}本（{human_size(size)}）をゴミ箱に入れずに完全に削除します。\n"
                f"元に戻せません。\n\n{names}\n\nよろしいですか？", icon="warning", default="no"):
            return
        errors = []
        for rel in sel:
            try:
                os.remove(self.items[rel]["full"])
            except OSError as e:
                errors.append(f"{rel}: {e}")
                continue
            self.data["files"].pop(rel, None)
            if rel in self.data["extra"]:
                self.data["extra"].remove(rel)
            self.items.pop(rel, None)
        self.save_data()
        self.refresh_table()
        self.update_capacity()
        if errors:
            messagebox.showerror("削除できなかった録画", "\n".join(errors))

    def delete_selected(self):
        sel = self.selected_unprotected()
        if not sel:
            return
        errors = []
        for rel in sel:
            try:
                self.move_to_trash(rel)
            except OSError as e:
                errors.append(f"{rel}: {e}")
        self.save_data()
        self.refresh_table()
        self.update_capacity()
        if errors:
            messagebox.showerror("ゴミ箱に入れられなかった録画", "\n".join(errors))

    # ---------------- ゴミ箱
    def used_bytes(self):
        """一覧の録画 + ゴミ箱の中身（ゴミ箱を空にするまで容量は空かない）"""
        return sum(i["size"] for i in self.items.values()) + self.trash_bytes()

    def trash_bytes(self):
        return sum(t["size"] for t in self.data["trash"])

    def move_to_trash(self, rel):
        info = self.items[rel]
        is_extra = rel in self.data["extra"]
        # 録画と同じドライブにゴミ箱を作るので、移動は一瞬で終わる
        base = self.folder if (self.folder and not is_extra) else os.path.dirname(info["full"])
        trash_dir = os.path.join(base, TRASH_DIR)
        os.makedirs(trash_dir, exist_ok=True)
        now = datetime.datetime.now()
        dest = os.path.join(trash_dir, now.strftime("%Y%m%d%H%M%S_") + os.path.basename(info["full"]))
        n = 1
        while os.path.exists(dest):
            root, ext = os.path.splitext(dest)
            dest, n = f"{root}_{n}{ext}", n + 1
        shutil.move(info["full"], dest)
        self.data["trash"].append({
            "key": rel, "orig": info["full"], "path": dest, "size": info["size"],
            "extra": is_extra, "deleted": now.isoformat(timespec="seconds"),
            "meta": self.data["files"].pop(rel, None)})
        if is_extra:
            self.data["extra"].remove(rel)
        self.items.pop(rel, None)

    def trash_days_left(self, t):
        deleted = datetime.datetime.fromisoformat(t["deleted"])
        return TRASH_DAYS - (datetime.datetime.now() - deleted).days

    def purge_trash(self):
        """ファイルがなくなったもの・期限が過ぎたものをゴミ箱から消す"""
        keep = []
        for t in self.data["trash"]:
            if not os.path.exists(t["path"]):
                continue
            if self.trash_days_left(t) <= 0:
                try:
                    os.remove(t["path"])
                    continue
                except OSError:
                    pass
            keep.append(t)
        self.data["trash"] = keep

    def restore_from_trash(self, t):
        dest = t["orig"]
        root, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):            # 同じ名前があれば「_復元1」などを付ける
            dest, n = f"{root}_復元{n}{ext}", n + 1
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(t["path"], dest)
        self.data["trash"].remove(t)
        if t["extra"] or not self.in_folder(dest):
            key = dest
            self.data["extra"].append(dest)
        elif self.folder:
            key = os.path.relpath(dest, self.folder)
        else:
            key = dest
        if t.get("meta"):
            self.data["files"][key] = t["meta"]

    def delete_forever(self, t):
        try:
            os.remove(t["path"])
        except FileNotFoundError:
            pass
        self.data["trash"].remove(t)

    def open_trash(self):
        win = tk.Toplevel(self)
        win.title("ゴミ箱")
        win.geometry("760x420")
        win.transient(self)
        ttk.Label(win, padding=8, text=f"ゴミ箱の録画は {TRASH_DAYS}日たつと自動で完全に削除されます。"
                  "完全に削除するまで容量は空きません。").pack(anchor="w")
        frm = ttk.Frame(win, padding=(8, 0))
        frm.pack(fill="both", expand=True)
        cols = [("name", "ファイル名", 300), ("size", "容量", 80),
                ("deleted", "ゴミ箱に入れた日", 140), ("left", "自動削除まで", 100)]
        tv = ttk.Treeview(frm, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for key, title, width in cols:
            tv.heading(key, text=title)
            tv.column(key, width=width, anchor="e" if key in ("size", "left") else "w")
        sb = ttk.Scrollbar(frm, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        info = ttk.Label(win, padding=(8, 4))
        info.pack(anchor="w")

        def fill():
            tv.delete(*tv.get_children())
            for idx, t in enumerate(self.data["trash"]):
                tv.insert("", "end", iid=str(idx), values=(
                    os.path.basename(t["orig"]), human_size(t["size"]),
                    t["deleted"].replace("T", " "), f"あと{max(0, self.trash_days_left(t))}日"))
            info.config(text=f"{len(self.data['trash'])}本 / 合計 {human_size(self.trash_bytes())}")

        def chosen():
            items = [self.data["trash"][int(i)] for i in tv.selection()]
            if not items:
                messagebox.showinfo("お知らせ", "ゴミ箱の中から録画を選んでください。", parent=win)
            return items

        def restore():
            self.restore_items(chosen(), parent=win)

        bot = ttk.Frame(win, padding=8)
        bot.pack(fill="x")
        ttk.Button(bot, text="↩ 元に戻す", command=restore).pack(side="left")
        ttk.Button(bot, text="✖ この動画を消す", style="Danger.TButton",
                   command=lambda: self.remove_from_trash(chosen(), parent=win)).pack(side="left", padx=6)
        ttk.Button(bot, text="🧹 ゴミ箱を空にする", style="Danger.TButton",
                   command=lambda: self.empty_trash(parent=win)).pack(side="right")
        fill()
        # メイン画面から空にしたときも、この画面を更新する
        self.trash_fill = lambda: fill() if win.winfo_exists() else None

    def restore_all(self):
        if not self.data["trash"]:
            messagebox.showinfo("お知らせ", "ゴミ箱は空です。")
            return
        n = len(self.data["trash"])
        self.restore_items(list(self.data["trash"]))
        messagebox.showinfo("お知らせ", f"ゴミ箱の {n - len(self.data['trash'])}本を元に戻しました。")

    def restore_items(self, items, parent=None):
        """ゴミ箱の録画を元の場所に戻す"""
        if not items:
            return
        # 元に戻しても、ゴミ箱の分ですでに容量に数えているので上限は変わらない
        errors = []
        for t in items:
            try:
                self.restore_from_trash(t)
            except OSError as e:
                errors.append(f"{os.path.basename(t['orig'])}: {e}")
        self.save_data()
        self.scan(popup=False)
        self.update_capacity()
        if self.trash_fill:
            self.trash_fill()
        if errors:
            messagebox.showerror("元に戻せなかった録画", "\n".join(errors), parent=parent or self)

    def empty_trash(self, parent=None):
        if not self.data["trash"]:
            messagebox.showinfo("お知らせ", "ゴミ箱は空です。", parent=parent or self)
            return
        self.remove_from_trash(list(self.data["trash"]), parent=parent)

    def remove_from_trash(self, items, parent=None):
        """ゴミ箱の録画を確認してから完全に削除する"""
        if not items:
            return
        parent = parent or self
        size = sum(t["size"] for t in items)
        names = "\n".join(os.path.basename(t["orig"]) for t in items[:10])
        if len(items) > 10:
            names += "\n…"
        if not messagebox.askyesno(
                "完全に削除", f"ゴミ箱の {len(items)}本（{human_size(size)}）を完全に削除します。\n"
                f"元に戻せません。\n\n{names}\n\nよろしいですか？",
                icon="warning", default="no", parent=parent):
            return
        errors = []
        for t in items:
            try:
                self.delete_forever(t)
            except OSError as e:
                errors.append(f"{os.path.basename(t['orig'])}: {e}")
        self.save_data()
        self.scan(popup=False)
        self.update_capacity()
        if self.trash_fill:
            self.trash_fill()
        if errors:
            messagebox.showerror("エラー", "\n".join(errors), parent=parent)

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
