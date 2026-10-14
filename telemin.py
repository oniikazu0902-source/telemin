# ------------------------------------------------------------------
# tElemin - AIテルミン演奏システム
#   1. 全画面追従表示: WINDOW_NORMAL で起動し、画面やウィンドウ解像度に合わせて
#      映像表示域・演奏レーンが黒帯なしで自動追従（'f'キーで全画面/通常切替）。
#   2. 演奏境界制御: Y_MIN_LIMIT / Y_MAX_LIMIT の外側では発音・反応しない安全設計。
#   3. 高音質MIDI自動演奏: songs/ ディレクトリを動的監視し、delta-time制御・ポリフォニー
#      リミッター・チャンネル最適化により音の濁りや再開時バーストを完全に防止。
#   4. トースト表示: 演奏画面では曲切り替え時のみ数秒間表示し、通常演奏画面をクリアに維持。
#   5. ピンチしきい値調整: キーボード [-] / [+] や設定画面(Tab)から直感的に感度調整可能。
#   6. 日本語表示: put_text_jp() によりPillow+Windows標準フォントで文字化けなく描画。
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
PINCH_THRESHOLD = 0.70  # つまみ判定のしきい値（0.20〜0.95、キーボード[-] / [+]や設定画面で調整可能）

# 波形変化モードのデフォルト状態
WAVE_MODE = False

# 和音モード（ひも引っ張り発音）のデフォルト状態（デフォルトOFF）
STRUM_MODE = False

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

def on_mouse(event, x, y, flags, param):
    global mouse_x, mouse_y, mouse_clicked
    if event == cv2.EVENT_LBUTTONDOWN:
        mouse_x, mouse_y = x, y
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

