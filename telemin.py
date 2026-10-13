# ------------------------------------------------------------------
# Gemini へ（Claude より 2026-10-14）:
#   今回の修正内容を共有します。このあたりを触るときは参考にしてください。
#   1. 全画面表示: namedWindow を WINDOW_NORMAL にし、起動時に全画面化。
#      'f' キーで切り替え。4:3 のカメラ映像は letterbox_to_screen() で黒帯を付けて
#      画面比率に合わせています。黒帯ぶんのズレは display_offset_x/y で
#      on_mouse() 内で補正しているので、ボタン座標は従来どおり元画像基準でOKです。
#   2. 日本語表示: cv2.putText は日本語が「???」になるため、曲名など日本語が
#      入りうる文字列は put_text_jp()（Pillow + Windows のメイリオ等）で描画してください。
#      Pillow / フォントが無い環境では cv2.putText にフォールバックします。
# ------------------------------------------------------------------
import cv2
import numpy as np
import math
import colorsys
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido
import threading
import time
import os
import glob
import song_fetcher

# =======
# 和音や音階リストを定義する配列
# =======
CHORD_MODE = True
ALLOWED_NOTES = [48, 50, 52, 53, 55, 57, 59, 60]

KEY_PRESETS = {
    'C_major': [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67],  # ハ長調（C, D, E, F, G, A, B...）
    'A_minor': [45, 47, 48, 50, 52, 53, 56, 57, 59, 60, 62, 64],  # イ短調（A, B, C, D, E, F, G...）
    'G_major': [43, 45, 47, 48, 50, 52, 54, 55, 57, 59, 60, 62],  # ト長調（Fが#する例: 54番）
    'D_major': [50, 52, 54, 55, 57, 59, 61, 62, 64, 66, 67, 69, 71, 73, 74],  # ニ長調（カノン原曲調: D, E, F#, G, A, B, C#）
    'A_pentatonic_minor': [45, 48, 50, 52, 55, 57, 60, 62, 64, 67, 69, 72],  # 藍調/ロック（A, C, D, E, G...）
    'Ryukyu': [48, 52, 53, 55, 59, 60, 64, 65, 67, 71, 72, 76],  # 琉球音階（沖縄風: C, E, F, G, B...）
    'Miyakobushi': [50, 51, 55, 57, 58, 62, 63, 67, 69, 70, 74, 75]  # 都節音階（和風: D, Eb, G, A, Bb...）
}
CURRENT_KEY = 'C_major'

# HUDメニュー表示用定義
SCALE_MENU_ITEMS = [
    ('1', 'C_major', 'C Major (ハ長調)'),
    ('2', 'A_minor', 'A Minor (イ短調)'),
    ('3', 'G_major', 'G Major (ト長調)'),
    ('4', 'A_pentatonic_minor', 'Pentatonic (ペンタトニック)'),
    ('5', 'Ryukyu', 'Ryukyu (琉球音階)'),
    ('6', 'Miyakobushi', 'Miyakobushi (都節音階)'),
    ('7', 'D_major', 'D Major (カノン/ニ長調)')
]

# =======
# 検出範囲・モードの設定
# =======
Y_MIN_LIMIT = 0.15  # これより上（0.0〜0.15）は無視
Y_MAX_LIMIT = 0.85  # これより下（0.85〜1.0）は無視
PINCH_THRESHOLD = 0.7  # つまみ判定のしきい値（0.0〜1.0）

# 波形変化モードのデフォルト状態
WAVE_MODE = False

# 和音モード（ひも引っ張り発音）のデフォルト状態
STRUM_MODE = True

# 自動演奏モードのデフォルト状態
AUTO_PLAY_ENABLED = False

# デバッグ表示（ステータス・ノート名・ピンチ％・赤い境界線など）のデフォルト状態
# 'd' キー、または設定画面のボタンで切り替え可能
SHOW_DEBUG = False

# 虹色レーン（ゲーミング）の設定
RAINBOW_LANES = True
RAINBOW_ALPHA = 0.30         # 通常レーンの濃さ（0.0〜1.0）
RAINBOW_ACTIVE_ALPHA = 0.70  # 発音中レーンの濃さ（0.0〜1.0）
RAINBOW_SPEED = 0.15         # 虹が流れる速さ（1秒あたり色相が1周する割合）

# UIの状態管理 ('PLAY' = 演奏画面, 'SETTINGS' = 設定画面)
UI_STATE = 'PLAY'

# 全画面表示の設定（'f' キーで切り替え可能）
FULLSCREEN = True

# マウス操作用変数
mouse_x, mouse_y = -1, -1
mouse_clicked = False

# 全画面時に黒帯（レターボックス）を足した量。マウス座標を元の画像座標に戻すために使う
display_offset_x, display_offset_y = 0, 0

def on_mouse(event, x, y, flags, param):
    global mouse_x, mouse_y, mouse_clicked
    if event == cv2.EVENT_LBUTTONDOWN:
        mouse_x, mouse_y = x - display_offset_x, y - display_offset_y
        mouse_clicked = True