def adapt_frame_to_ratio(frame, target_ratio, base_height=480):
    """画面やウィンドウのアスペクト比に合わせて、カメラ映像を歪みなく中央クロップ＆リサイズして表示域を追従させる"""
    fh, fw = frame.shape[:2]
    current_ratio = fw / max(1, fh)

    if current_ratio > target_ratio:
        # 横が広すぎる場合、左右を均等にクロップ
        new_w = int(round(fh * target_ratio))
        start_x = max(0, (fw - new_w) // 2)
        cropped = frame[:, start_x:start_x + new_w]
    elif current_ratio < target_ratio:
        # 縦が長すぎる場合、上下を均等にクロップ
        new_h = int(round(fw / target_ratio))
        start_y = max(0, (fh - new_h) // 2)
        cropped = frame[start_y:start_y + new_h, :]
    else:
        cropped = frame

    target_w = max(640, int(round(base_height * target_ratio)))
    if cropped.shape[1] != target_w or cropped.shape[0] != base_height:
        return cv2.resize(cropped, (target_w, base_height), interpolation=cv2.INTER_LINEAR)
    return cropped

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
# 自動演奏用クラス (MIDIインポート再生専用)
# ==========================================
MIDI_DIR = "songs"

class AutoPlayer:
    def __init__(self, outport, midi_dir=MIDI_DIR):
        self.outport = outport
        self.midi_dir = midi_dir
        self.running = False
        self.paused = True
        self.thread = None
        self.lock = threading.Lock()
        self.current_idx = 0
        self.song_changed = False
        self.songs = []
        self._last_scan_files = []
        self.title_changed_at = time.time()

        # songs フォルダがなければ自動作成
        if not os.path.exists(self.midi_dir):
            os.makedirs(self.midi_dir, exist_ok=True)

        self.scan_songs()

    def scan_songs(self):
        """songs/ フォルダおよびカレント内のMIDIファイルをスキャンして自動登録・更新"""
        with self.lock:
            midi_files = []
            seen_paths = set()

            def add_file(f):
                norm = os.path.normcase(os.path.abspath(f))
                if norm not in seen_paths and os.path.isfile(f):
                    seen_paths.add(norm)
                    midi_files.append(f)

            # 1. songs/ フォルダ内のMIDIファイルを収集
            if os.path.exists(self.midi_dir):
                for ext in ["*.mid", "*.midi", "*.MID", "*.MIDI"]:
                    for f in glob.glob(os.path.join(self.midi_dir, ext)):
                        add_file(f)

            # 2. カレントディレクトリのMIDIファイルも収集
            for ext in ["*.mid", "*.midi", "*.MID", "*.MIDI"]:
                for f in glob.glob(ext):
                    add_file(f)

            # ファイル名順（canonがあれば最優先）でソート
            def sort_key(f):
                base = os.path.basename(f).lower()
                return (0 if "canon" in base else 1, base)

            midi_files.sort(key=sort_key)

            # ファイル構成に変化がなければスキップ
            current_file_keys = [os.path.normcase(os.path.abspath(f)) for f in midi_files]
            if current_file_keys == self._last_scan_files and self.songs:
                return False

            self._last_scan_files = list(current_file_keys)

            current_norm = os.path.normcase(os.path.abspath(self.songs[self.current_idx]["path"])) if (self.songs and self.current_idx < len(self.songs)) else None

            new_songs = []
            for f in midi_files:
                fname = os.path.basename(f)
                title = os.path.splitext(fname)[0]
                new_songs.append({
                    "title": title,
                    "path": f
                })

            self.songs = new_songs

            # 以前選択していた曲の位置を維持
            found = False
            if current_norm:
                for i, s in enumerate(self.songs):
                    if os.path.normcase(os.path.abspath(s["path"])) == current_norm:
                        self.current_idx = i
                        found = True
                        break
            if not found:
                self.current_idx = 0

            self.title_changed_at = time.time()
            if self.songs:
                print(f"【MIDIカタログ更新】{len(self.songs)}曲のMIDIファイルを認識しました。")
            return True

    def next_song(self):
        self.scan_songs()  # 切り替え時に最新フォルダ状況を反映
        with self.lock:
            if not self.songs:
                return
            self.current_idx = (self.current_idx + 1) % len(self.songs)
            self.song_changed = True
            self.title_changed_at = time.time()
            self._all_notes_off()
            print(f"【自動演奏】次のMIDIへ切替: {self.get_current_title()}")

    def prev_song(self):
        self.scan_songs()  # 切り替え時に最新フォルダ状況を反映
        with self.lock:
            if not self.songs:
                return
            self.current_idx = (self.current_idx - 1) % len(self.songs)
            self.song_changed = True
            self.title_changed_at = time.time()
            self._all_notes_off()
            print(f"【自動演奏】前のMIDIへ切替: {self.get_current_title()}")

    def get_current_title(self):
        with self.lock:
            if not self.songs:
                return "No MIDI files in 'songs/'"
            return f"{self.songs[self.current_idx]['title']} [{self.current_idx + 1}/{len(self.songs)}]"

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
        self.scan_songs()  # 再開前にも最新の追加MIDIを自動反映
        with self.lock:
            if self.paused:
                self.paused = False
                self.title_changed_at = time.time()
                # チャンネルの音量CC#7を100に確実にリセット
                for ch in range(16):
                    self.outport.send(mido.Message('control_change', control=7, value=100, channel=ch))
                print(f"【自動演奏】再開: {self.get_current_title()}")

    def stop(self):
        self.running = False
        self.song_changed = True
        if self.thread:
            self.thread.join()

    def _play_loop(self):
        last_check_time = 0
        while self.running:
            # 3秒おきにディレクトリを自動スキャンして新規追加MIDIを動的検知
            now = time.time()
            if now - last_check_time > 3.0:
                self.scan_songs()
                last_check_time = now

            with self.lock:
                if not self.songs:
                    time.sleep(0.5)
                    continue
                idx = self.current_idx
                song = self.songs[idx]
                self.song_changed = False

            mid_path = song.get("path")
            if os.path.exists(mid_path):
                try:
                    mid = mido.MidiFile(mid_path)
                    print(f"【自動演奏】MIDI再生開始: {song['title']}")

                    # 再生開始時に音量を健全な値（100）にリセット
                    for ch in range(16):
                        self.outport.send(mido.Message('control_change', control=7, value=100, channel=ch))

                    # 発音中ノートを追跡（ポリフォニー制限＆音の濁り防止）
                    active_notes = {}  # (ch, note) -> timestamp
                    MAX_POLYPHONY = 10
                    song_completed_naturally = True

                    for msg in mid:
                        if not self.running or self.song_changed:
                            song_completed_naturally = False
                            break

                        # デルタタイム待機（一時停止中は実時間を減算しないため、再開時のノート爆発バーストを防ぐ）
                        dt = msg.time
                        while dt > 0 and self.running and not self.song_changed:
                            if self.paused:
                                time.sleep(0.05)
                                continue
                            step = min(dt, 0.02)
                            time.sleep(step)
                            dt -= step

                        if not self.running or self.song_changed:
                            song_completed_naturally = False
                            break

                        while self.paused and self.running and not self.song_changed:
                            time.sleep(0.05)

                        if not self.running or self.song_changed:
                            song_completed_naturally = False
                            break

                        # 1. ドラムチャンネル (MIDI ch 10 = index 9) はピッチシンセに不適なため除外
                        if hasattr(msg, 'channel') and msg.channel == 9:
                            continue

                        # 2. note_on / note_off のみ厳選送信（外部MIDIのCCやプログラムチェンジによるシンセ誤動作を防止）
                        if msg.type == 'note_on' and msg.velocity > 0:
                            key = (msg.channel, msg.note)
                            # 同音連打時は先に消音
                            if key in active_notes:
                                self.outport.send(mido.Message('note_off', note=msg.note, channel=msg.channel))
                                del active_notes[key]

                            # ポリフォニー上限チェック（音が濁るのを防ぐため最も古い音を消音）
                            if len(active_notes) >= MAX_POLYPHONY:
                                oldest_key = min(active_notes, key=active_notes.get)
                                self.outport.send(mido.Message('note_off', note=oldest_key[1], channel=oldest_key[0]))
                                del active_notes[oldest_key]

                            # ベロシティを適切な音量帯(55〜110)にノーマライズ
                            vel = max(55, min(110, msg.velocity))
                            self.outport.send(mido.Message('note_on', note=msg.note, velocity=vel, channel=msg.channel))
                            active_notes[key] = time.time()

                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            key = (msg.channel, msg.note)
                            self.outport.send(mido.Message('note_off', note=msg.note, channel=msg.channel))
                            active_notes.pop(key, None)

                    # 曲が自然終了した場合は次の曲へ自動で進む
                    if song_completed_naturally and self.running and not self.song_changed:
                        with self.lock:
                            if self.songs:
                                self.current_idx = (self.current_idx + 1) % len(self.songs)
                                self.title_changed_at = time.time()
                                print(f"【自動演奏】次の曲へ自動進行: {self.get_current_title()}")

                except Exception as e:
                    print(f"MIDI再生エラー ({song['title']}): {e}")
                    time.sleep(1)
            else:
                time.sleep(0.5)

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
# 広角・高画質対応（可能であれば1280x720を要求）
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

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
        'active': False,
        'pinch': 0.0
    },
    'Left': {
        'note': None,
        'notes': set(),
        'pinch_start_x': None,
        'pinch_start_y': None,
        'y_smooth': None,
        'channel': 1,
        'active': False,
        'pinch': 0.0
    }
}