def get_screen_size():
    """モニターの解像度を取得する（Windows以外や取得失敗時は None）"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetProcessDPIAware()  # 拡大率(125%など)の影響を受けない実解像度を取る
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return None

def apply_window_mode(window_name, fullscreen):
    mode = cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL
    cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, mode)

def letterbox_to_screen(img, screen_size):
    """カメラ映像(4:3など)を画面の縦横比に合わせて黒帯で囲む（引き伸ばしによる歪み防止）"""
    global display_offset_x, display_offset_y
    if screen_size is None:
        display_offset_x, display_offset_y = 0, 0
        return img
    sw, sh = screen_size
    ih, iw = img.shape[:2]
    target_ratio = sw / sh
    if iw / ih < target_ratio:
        # 画面のほうが横長 → 左右に黒帯
        new_w = int(round(ih * target_ratio))
        pad = new_w - iw
        left = pad // 2
        display_offset_x, display_offset_y = left, 0
        return cv2.copyMakeBorder(img, 0, 0, left, pad - left, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    else:
        # 画面のほうが縦長 → 上下に黒帯
        new_h = int(round(iw / target_ratio))
        pad = new_h - ih
        top = pad // 2
        display_offset_x, display_offset_y = 0, top
        return cv2.copyMakeBorder(img, top, pad - top, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))

# 日本語テキスト描画ヘルパー
# cv2.putText は日本語に対応しておらず「???」になるため、Pillow + Windows標準フォントで描画する
JP_FONT_CANDIDATES = [
    r'C:\Windows\Fonts\meiryo.ttc',
    r'C:\Windows\Fonts\YuGothM.ttc',
    r'C:\Windows\Fonts\msgothic.ttc',
]
_jp_font_cache = {}

def _get_jp_font(size):
    if size in _jp_font_cache:
        return _jp_font_cache[size]
    font = None
    try:
        from PIL import ImageFont
        for path in JP_FONT_CANDIDATES:
            if os.path.exists(path):
                font = ImageFont.truetype(path, size)
                break
    except ImportError:
        pass
    _jp_font_cache[size] = font
    return font

def put_text_jp(img, text, org, font_size=16, color=(255, 255, 255), bg_color=None):
    """日本語を含む文字列を描画する。org は文字の左上座標。color は BGR。
    bg_color を指定すると文字の後ろに背景の帯を敷く（レーンの音名と重なっても読めるように）。
    Pillow や日本語フォントが無い環境では cv2.putText にフォールバックする。"""
    x, y = org
    font = _get_jp_font(font_size)
    if font is None:
        if bg_color is not None:
            (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_size / 35, 1)
            cv2.rectangle(img, (x - 4, y - 3), (x + tw + 4, y + th + 5), bg_color, -1)
        cv2.putText(img, text, (x, y + font_size - 4), cv2.FONT_HERSHEY_SIMPLEX, font_size / 35, color, 1)
        return

    from PIL import Image, ImageDraw
    left, top, right, bottom = font.getbbox(text)
    x0, y0 = max(0, x - 4), max(0, y - 3)
    x1 = min(img.shape[1], x + right + 4)
    y1 = min(img.shape[0], y + bottom + 5)
    if x1 <= x0 or y1 <= y0:
        return
    if bg_color is not None:
        cv2.rectangle(img, (x0, y0), (x1, y1), bg_color, -1)

    # 必要な範囲だけ切り出して描画する（毎フレーム全体を変換すると重いため）
    roi = img[y0:y1, x0:x1]
    pil_img = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    draw.text((x - x0, y - y0), text, font=font, fill=(color[2], color[1], color[0]))
    img[y0:y1, x0:x1] = cv2.cvtColor(np.asarray(pil_img), cv2.COLOR_RGB2BGR)

# ボタンの当たり判定ヘルパー
def is_in_rect(px, py, rx, ry, rw, rh):
    return rx <= px <= rx + rw and ry <= py <= ry + rh

# HUDボタン描画ヘルパー
def draw_button(img, text, x, y, w, h, is_active, active_color=(255, 255, 0), inactive_color=(100, 100, 100)):
    bg_color = (60, 60, 60) if not is_active else (90, 90, 90)
    border_color = active_color if is_active else inactive_color
    thickness = 2 if is_active else 1

    # 角丸の代わりに四角形を塗る
    cv2.rectangle(img, (x, y), (x + w, y + h), bg_color, -1)
    cv2.rectangle(img, (x, y), (x + w, y + h), border_color, thickness)

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5
    text_thickness = 2 if is_active else 1
    text_color = (255, 255, 255) if is_active else (200, 200, 200)

    text_size = cv2.getTextSize(text, font, font_scale, text_thickness)[0]
    text_x = x + (w - text_size[0]) // 2
    text_y = y + (h + text_size[1]) // 2
    cv2.putText(img, text, (text_x, text_y), font, font_scale, text_color, text_thickness)

# ==========================================
# 虹色ヘルパー関数
# ==========================================
def rainbow_bgr(pos, offset=0.0):
    """pos(0.0〜1.0)と時間オフセットから、OpenCV用のBGR虹色を返す"""
    hue = (pos + offset) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return (int(b * 255), int(g * 255), int(r * 255))

# ==========================================
# 音名変換ヘルパー関数
# ==========================================
def midi_to_note_name(note):
    if note is None:
        return "OFF"
    note_names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (note // 12) - 1
    name = note_names[note % 12]
    return f"{name}{octave}"

# ==========================================
# わけわからん
# ==========================================
def extract_hand_features(hand_landmarks):
    features = {}

    # 【音程用】親指の先端(4番)のY座標 (0.0=上, 1.0=下) ※変更不要箇所
    features['index_y'] = hand_landmarks[4].y

    # 【波形用】親指の先端(4番)のX座標 (0.0=左, 1.0=右) ※こちらも親指に統一
    features['index_x'] = hand_landmarks[4].x

    # 【音量・エフェクト用】親指(4番)と人差し指(8番)の距離（ピンチ具合）
    thumb = hand_landmarks[4]
    index = hand_landmarks[8]
    pinch_dist = math.hypot(thumb.x - index.x, thumb.y - index.y)
    features['pinch'] = max(0.0, 1.0 - (pinch_dist * 6.0))

    # 【今後の拡張用】手首(0番)のY座標など
    features['wrist_y'] = hand_landmarks[0].y

    # 💡 【将来の拡張用CC候補スロット】
    # 1. Filter Cutoff (CC#74): 例として中指のピンチや手の傾きを使用可能
    # 2. Modulation Wheel (CC#1): 例として小指の曲げ具合や手の細かな震え
    # 3. Resonance (CC#71): 例として薬指の位置
    # 今はデフォルト値をプレースホルダーとして定義しておきます
    features['filter_cutoff'] = max(0.0, min(1.0, 1.0 - hand_landmarks[12].y))  # 中指の高さでカットオフ
    features['modulation_wheel'] = 0.0  # 今後実装
    features['resonance'] = 0.5  # 今後実装

    return features

# ==========================================
# 自動演奏用メロディデータ & クラス
# ==========================================

# 1. YMO「ライディーン」
RYDEEN_MELODY = [
    (67, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (74, 0.4), (71, 0.4),
    (69, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (67, 0.6), (0, 0.2),
    (67, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (74, 0.4), (76, 0.4),
    (79, 0.2), (76, 0.2), (74, 0.2), (71, 0.2), (74, 0.6), (0, 0.4)
]

# 2. 黒うさP / 初音ミク「千本桜」サビ
SENBONZAKURA_MELODY = [
    (69, 0.18), (69, 0.18), (69, 0.18), (72, 0.25), (69, 0.18), (67, 0.25),
    (69, 0.18), (69, 0.18), (69, 0.18), (72, 0.25), (74, 0.35), (0, 0.15),
    (72, 0.18), (74, 0.18), (76, 0.25), (76, 0.25), (74, 0.25),
    (72, 0.18), (69, 0.18), (67, 0.25), (69, 0.45), (0, 0.3),
    (69, 0.18), (69, 0.18), (69, 0.18), (72, 0.25), (69, 0.18), (67, 0.25),
    (69, 0.18), (69, 0.18), (69, 0.18), (72, 0.25), (74, 0.35), (0, 0.15),
    (72, 0.18), (74, 0.18), (76, 0.25), (76, 0.25), (74, 0.25),
    (72, 0.18), (69, 0.18), (67, 0.25), (69, 0.5), (0, 0.4)
]

# 3. 高橋洋子「残酷な天使のテーゼ」サビ
ZANKOKU_MELODY = [
    (72, 0.3), (75, 0.2), (77, 0.3), (75, 0.2), (74, 0.25), (72, 0.4), (0, 0.15),
    (74, 0.2), (74, 0.2), (75, 0.2), (77, 0.25), (79, 0.35), (77, 0.2), (75, 0.2), (74, 0.4), (0, 0.2),
    (72, 0.25), (75, 0.25), (77, 0.25), (79, 0.45), (0, 0.15),
    (82, 0.3), (80, 0.2), (79, 0.25), (77, 0.25), (79, 0.35), (75, 0.25), (72, 0.6), (0, 0.5)
]

# 4. 東方幻想郷「Bad Apple!!」サビ
BAD_APPLE_MELODY = [
    (71, 0.2), (71, 0.2), (71, 0.2), (71, 0.2), (72, 0.2), (71, 0.2), (69, 0.2), (67, 0.2),
    (69, 0.2), (69, 0.2), (69, 0.2), (69, 0.2), (71, 0.2), (69, 0.2), (67, 0.2), (66, 0.2),
    (67, 0.2), (67, 0.2), (67, 0.2), (67, 0.2), (69, 0.2), (67, 0.2), (66, 0.2), (64, 0.2),
    (66, 0.2), (67, 0.2), (69, 0.2), (71, 0.3), (69, 0.2), (67, 0.2), (66, 0.3), (0, 0.4)
]

# 5. 東方紅魔郷「U.N.オーエンは彼女なのか？」メインフレーズ
UN_OWEN_MELODY = [
    (71, 0.15), (76, 0.15), (71, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (76, 0.15),
    (71, 0.15), (76, 0.15), (71, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (76, 0.15),
    (79, 0.15), (78, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (75, 0.15), (71, 0.25), (0, 0.1),
    (71, 0.15), (76, 0.15), (71, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (76, 0.15),
    (71, 0.15), (76, 0.15), (71, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (76, 0.15),
    (79, 0.15), (78, 0.15), (76, 0.15), (75, 0.15), (76, 0.15), (78, 0.15), (79, 0.2), (81, 0.35), (0, 0.4)
]

# 6. J.パッヘルベル「カノン」D Major (Andante ♩=84 楽譜準拠)
CANON_MELODY = [
    # 序奏和音（D - A - Bm - F#m - G - D - G - A）
    (74, 0.71), (73, 0.71), (71, 0.71), (69, 0.71), (67, 0.71), (66, 0.71), (67, 0.71), (69, 0.71),
    # 第1主題（四分音符の優美な主旋律）
    (78, 0.71), (76, 0.71), (74, 0.71), (73, 0.71),
    (71, 0.71), (69, 0.71), (71, 0.71), (73, 0.71),
    (74, 0.71), (73, 0.71), (71, 0.71), (69, 0.71),
    (67, 0.71), (66, 0.71), (67, 0.71), (64, 0.71),
    # 小節13〜16
    (66, 0.71), (69, 0.71), (67, 0.71), (66, 0.71),
    (64, 0.71), (62, 0.71), (64, 0.71), (66, 0.71),
    (67, 0.71), (71, 0.71), (69, 0.71), (67, 0.71),
    (66, 0.71), (64, 0.71), (66, 0.71), (67, 0.71),
    # 第2主題（八分音符の流れる旋律）
    (78, 0.36), (74, 0.36), (76, 0.36), (73, 0.36), (74, 0.36), (71, 0.36), (73, 0.36), (69, 0.36),
    (71, 0.36), (67, 0.36), (69, 0.36), (66, 0.36), (67, 0.36), (64, 0.36), (66, 0.36), (62, 0.36),
    (74, 0.36), (71, 0.36), (73, 0.36), (69, 0.36), (71, 0.36), (67, 0.36), (69, 0.36), (66, 0.36),
    (67, 0.36), (64, 0.36), (66, 0.36), (62, 0.36), (64, 0.36), (61, 0.36), (62, 0.36), (64, 0.36),
    (74, 1.43), (0, 0.5)
]


class AutoPlayer:
    def __init__(self, outport):
        self.outport = outport
        self.running = False
        self.paused = True
        self.thread = None
        self.lock = threading.Lock()
        self.current_idx = 0
        self.song_changed = False
        self.songs = []
        self._init_song_catalog()

    def _init_song_catalog(self):
        """演奏可能な楽曲リスト（プリセット＋ローカルMIDI＋オンラインカタログ）を構築"""
        self.songs = [
            {"type": "preset", "title": "カノン (パッヘルベル)", "data": CANON_MELODY},
            {"type": "preset", "title": "ライディーン (YMO)", "data": RYDEEN_MELODY},
            {"type": "preset", "title": "千本桜 (初音ミク)", "data": SENBONZAKURA_MELODY},
            {"type": "preset", "title": "残酷な天使のテーゼ (エヴァ)", "data": ZANKOKU_MELODY},
            {"type": "preset", "title": "Bad Apple!! (東方)", "data": BAD_APPLE_MELODY},
            {"type": "preset", "title": "U.N.オーエンは彼女なのか？ (東方)", "data": UN_OWEN_MELODY},
        ]
        
        # 0. 楽譜から生成されたカノン（最優先曲）
        canon_path = os.path.join("songs", "canon.mid")
        if os.path.exists(canon_path):
            self.songs.insert(0, {"type": "midi", "title": "カノン (パッヘルベル) [Full MIDI]", "path": canon_path})

        # 1. カレントディレクトリの rydeen.mid があれば登録
        if os.path.exists("rydeen.mid"):
            self.songs.append({"type": "midi", "title": "ライディーン (Full MIDI)", "path": "rydeen.mid"})

        # 2. songs/ または downloaded_songs/ フォルダ内のMIDIファイルを追加
        for search_dir in ["songs", "downloaded_songs"]:
            if os.path.exists(search_dir):
                for f in glob.glob(os.path.join(search_dir, "*.mid")):
                    fname = os.path.basename(f)
                    if fname == "canon.mid":
                        continue  # 既に0番目に最優先登録済み
                    title = f"{os.path.splitext(fname)[0]} [MIDI]"
                    self.songs.append({"type": "midi", "title": title, "path": f})

        # 3. song_fetcher のオンライン人気曲（未キャッシュのものもリストに登録）
        for item in song_fetcher.FEATURED_ONLINE_SONGS:
            cached_path = song_fetcher.get_cached_path(item["local_filename"])
            if os.path.exists(cached_path):
                already = any(s.get("path") == cached_path for s in self.songs)
                if not already:
                    self.songs.append({"type": "midi", "title": f"{item['title']} [DL済]", "path": cached_path})
            else:
                self.songs.append({"type": "online", "title": f"{item['title']} [API取得]", "raw_info": item})

    def reload_songs(self):
        """外部追加などがあった場合にリストを再読み込み"""
        with self.lock:
            current_title = self.get_current_title()
            self._init_song_catalog()
            found = False
            for i, s in enumerate(self.songs):
                if s["title"] == current_title:
                    self.current_idx = i
                    found = True
                    break
            if not found and self.current_idx >= len(self.songs):
                self.current_idx = 0

    def next_song(self):
        with self.lock:
            if not self.songs:
                return
            self.current_idx = (self.current_idx + 1) % len(self.songs)
            self.song_changed = True
            self._all_notes_off()
            print(f"【自動演奏】次の曲へ切替: {self.get_current_title()} [{self.current_idx + 1}/{len(self.songs)}]")

    def prev_song(self):
        with self.lock:
            if not self.songs:
                return
            self.current_idx = (self.current_idx - 1) % len(self.songs)
            self.song_changed = True
            self._all_notes_off()
            print(f"【自動演奏】前の曲へ切替: {self.get_current_title()} [{self.current_idx + 1}/{len(self.songs)}]")

    def get_current_title(self):
        if not self.songs:
            return "No Songs"
        return self.songs[self.current_idx]["title"]

    def fetch_all_featured_online(self):
        """オンラインの東方Project曲を一括ダウンロードしてリストを即座に最新化"""
        print("【自動演奏】東方Project名曲MIDIの一括ダウンロードを開始します...")
        def _bg():
            for item in song_fetcher.FEATURED_ONLINE_SONGS:
                song_fetcher.download_song(item)
            self.reload_songs()
            print("【自動演奏】東方Project名曲MIDIのダウンロードと登録が完了しました！")
        t = threading.Thread(target=_bg, daemon=True)
        t.start()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._play_loop, daemon=True)
        self.thread.start()

    def _all_notes_off(self):
        for ch in range(16):
            self.outport.send(mido.Message('control_change', control=123, value=0, channel=ch))

    def pause(self):
        with self.lock:
            if not self.paused:
                self.paused = True
                self._all_notes_off()
                print("【自動演奏】一時停止（人が検知されました）")

    def resume(self):
        with self.lock:
            if self.paused:
                self.paused = False
                print(f"【自動演奏】再開: {self.get_current_title()}")

    def stop(self):
        self.running = False
        self.song_changed = True
        if self.thread:
            self.thread.join()

    def _play_loop(self):

        while self.running:
            if not self.songs:
                time.sleep(0.5)
                continue

            with self.lock:
                idx = self.current_idx
                song = self.songs[idx]
                self.song_changed = False

            # 未ダウンロードのオンライン曲の場合は、自動で即時取得
            if song["type"] == "online":
                print(f"【自動演奏】オンライン曲をダウンロード中...: {song['title']}")
                path = song_fetcher.download_song(song["raw_info"])
                if path:
                    song["type"] = "midi"
                    song["path"] = path
                    song["title"] = f"{song['raw_info']['title']} [DL済]"
                else:
                    time.sleep(1)
                    continue

            # 1. MIDIファイルの再生
            if song["type"] == "midi":
                mid_path = song.get("path")
                if os.path.exists(mid_path):
                    try:
                        mid = mido.MidiFile(mid_path)
                        print(f"【自動演奏】MIDI再生中: {song['title']}")
                        for msg in mid.play():
                            if not self.running or self.song_changed:
                                break
                            while self.paused and self.running and not self.song_changed:
                                time.sleep(0.1)
                            if self.song_changed:
                                break
                            if not self.paused:
                                self.outport.send(msg)
                    except Exception as e:
                        print(f"MIDI再生エラー ({song['title']}): {e}")
                        time.sleep(1)
                else:
                    time.sleep(0.5)

            # 2. 簡易メロディ（プリセット）の再生
            elif song["type"] == "preset":
                melody = song["data"]
                for note, duration in melody:
                    if not self.running or self.song_changed:
                        break

                    while self.paused and self.running and not self.song_changed:
                        time.sleep(0.1)

                    if self.song_changed:
                        break

                    if note > 0 and not self.paused:
                        # チャンネル3 (channel=2) を自動演奏で使用
                        self.outport.send(mido.Message('note_on', note=note, velocity=75, channel=2))

                    steps = max(1, int(duration / 0.05))
                    for _ in range(steps):
                        if not self.running or self.paused or self.song_changed:
                            break
                        time.sleep(0.05)

                    if note > 0:
                        self.outport.send(mido.Message('note_off', note=note, channel=2))

                    time.sleep(0.02)

            self._all_notes_off()
            time.sleep(0.3)

# ==========================================
# 1. AIモデルのセットアップ（Tasks API）
# ==========================================
base_options = python.BaseOptions(model_asset_path='hand_landmarker.task')
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=2,  # 検出する手の数
    min_hand_detection_confidence=0.7
)
detector = vision.HandLandmarker.create_from_options(options)

# ==========================================
# 2. MIDI出力・入力ポートを開く
# ==========================================
# 出力ポート (Vital用仮想MIDI)
try:
    outport = mido.open_output('Default Basic App Loopback 1')
    print("MIDI出力ポート 'Default Basic App Loopback 1' を開きました。")
except OSError:
    print("MIDI出力ポートが見つかりません。loopMIDIやWindows MIDI Servicesが有効か確認してください。")
    exit()

# 入力ポート (MIDIコントローラーからPythonへの入力 ＆ スルー転送用)
inport = None
input_ports = mido.get_input_names()
print("--- 利用可能なMIDI入力ポート ---")
for port in input_ports:
    print(f" - {port}")
print("--------------------------------")

for port in input_ports:
    # 仮想ループバックポートは除外し、物理コントローラーらしきものを自動選択
    if "Loopback" not in port and "loopMIDI" not in port:
        try:
            inport = mido.open_input(port)
            print(f"MIDI入力コントローラーとして '{port}' を開きました。（MIDIスルー有効）")
            break
        except OSError:
            pass

if inport is None:
    print("物理MIDIコントローラーは接続されていません（テルミン演奏機能のみで動作します）。")

# ==========================================
# 3. 自動演奏スレッドの始動
# ==========================================
auto_player = AutoPlayer(outport)
auto_player.start()

# ==========================================
# 4. カメラの準備とメインループ
# ==========================================
cap = cv2.VideoCapture(0)

# ウィンドウを事前に明示作成してマウスコールバックを設定
# WINDOW_NORMAL にしないとウィンドウに合わせて映像が拡大されない
cv2.namedWindow('Theremin Camera', cv2.WINDOW_NORMAL)
cv2.setMouseCallback('Theremin Camera', on_mouse)
SCREEN_SIZE = get_screen_size()
apply_window_mode('Theremin Camera', FULLSCREEN)

hand_states = {
    'Right': {
        'note': None,
        'notes': set(),
        'pinch_start_x': None,
        'pinch_start_y': None,
        'y_smooth': None,
        'channel': 0,
        'active': False
    },
    'Left': {
        'note': None,
        'notes': set(),
        'pinch_start_x': None,
        'pinch_start_y': None,
        'y_smooth': None,
        'channel': 1,
        'active': False
    }
}

last_hand_time = time.time()

print("カメラに向かって手をかざしてください。Escキーで終了します。")

try:
    while cap.isOpened():
        success, image = cap.read()
        if not success:
            break

        # 鏡のように反転させ、色をOpenCV標準(BGR)からRGBに変換
        image = cv2.flip(image, 1)
        h, w, _ = image.shape  # マウス処理でも使うので、ここで先に取得する
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # MediaPipe専用の画像フォーマットに変換してAIに入力
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
        detection_result = detector.detect(mp_image)

        current_detected_hands = []

        # ------------------------------------
        # マウスクリックアクションの処理
        # ------------------------------------
        if mouse_clicked:
            mouse_clicked = False  # イベントの消費

            if UI_STATE == 'PLAY':
                # 右上のSettingsボタンの当たり判定
                if is_in_rect(mouse_x, mouse_y, w - 140, 10, 130, 35):
                    UI_STATE = 'SETTINGS'
                    print("【画面切替】設定画面を開きました")

            elif UI_STATE == 'SETTINGS':
                # 戻るボタン (<- Back)
                if is_in_rect(mouse_x, mouse_y, 10, 10, 120, 35):
                    UI_STATE = 'PLAY'
                    print("【画面切替】演奏画面に戻りました")

                # スケール選択ボタン (左列 1〜3)
                elif is_in_rect(mouse_x, mouse_y, 40, 80, 250, 40):
                    CURRENT_KEY = 'C_major'
                    print("【切替】ハ長調 (C Major) になりました")
                elif is_in_rect(mouse_x, mouse_y, 40, 135, 250, 40):
                    CURRENT_KEY = 'A_minor'
                    print("【切替】イ短調 (A Minor) になりました")
                elif is_in_rect(mouse_x, mouse_y, 40, 190, 250, 40):
                    CURRENT_KEY = 'G_major'
                    print("【切替】ト長調 (G Major) になりました")

                # スケール選択ボタン (右列 4〜6)
                elif is_in_rect(mouse_x, mouse_y, 320, 80, 280, 40):
                    CURRENT_KEY = 'A_pentatonic_minor'
                    print("【切替】ペンタトニック・マイナー (A Pentatonic Minor) になりました")
                elif is_in_rect(mouse_x, mouse_y, 320, 135, 280, 40):
                    CURRENT_KEY = 'Ryukyu'
                    print("【切替】琉球音階 (Ryukyu) になりました")
                elif is_in_rect(mouse_x, mouse_y, 320, 190, 280, 40):
                    CURRENT_KEY = 'Miyakobushi'
                    print("【切替】都節音階 (Miyakobushi) になりました")

                # モードトグルボタン
                elif is_in_rect(mouse_x, mouse_y, 40, 260, 160, 45):
                    WAVE_MODE = not WAVE_MODE
                    print(f"【切替】波形モードが {'ON' if WAVE_MODE else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 220, 260, 160, 45):
                    STRUM_MODE = not STRUM_MODE
                    print(f"【切替】和音モードが {'ON' if STRUM_MODE else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 400, 260, 160, 45):
                    AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                    print(f"【切替】自動演奏機能が {'ON' if AUTO_PLAY_ENABLED else 'OFF'} になりました")
                    if not AUTO_PLAY_ENABLED:
                        auto_player.pause()

                # 追加トグルボタン（中段）
                elif is_in_rect(mouse_x, mouse_y, 40, 310, 250, 40):
                    SHOW_DEBUG = not SHOW_DEBUG
                    print(f"【切替】デバッグ表示が {'ON' if SHOW_DEBUG else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 310, 310, 250, 40):
                    RAINBOW_LANES = not RAINBOW_LANES
                    print(f"【切替】虹色レーンが {'ON' if RAINBOW_LANES else 'OFF'} になりました")

                # 自動演奏曲選択 & API取得ボタン（下段）
                elif is_in_rect(mouse_x, mouse_y, 40, 385, 120, 40):
                    auto_player.prev_song()
                elif is_in_rect(mouse_x, mouse_y, 180, 385, 120, 40):
                    auto_player.next_song()
                elif is_in_rect(mouse_x, mouse_y, 320, 385, 260, 40):
                    auto_player.fetch_all_featured_online()


        # ------------------------------------
        # MIDIコントローラーからの入力をVitalへスルー転送
        # ------------------------------------
        if inport is not None:
            for msg in inport.iter_pending():
                outport.send(msg)

        # ------------------------------------
        # 手の位置から音程を計算してMIDI送信
        # ------------------------------------
        if detection_result.hand_landmarks and detection_result.handedness:
            for hand_landmarks, handedness in zip(detection_result.hand_landmarks, detection_result.handedness):
                hand_type = handedness[0].category_name
                current_detected_hands.append(hand_type)
                state = hand_states[hand_type]
                state['active'] = True

                features = extract_hand_features(hand_landmarks)
                y = features['index_y']
                y = max(Y_MIN_LIMIT, min(Y_MAX_LIMIT, y))
                y_scaled = (y - Y_MIN_LIMIT) / (Y_MAX_LIMIT - Y_MIN_LIMIT)

                # 指の震え（チャタリング）を抑えるための指数移動平均（EMA）フィルター
                EMA_ALPHA = 0.20  # 小さいほど滑らかになりますが、応答遅延がわずかに増えます
                if state['y_smooth'] is None:
                    state['y_smooth'] = y_scaled
                else:
                    state['y_smooth'] = EMA_ALPHA * y_scaled + (1.0 - EMA_ALPHA) * state['y_smooth']

                y_scaled_final = state['y_smooth']

                if CHORD_MODE:
                    ALLOWED_NOTES = KEY_PRESETS[CURRENT_KEY]
                    index = int((1.0 - y_scaled_final) * len(ALLOWED_NOTES))
                    index = max(0, min(len(ALLOWED_NOTES) - 1, index))
                    note = ALLOWED_NOTES[index]
                else:
                    note = int((1.0 - y_scaled_final) * 24) + 60

                note = max(0, min(127, note))

                # 【機能追加】左手の場合は1オクターブ下げるロジック
                if hand_type == 'Left':
                    note = max(0, note - 12)

                cc_value = int(features['pinch'] * 127)
                cc_value = max(0, min(127, cc_value))

                x_cc_value = int(features['index_x'] * 127)
                x_cc_value = max(0, min(127, x_cc_value))

                outport.send(mido.Message('control_change', control=7, value=cc_value, channel=state['channel']))

                # WAVE_MODEがTrueのときのみX座標（CC#16）を送信
                if WAVE_MODE:
                    outport.send(mido.Message('control_change', control=16, value=x_cc_value, channel=state['channel']))

                # つまんでいるかどうかの判定
                is_pinching = features['pinch'] >= PINCH_THRESHOLD

                if STRUM_MODE:
                    # ------------------------------------
                    # 【和音モード】ひも引っ張り発音
                    # ------------------------------------
                    if is_pinching:
                        # つまみ開始座標の記録（正規化座標）
                        if state['pinch_start_y'] is None:
                            state['pinch_start_x'] = features['index_x']
                            state['pinch_start_y'] = features['index_y']

                        # 開始点と現在地のY軸インデックスを計算
                        y_start = max(Y_MIN_LIMIT, min(Y_MAX_LIMIT, state['pinch_start_y']))
                        y_start_scaled = (y_start - Y_MIN_LIMIT) / (Y_MAX_LIMIT - Y_MIN_LIMIT)

                        if CHORD_MODE:
                            ALLOWED_NOTES = KEY_PRESETS[CURRENT_KEY]
                            idx_start = int((1.0 - y_start_scaled) * len(ALLOWED_NOTES))
                            idx_start = max(0, min(len(ALLOWED_NOTES) - 1, idx_start))

                            idx_current = int((1.0 - y_scaled_final) * len(ALLOWED_NOTES))
                            idx_current = max(0, min(len(ALLOWED_NOTES) - 1, idx_current))

                            # 開始インデックスから現在インデックスまでの範囲
                            step = 1 if idx_start <= idx_current else -1
                            target_notes = set()
                            for i in range(idx_start, idx_current + step, step):
                                n = ALLOWED_NOTES[i]
                                if hand_type == 'Left':
                                    n = max(0, n - 12)
                                target_notes.add(n)
                        else:
                            # クロマチック（半音階）の場合の範囲
                            note_start = int((1.0 - y_start_scaled) * 24) + 60
                            note_start = max(0, min(127, note_start))
                            if hand_type == 'Left':
                                note_start = max(0, note_start - 12)

                            step = 1 if note_start <= note else -1
                            target_notes = set(range(note_start, note + step, step))

                        # 同時発音数に応じたベロシティの自動減衰（クリッピング防止）
                        num_notes = len(target_notes)
                        if num_notes > 0:
                            velocity = int(100 / math.sqrt(num_notes))
                            velocity = max(40, min(100, velocity))
                        else:
                            velocity = 100

                        # 新しく発音すべきノートを note_on
                        for n in target_notes:
                            if n not in state['notes']:
                                outport.send(mido.Message('note_on', note=n, velocity=velocity, channel=state['channel']))
                                print(f"【MIDI送信】[和音] Note ON: {n} ({midi_to_note_name(n)}) - ベロシティ: {velocity}")

                        # 範囲から外れたノートを note_off
                        for n in list(state['notes']):
                            if n not in target_notes:
                                outport.send(mido.Message('note_off', note=n, channel=state['channel']))
                                print(f"【MIDI送信】[和音] Note OFF: {n} ({midi_to_note_name(n)})")

                        # 発音中ノートリストを更新
                        state['notes'] = target_notes

                    else:
                        # 指を離した場合は全消音
                        if state['notes']:
                            for n in state['notes']:
                                outport.send(mido.Message('note_off', note=n, channel=state['channel']))
                                print(f"【MIDI送信】[和音全消音] Note OFF: {n} ({midi_to_note_name(n)})")
                            state['notes'].clear()
                        state['pinch_start_x'] = None
                        state['pinch_start_y'] = None

                else:
                    # ------------------------------------
                    # 【通常モード】単音レガート発音
                    # ------------------------------------
                    if state['notes']:
                        for n in state['notes']:
                            outport.send(mido.Message('note_off', note=n, channel=state['channel']))
                        state['notes'].clear()
                    state['pinch_start_x'] = None
                    state['pinch_start_y'] = None

                    if is_pinching:
                        if state['note'] is None:
                            outport.send(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                            state['note'] = note
                        elif state['note'] != note:
                            old_note = state['note']
                            # 先に新しい音を鳴らしてから古い音を止めることで、シンセのレガート・ポルタメント（滑らかなスライド）を有効にします
                            outport.send(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                            outport.send(mido.Message('note_off', note=old_note, channel=state['channel']))
                            state['note'] = note
                    else:
                        if state['note'] is not None:
                            outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
                            state['note'] = None

        # ------------------------------------
        # 手の検出有無による自動演奏スレッドの制御
        # ------------------------------------
        if len(current_detected_hands) > 0:
            auto_player.pause()
            last_hand_time = time.time()
        else:
            # AUTO_PLAY_ENABLED が True のときのみ、手がない状態が1.5秒続いたら自動演奏を開始
            if AUTO_PLAY_ENABLED and (time.time() - last_hand_time > 1.5):
                auto_player.resume()

        # 画面から消えた手の後処理
        for hand_type in ['Right', 'Left']:
            if hand_type not in current_detected_hands:
                state = hand_states[hand_type]
                if state['active']:
                    if state['note'] is not None:
                        outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
                        state['note'] = None
                    if state['notes']:
                        for n in state['notes']:
                            outport.send(mido.Message('note_off', note=n, channel=state['channel']))
                        state['notes'].clear()
                    state['pinch_start_x'] = None
                    state['pinch_start_y'] = None
                    state['y_smooth'] = None  # 手が消えたらスムージングを初期化
                    state['active'] = False

        # ==========================================
        # 5. カメラ映像の表示 (HUD)
        # ==========================================
        if UI_STATE == 'PLAY':
            # ------------------------------------
            # 演奏画面 (PLAY)
            # ------------------------------------
            notes_in_scale = KEY_PRESETS[CURRENT_KEY] if CHORD_MODE else [60 + i for i in range(24)]
            N = len(notes_in_scale)

            # 各レーンの上下ピクセル位置を事前計算
            lane_bounds = []
            for i in range(N):
                y_scaled_top = (N - 1 - i) / N
                y_scaled_bottom = (N - i) / N
                y_top = Y_MIN_LIMIT + y_scaled_top * (Y_MAX_LIMIT - Y_MIN_LIMIT)
                y_bottom = Y_MIN_LIMIT + y_scaled_bottom * (Y_MAX_LIMIT - Y_MIN_LIMIT)
                lane_bounds.append((int(h * y_top), int(h * y_bottom)))

            # ------------------------------------
            # 虹色レーン帯（ゲーミング）
            # ------------------------------------
            if RAINBOW_LANES:
                # 現在発音中の音（左手は1オクターブ下げて鳴らしているので+12して元のレーンに合わせる）
                active_notes = set()
                for ht in ['Right', 'Left']:
                    st = hand_states[ht]
                    shift = 12 if ht == 'Left' else 0
                    if STRUM_MODE:
                        for n in st['notes']:
                            active_notes.add(n + shift)
                    elif st['note'] is not None:
                        active_notes.add(st['note'] + shift)

                # 時間とともに虹が流れるオフセット
                hue_offset = (time.time() * RAINBOW_SPEED) % 1.0

                base_overlay = image.copy()
                active_overlay = image.copy()
                has_active = False

                for i in range(N):
                    top_px, bottom_px = lane_bounds[i]
                    # 低い音(下)から高い音(上)へ、赤→紫のグラデーション
                    pos = (N - 1 - i) / max(1, N - 1)
                    color = rainbow_bgr(pos * 0.85, hue_offset)

                    cv2.rectangle(base_overlay, (0, top_px), (w, bottom_px), color, -1)

                    if notes_in_scale[i] in active_notes:
                        cv2.rectangle(active_overlay, (0, top_px), (w, bottom_px), color, -1)
                        has_active = True

                cv2.addWeighted(base_overlay, RAINBOW_ALPHA, image, 1.0 - RAINBOW_ALPHA, 0, image)
                if has_active:
                    cv2.addWeighted(active_overlay, RAINBOW_ACTIVE_ALPHA, image, 1.0 - RAINBOW_ACTIVE_ALPHA, 0, image)

            # 音程の境界線（薄いガイドライン）と音名表示の描画
            for i in range(N):
                top_px, bottom_px = lane_bounds[i]

                # レーンの境界線を描画（最上部/最下部を除く内側の線）
                if i < N - 1:
                    line_color = (255, 255, 255) if RAINBOW_LANES else (55, 55, 55)
                    cv2.line(image, (0, top_px), (w, top_px), line_color, 1)

                # レーンの中央に音名（C4, D4等）を表示
                y_center_px = (top_px + bottom_px) // 2
                note_name = midi_to_note_name(notes_in_scale[i])
                name_color = (255, 255, 255) if RAINBOW_LANES else (110, 110, 110)
                cv2.putText(image, note_name, (15, y_center_px + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, name_color, 1)

            # 自動演奏曲ステータス表示
            cur_song_title = auto_player.get_current_title()
            # レーンの音名(x=15)と重ならないよう少し右から描画し、背景帯を敷く
            put_text_jp(image, f"AutoSong: {cur_song_title}  [N:Next / P:Prev / T:Fetch]", (50, 103), 15, (0, 255, 255), bg_color=(30, 30, 30))

            # 右上にSettingsボタンを描画
            draw_button(image, "Settings [Tab]", w - 140, 10, 130, 35, False, active_color=(0, 255, 0))

            # ------------------------------------
            # デバッグ表示（SHOW_DEBUG が True のときのみ）
            # ------------------------------------
            if SHOW_DEBUG:
                # 有効演奏エリアの最上部・最下部の境界線
                cv2.line(image, (0, int(h * Y_MIN_LIMIT)), (w, int(h * Y_MIN_LIMIT)), (0, 0, 255), 2)
                cv2.line(image, (0, int(h * Y_MAX_LIMIT)), (w, int(h * Y_MAX_LIMIT)), (0, 0, 255), 2)

                # 左上に1行でステータスを集約表示
                wave_txt = "WAVE:ON" if WAVE_MODE else "WAVE:OFF"
                strum_txt = "STRUM:ON" if STRUM_MODE else "STRUM:OFF"
                auto_txt = "AUTO:ON" if AUTO_PLAY_ENABLED else "AUTO:OFF"
                status_line = f"[{CURRENT_KEY}] | {wave_txt} | {strum_txt} | {auto_txt}"
                cv2.putText(image, status_line, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

                r_note = hand_states['Right']['note']
                l_note = hand_states['Left']['note']
                cv2.putText(image, f"R-Note: {midi_to_note_name(r_note)}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                cv2.putText(image, f"L-Note: {midi_to_note_name(l_note)}", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 2)

        else:
            # ------------------------------------
            # 設定画面 (SETTINGS)
            # ------------------------------------
            # 半透明の暗い背景オーバーレイ
            overlay = image.copy()
            cv2.rectangle(overlay, (0, 0), (w, h), (25, 25, 25), -1)
            cv2.addWeighted(overlay, 0.85, image, 0.15, 0, image)

            # 左上に ◀ Back ボタン
            draw_button(image, "Back [Tab]", 10, 10, 120, 35, False, active_color=(0, 255, 0))

            # 中央上にタイトル
            cv2.putText(image, "tElemin Settings", (220, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # スケール選択ボタン (左列 1〜3)
            draw_button(image, "1. C Major", 40, 80, 250, 40, CURRENT_KEY == 'C_major')
            draw_button(image, "2. A Minor", 40, 135, 250, 40, CURRENT_KEY == 'A_minor')
            draw_button(image, "3. G Major", 40, 190, 250, 40, CURRENT_KEY == 'G_major')

            # スケール選択ボタン (右列 4〜6)
            draw_button(image, "4. Pentatonic Minor", 320, 80, 280, 40, CURRENT_KEY == 'A_pentatonic_minor')
            draw_button(image, "5. Ryukyu Scale", 320, 135, 280, 40, CURRENT_KEY == 'Ryukyu')
            draw_button(image, "6. Miyakobushi Scale", 320, 190, 280, 40, CURRENT_KEY == 'Miyakobushi')

            # モード切替トグルボタン (上段)
            draw_button(image, f"WAVE: {'ON' if WAVE_MODE else 'OFF'}", 40, 250, 160, 45, WAVE_MODE)
            draw_button(image, f"STRUM: {'ON' if STRUM_MODE else 'OFF'}", 220, 250, 160, 45, STRUM_MODE)
            draw_button(image, f"AUTO: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}", 400, 250, 160, 45, AUTO_PLAY_ENABLED)

            # 追加トグルボタン (中段)
            draw_button(image, f"DEBUG [d]: {'ON' if SHOW_DEBUG else 'OFF'}", 40, 310, 250, 40, SHOW_DEBUG)
            draw_button(image, f"RAINBOW [r]: {'ON' if RAINBOW_LANES else 'OFF'}", 310, 310, 250, 40, RAINBOW_LANES)

            # 自動演奏ソング選択コントロール (下段)
            cur_song_title = auto_player.get_current_title()
            put_text_jp(image, f"AutoSong: {cur_song_title}", (40, 358), 16, (255, 255, 255))
            draw_button(image, "< Prev [P]", 40, 385, 120, 40, False, active_color=(0, 255, 255))
            draw_button(image, "Next [N] >", 180, 385, 120, 40, False, active_color=(0, 255, 255))
            draw_button(image, "Fetch Touhou [T]", 320, 385, 260, 40, False, active_color=(255, 200, 0))

        # ------------------------------------
        # 手の描画処理（演奏画面のときのみ表示）
        # ------------------------------------
        if UI_STATE == 'PLAY' and detection_result.hand_landmarks and detection_result.handedness:
            for hand_landmarks, handedness in zip(detection_result.hand_landmarks, detection_result.handedness):
                hand_type = handedness[0].category_name
                state = hand_states[hand_type]

                # 親指(4番)の画面上のピクセル座標を計算
                idx_x = int(hand_landmarks[4].x * w)
                idx_y = int(hand_landmarks[4].y * h)

                features = extract_hand_features(hand_landmarks)
                pinch_pct = int(features['pinch'] * 100)

                # 和音モード中の「ひも（直線）」描画処理（演奏の見た目なので常に表示）
                if STRUM_MODE and state['pinch_start_x'] is not None and state['pinch_start_y'] is not None:
                    start_px_x = int(state['pinch_start_x'] * w)
                    start_px_y = int(state['pinch_start_y'] * h)

                    # ひもをシアン (255, 255, 0) で描画
                    cv2.line(image, (start_px_x, start_px_y), (idx_x, idx_y), (255, 255, 0), 3)
                    # つまみ開始アンカー
                    anchor_color = (0, 0, 255) if SHOW_DEBUG else (255, 255, 255)
                    cv2.circle(image, (start_px_x, start_px_y), 8, anchor_color, -1)

                if STRUM_MODE:
                    is_active_sound = bool(state['notes'])
                else:
                    is_active_sound = state['note'] is not None

                if SHOW_DEBUG:
                    # デバッグ時：発音中は緑、無音は赤の円（当たり判定の確認用）
                    color = (0, 255, 0) if is_active_sound else (0, 0, 255)
                    cv2.circle(image, (idx_x, idx_y), 15, color, -1)
                    cv2.putText(image, f"Pinch:{pinch_pct}%", (idx_x + 20, idx_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

                    if STRUM_MODE:
                        if state['notes']:
                            sorted_notes = sorted(list(state['notes']))
                            notes_str = ", ".join([midi_to_note_name(n) for n in sorted_notes])
                            cv2.putText(image, f"NOTES: {notes_str}", (idx_x + 20, idx_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                    else:
                        if state['note'] is not None:
                            note_name = midi_to_note_name(state['note'])
                            cv2.putText(image, f"NOTE:{state['note']} ({note_name})", (idx_x + 20, idx_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                else:
                    # 通常時：指先の位置だけを示すシンプルなマーカー（発音中は塗りつぶし）
                    if is_active_sound:
                        cv2.circle(image, (idx_x, idx_y), 14, (255, 255, 255), -1)
                        cv2.circle(image, (idx_x, idx_y), 14, (0, 0, 0), 2)
                    else:
                        cv2.circle(image, (idx_x, idx_y), 14, (255, 255, 255), 2)

        if FULLSCREEN:
            cv2.imshow('Theremin Camera', letterbox_to_screen(image, SCREEN_SIZE))
        else:
            display_offset_x, display_offset_y = 0, 0
            cv2.imshow('Theremin Camera', image)

        key = cv2.waitKey(5)
        if key != -1:
            key = key & 0xFF
            if key == 27:
                break
            elif key == ord('1'):
                CURRENT_KEY = 'C_major'
                print("【切替】ハ長調 (C Major) になりました")
            elif key == ord('2'):
                CURRENT_KEY = 'A_minor'
                print("【切替】イ短調 (A Minor) になりました")
            elif key == ord('3'):
                CURRENT_KEY = 'G_major'
                print("【切替】ト長調 (G Major) になりました")
            elif key == ord('4'):
                CURRENT_KEY = 'A_pentatonic_minor'
                print("【切替】ペンタトニック・マイナー (A Pentatonic Minor) になりました")
            elif key == ord('5'):
                CURRENT_KEY = 'Ryukyu'
                print("【切替】琉球音階 (Ryukyu) になりました")
            elif key == ord('6'):
                CURRENT_KEY = 'Miyakobushi'
                print("【切替】都節音階 (Miyakobushi) になりました")
            elif key == ord('7'):
                CURRENT_KEY = 'D_major'
                print("【切替】ニ長調 / カノンの調 (D Major) になりました")
            elif key == ord('w'):
                WAVE_MODE = not WAVE_MODE
                print(f"【切替】波形モードが {'ON' if WAVE_MODE else 'OFF'} になりました")
            elif key == ord('h'):
                STRUM_MODE = not STRUM_MODE
                print(f"【切替】和音モードが {'ON' if STRUM_MODE else 'OFF'} になりました")
            elif key == ord('m'):
                AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                print(f"【切替】自動演奏機能が {'ON' if AUTO_PLAY_ENABLED else 'OFF'} になりました")
                if not AUTO_PLAY_ENABLED:
                    auto_player.pause()
            elif key == ord('d'):
                SHOW_DEBUG = not SHOW_DEBUG
                print(f"【切替】デバッグ表示が {'ON' if SHOW_DEBUG else 'OFF'} になりました")
            elif key == ord('r'):
                RAINBOW_LANES = not RAINBOW_LANES
                print(f"【切替】虹色レーンが {'ON' if RAINBOW_LANES else 'OFF'} になりました")
            elif key == ord('n') or key == ord('['):
                auto_player.next_song()
            elif key == ord('p') or key == ord(']'):
                auto_player.prev_song()
            elif key == ord('t'):
                auto_player.fetch_all_featured_online()
            elif key == ord('f'):
                FULLSCREEN = not FULLSCREEN
                apply_window_mode('Theremin Camera', FULLSCREEN)
                print(f"【切替】全画面表示が {'ON' if FULLSCREEN else 'OFF'} になりました")

            elif key == 9:  # Tabキーのキーコード (9)
                UI_STATE = 'SETTINGS' if UI_STATE == 'PLAY' else 'PLAY'
                print(f"【画面切替】画面を {UI_STATE} モードに切り替えました")

except KeyboardInterrupt:
    pass

finally:
    # 終了処理
    print("終了処理を実行しています...")
    cap.release()
    cv2.destroyAllWindows()

    # 自動演奏スレッドの完全停止
    auto_player.stop()

    # 音の消音処理
    for hand_type in ['Right', 'Left']:
        state = hand_states[hand_type]
        if state['note'] is not None:
            outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
        if state['notes']:
            for n in state['notes']:
                outport.send(mido.Message('note_off', note=n, channel=state['channel']))

    # 全チャンネル消音信号送信
    for ch in range(16):
        outport.send(mido.Message('control_change', control=123, value=0, channel=ch))

    outport.close()
    if inport is not None:
        inport.close()
    detector.close()
    print("終了しました。")