last_hand_time = time.time()

print("カメラに向かって手をかざしてください。Escキーで終了します。")

try:
    while cap.isOpened():
        success, image = cap.read()
        if not success:
            break

        # 鏡のように反転
        image = cv2.flip(image, 1)

        # 全画面またはウィンドウ解像度に合わせて映像表示域を自動追従（黒帯なし）
        if FULLSCREEN and SCREEN_SIZE is not None:
            target_ratio = SCREEN_SIZE[0] / max(1, SCREEN_SIZE[1])
        else:
            win_rect = cv2.getWindowImageRect('Theremin Camera')
            if win_rect[2] > 0 and win_rect[3] > 0:
                target_ratio = win_rect[2] / win_rect[3]
            elif SCREEN_SIZE is not None:
                target_ratio = SCREEN_SIZE[0] / max(1, SCREEN_SIZE[1])
            else:
                target_ratio = 16.0 / 9.0

        image = adapt_frame_to_ratio(image, target_ratio, base_height=480)
        h, w, _ = image.shape
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

                # 自動演奏曲選択 & ディレクトリ再スキャンボタン（下段）
                elif is_in_rect(mouse_x, mouse_y, 40, 385, 120, 40):
                    auto_player.prev_song()
                elif is_in_rect(mouse_x, mouse_y, 180, 385, 120, 40):
                    auto_player.next_song()
                elif is_in_rect(mouse_x, mouse_y, 320, 385, 260, 40):
                    auto_player.scan_songs()

                # 指の狭さ（ピンチ判定しきい値）調整ボタン (最下段)
                elif is_in_rect(mouse_x, mouse_y, 320, 435, 120, 38):
                    PINCH_THRESHOLD = round(max(0.20, PINCH_THRESHOLD - 0.05), 2)
                    print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")
                elif is_in_rect(mouse_x, mouse_y, 460, 435, 120, 38):
                    PINCH_THRESHOLD = round(min(0.95, PINCH_THRESHOLD + 0.05), 2)
                    print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")


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
                state['pinch'] = features['pinch']

                raw_y = features['index_y']
                in_play_zone = (Y_MIN_LIMIT <= raw_y <= Y_MAX_LIMIT)

                y = max(Y_MIN_LIMIT, min(Y_MAX_LIMIT, raw_y))
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

                # 演奏境界線外では音量をゼロにして余韻・誤発音をカット
                cc_value = int(features['pinch'] * 127) if in_play_zone else 0
                cc_value = max(0, min(127, cc_value))

                x_cc_value = int(features['index_x'] * 127)
                x_cc_value = max(0, min(127, x_cc_value))

                outport.send(mido.Message('control_change', control=7, value=cc_value, channel=state['channel']))

                # WAVE_MODEがTrueのときのみX座標（CC#16）を送信
                if WAVE_MODE:
                    outport.send(mido.Message('control_change', control=16, value=x_cc_value, channel=state['channel']))

                # つまみ判定かつ演奏境界線内のときのみ発音有効（端の外では一切反応させない）
                is_pinching = (features['pinch'] >= PINCH_THRESHOLD) and in_play_zone

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

            # 演奏境界線（上端・下端の境界を常に可視化して、線の外で反応しないエリアを明示）
            bound_color = (0, 0, 255) if SHOW_DEBUG else (70, 70, 120)
            bound_thick = 2 if SHOW_DEBUG else 1
            cv2.line(image, (0, int(h * Y_MIN_LIMIT)), (w, int(h * Y_MIN_LIMIT)), bound_color, bound_thick)
            cv2.line(image, (0, int(h * Y_MAX_LIMIT)), (w, int(h * Y_MAX_LIMIT)), bound_color, bound_thick)

            # 自動演奏曲ステータス表示（曲変更後3.5秒間のみトースト表示し、演奏画面に文字が残り続けないようにする）
            if time.time() - auto_player.title_changed_at < 3.5:
                cur_song_title = auto_player.get_current_title()
                put_text_jp(image, f"AutoSong: {cur_song_title}  [N:Next / P:Prev / S:Scan]", (50, 103), 15, (0, 255, 255), bg_color=(30, 30, 30))

            # 右上にSettingsボタンを描画
            draw_button(image, "Settings [Tab]", w - 140, 10, 130, 35, False, active_color=(0, 255, 0))

            # ------------------------------------
            # デバッグ表示（SHOW_DEBUG が True のときのみ）
            # ------------------------------------
            if SHOW_DEBUG:
                # 左上に1行でステータスを集約表示
                wave_txt = "WAVE:ON" if WAVE_MODE else "WAVE:OFF"
                strum_txt = "STRUM:ON" if STRUM_MODE else "STRUM:OFF"
                auto_txt = "AUTO:ON" if AUTO_PLAY_ENABLED else "AUTO:OFF"
                status_line = f"[{CURRENT_KEY}] | {wave_txt} | {strum_txt} | {auto_txt} | TH:{PINCH_THRESHOLD:.2f}"
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
            draw_button(image, "Rescan songs/ [S]", 320, 385, 260, 40, False, active_color=(0, 200, 255))

            # 指の狭さ（ピンチ判定しきい値）コントロール (最下段)
            r_p = int(hand_states['Right'].get('pinch', 0.0) * 100)
            l_p = int(hand_states['Left'].get('pinch', 0.0) * 100)
            put_text_jp(image, f"Pinch Threshold: {PINCH_THRESHOLD:.2f} (Now R:{r_p}% L:{l_p}%)", (40, 442), 15, (255, 255, 255))
            draw_button(image, "[-] Down", 320, 435, 120, 38, False, active_color=(255, 200, 0))
            draw_button(image, "[+] Up", 460, 435, 120, 38, False, active_color=(255, 200, 0))

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

        # 映像を表示（黒帯なしでウィンドウ・全画面に完全追従）
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
                auto_player.title_changed_at = time.time()
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
            elif key == ord('s'):
                auto_player.scan_songs()
            elif key == ord('-') or key == ord('_'):
                PINCH_THRESHOLD = round(max(0.20, PINCH_THRESHOLD - 0.05), 2)
                auto_player.title_changed_at = time.time()
                print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")
            elif key == ord('=') or key == ord('+'):
                PINCH_THRESHOLD = round(min(0.95, PINCH_THRESHOLD + 0.05), 2)
                auto_player.title_changed_at = time.time()
                print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")
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
