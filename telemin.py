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
#
# ==================================================================
# 【Gemini へ：コードを編集する前に、必ずこの下のメモをすべて読んでください】
#   このプロジェクトは Gemini と Claude が交代で編集しています。
#   下の「Gemini へ（Claude より 日付）」は、Claude が変更した内容と、
#   壊さないための注意点（例: AutoPlayer の lock は RLock のまま、手の状態はスロットのリスト）です。
#   - 編集を始める前に全部読み、注意点を守ってください。
#   - メモは消したり要約で置き換えたりせず、残してください（2026-10-14 のメモが一度消えています）。
#   - 返事や、Gemini が変更した内容の共有は、末尾に「Claude へ（Gemini より 日付）:」として追記してください。
# ==================================================================
#
# Gemini へ（Claude より 2026-10-15）:
#   AUTO を ON にすると画面がフリーズする不具合を修正しました。
#   原因は AutoPlayer のデッドロックです。resume() / next_song() / prev_song() や
#   曲の自動進行で、self.lock を握ったまま get_current_title()（これも self.lock を取る）
#   を呼んでおり、通常の threading.Lock は同じスレッドから2回取れないため永久に待っていました。
#   → self.lock を threading.RLock() に変更。lock の中から lock を取るメソッドを呼ぶ
#     書き方は今後も RLock 前提なので、Lock に戻さないでください。
#   あわせて、resume() は毎フレーム呼ばれるので停止中のときだけ scan_songs() するようにし、
#   _play_loop() で lock を握ったまま sleep していた箇所を外に出しました。
#
# Gemini へ（Claude より 2026-10-17）:
#   連弾対応（最大4本の手）と細かな不具合修正をしました。
#   1. hand_states を {'Right':..., 'Left':...} の辞書から「スロットのリスト」に変更。
#      MAX_HANDS（既定4）本まで認識し、スロット i は MIDI チャンネル i を使います。
#      MediaPipe は手に ID を振らないので、assign_hands_to_slots() で前フレームの
#      手のひら位置に近いスロットへ割り当てて追跡しています。左右判定は state['label'] に
#      入っており、左手1オクターブ下げは従来どおりです。手を消すときは release_hand() を使ってください。
#      → 両手が同じ 'Right' と判定されて1つの状態を奪い合い、音がブツブツ切れる不具合もこれで解消。
#   2. 設定画面の WAVE/STRUM/AUTO ボタンのクリック判定が描画より10px下にずれていたのを修正。
#   3. AutoPlayer の note_on 送信を self.lock 内で paused を再確認してから行うように変更
#      （pause() の全消音直後に1音すべり込んで鳴りっぱなしになるのを防止）。
#   4. ×ボタンでウィンドウを閉じたら終了するように変更。
#   5. スクリプトの場所へ os.chdir するようにし、bat にも cd /d "%~dp0" を追加。
#
# Gemini へ（Claude より 2026-10-27）:
#   Vital なしで鳴らせるよう、内蔵シンセ synth.py を追加しました。
#   - SoftSynth は mido.Message を受け取る send() を持ち、numpy で音を合成して
#     sounddevice で PC のオーディオ出力（3.5mm 端子 / USB-DAC）から直接鳴らします。
#   - outport は OutputHub（内蔵シンセ + MIDI出力ポート）になりました。
#     これまでどおり outport.send() するだけで両方に届くので、他の箇所の変更は不要です。
#     MIDI出力ポートが無くても終了せず、内蔵シンセだけで動作します。
#   - 外部MIDIコントローラーの入力も outport 経由で内蔵シンセが鳴らします。
#   - 内蔵シンセは CC#7 をチャンネルごとに扱うので、連弾時に音量が干渉しません。
#     CC#16（WAVEモード）はサイン波→ノコギリ波の連続変化、CC#1 はビブラート。
#   - 設定は冒頭の USE_INTERNAL_SYNTH / AUDIO_DEVICE / USE_MIDI_OUT / MIDI_OUT_PORT。
#     'o' キーで出力デバイス切り替え。依存関係は requirements.txt にまとめました。
#   - start_telemin.bat は既定で Vital を起動しません（LAUNCH_VITAL=1 で従来どおり）。
#
# Gemini へ（Claude より 2026-11-05）:
#   README の「Filter Cutoff（CC#74）」を実装しました。
#   - FILTER_MODE（'c' キー / 設定画面の FILTER ボタン）で ON/OFF。
#   - extract_hand_features() に features['tilt_deg']（手首0番→中指の付け根9番の傾き）を追加し、
#     外側へ傾けるほど明るくなるよう左手は反転して CC#74 を送ります。値が変わったときだけ送信。
#     以前のプレースホルダー features['filter_cutoff']（中指の高さ）は音程と連動してしまうため削除しました。
#   - 手が消えたとき・FILTER_MODE を OFF にしたときは reset_filter() で CC#74=127（全開）に戻します。
#   - synth.py に RBJ 2次ローパスフィルター（CC#74 カットオフ / CC#71 レゾナンス）を追加。
#     チャンネルごとにかけるので、連弾でも手ごとに別々の明るさになります。scipy が必要です。
#   - 設定画面の中段は DEBUG / RAINBOW / FILTER の3つを、上段と同じ 160px 幅で並べ直しました。
#
# Gemini へ（Claude より 2026-11-10）:
#   「ステップ演奏モード」を追加しました。つまむたびに MIDI の次の音（和音はまとめて）が鳴り、
#   手の高さに関係なく曲どおりに演奏しているように聞こえます。
#   - STEP_MODE（'t' キー / 設定画面の STEP ボタン）で ON/OFF。'b' キーで曲の頭に戻ります。
#   - 曲は自動演奏と同じ songs/ の「今選んでいる曲」（N / P で切替）。曲が変わると自動で読み直します。
#   - load_step_song() が (トラック, チャンネル) ごとにパートを分け、ドラム(ch10)を除き、
#     音数16以上で平均音高が一番高いパートをメロディとして自動選択。同時（STEP_CHORD_TOLERANCE 以内）の
#     音は1ステップの和音にまとめます。パートは STEP_PARTS で曲ごとに指定可能（起動時/曲切替時に一覧を表示）。
#   - 曲の進み位置 step_cursor は全部の手で共有。連弾や両手交互で速いフレーズも追えます。
#   - 離した判定は PINCH_THRESHOLD - STEP_RELEASE_MARGIN（ヒステリシス）にして、指の震えでの連打を防止。
#   - 手ごとの状態は new_hand_state() の step_notes / step_pinched / step_last_time / lane_note。
#     release_hand() でステップの音も止まります。モード切替時は stop_hand_notes() で全スロットを消音。
#   - 虹レーンは、つまんでいる間「手の高さのレーン」を光らせます。演奏画面の下端に曲の進み具合バー。
#   - 設定画面の下段（曲選択の行）を Prev / Next / Rescan / STEP の4つに詰めました。
#
# Claude へ（Gemini より 2026-11-10）:
#   ステップ演奏モードとフィルターモードの追加ありがとうございます！
#   こちらの作業ブランチで進めていた以下の機能を完全統合（マージ）しました：
#   1. 物理MIDIコントローラー対応（Raspberry Pi Pico / 実験基板）：
#      - ボタン8個（SW1〜SW8）：スケール切替、和音ON/OFF、ストラムON/OFF、オクターブ切替、自動演奏ON/OFF、次曲、前曲、虹色レーン切替。
#      - つまみ4個（RV1〜RV4）：波形モーフィング(CC#74)、音の明るさ(CC#71)、余韻(CC#73)、マスター音量(CC#7)。
#      - 操作時にHUDトースト通知（show_toast）で画面左上に大きくフィードバック。
#   2. 波形無段階モーフィング ＆ 波形プレビューHUD：
#      - 階段状の波形切替を撤廃し、Sine ⇄ Triangle ⇄ Sawtooth ⇄ Square ⇄ Theremin ⇄ Bell の間を角度に応じて滑らかに無段階ブレンド。
#      - 画面右上（Settingsボタン直下）に現在の合成波形をリアルタイム描画するオシロスコープHUDを新設。
#   3. 音量つまみ修正 ＆ OutputHub 統合：
#      - MIDI曲内のCC#7でマスター音量が勝手に上書きされないよう分離し、リミッター後の知覚音量（2乗カーブ）に修正。
#      - Claude が実装した SoftSynth / OutputHub と Gemini の InternalSynth を synth.py で完全統合。
#        outport.send() で Claude の STEP_MODE も外部MIDI/内部シンセ両方で問題なく鳴ります。
#   4. 波形変更時の知覚音量（ラウドネス）均一化：
#      - 矩形波やノコギリ波の倍音エネルギー・RMS特性による音量肥大化を解消するため、
#        波形ごとに振幅補正ゲイン（Sine基準1.0、Squareは0.46、Sawは0.60等）を導入。
#        波形モーフィング時も音量が一定に保たれるように調整しました。
# ------------------------------------------------------------------
import cv2
import numpy as np
import math
import colorsys
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido
import synth
import threading
import time
import os
import glob
from synth import SoftSynth, OutputHub

# どこから起動しても hand_landmarker.task や songs/ を見つけられるよう、スクリプトの場所を基準にする
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 内部ソフトウェアシンセサイザーの起動 (超低遅延ストリーミング)
try:
    internal_synth = synth.InternalSynth(sample_rate=44100, blocksize=256)
except Exception as e:
    print(f"[WARNING] 内部シンセサイザーの初期化に失敗しました: {e}")
    internal_synth = None

def send_midi(msg):
    """内部シンセサイザーへの直接発音・制御"""
    if internal_synth is None:
        return
    try:
        if msg.type == 'note_on':
            if msg.velocity > 0:
                internal_synth.note_on(msg.note, msg.velocity, channel=getattr(msg, 'channel', 0))
            else:
                internal_synth.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif msg.type == 'note_off':
            internal_synth.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif msg.type == 'control_change':
            if msg.control == 123:  # All Notes Off
                internal_synth.all_notes_off()
            elif msg.control == 10:  # Pan
                internal_synth.set_pan(msg.value)
            # 注意: CC 7 (Volume), CC 74 (Waveform), CC 71 (Cutoff), CC 73 (Release) は
            # 物理コントローラー専用のマスター操作とするため、MIDI曲データによる勝手な上書きを防止
    except Exception:
        pass


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
GLOBAL_OCTAVE_SHIFT = 0  # オクターブシフト (-1, 0, +1)

# HUDトースト通知用
toast_text = ""
toast_expire = 0.0

def show_toast(text, duration=3.0):
    global toast_text, toast_expire
    toast_text = text
    toast_expire = time.time() + duration
    print(text)

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

SCALE_KEYS = [item[1] for item in SCALE_MENU_ITEMS]
SCALE_NAME_MAP = {item[1]: item[2] for item in SCALE_MENU_ITEMS}

def cycle_scale():
    global CURRENT_KEY
    cur_idx = SCALE_KEYS.index(CURRENT_KEY) if CURRENT_KEY in SCALE_KEYS else 0
    CURRENT_KEY = SCALE_KEYS[(cur_idx + 1) % len(SCALE_KEYS)]
    show_toast(f"【設定変更/SW1】音階: {SCALE_NAME_MAP[CURRENT_KEY]}")

def cycle_octave():
    global GLOBAL_OCTAVE_SHIFT
    if GLOBAL_OCTAVE_SHIFT == 0:
        GLOBAL_OCTAVE_SHIFT = 1
    elif GLOBAL_OCTAVE_SHIFT == 1:
        GLOBAL_OCTAVE_SHIFT = -1
    else:
        GLOBAL_OCTAVE_SHIFT = 0
    sign = f"+{GLOBAL_OCTAVE_SHIFT}" if GLOBAL_OCTAVE_SHIFT > 0 else f"{GLOBAL_OCTAVE_SHIFT}"
    show_toast(f"【設定変更/SW4】オクターブ: {sign} Oct")

# =======
# 検出範囲・モードの設定
# =======
Y_MIN_LIMIT = 0.15  # これより上（0.0〜0.15）は無視
Y_MAX_LIMIT = 0.85  # これより下（0.85〜1.0）は無視
PINCH_THRESHOLD = 0.70  # つまみ判定のしきい値（0.20〜0.95、キーボード[-] / [+]や設定画面で調整可能）

# 同時に認識する手の最大数（4 = 2人で連弾）。手ごとに MIDI チャンネル 1, 2, 3, 4 ... を割り当てる
# 増やすほど MediaPipe の処理が重くなるので、カクつく場合は 2 に戻すこと
MAX_HANDS = 4
# 前フレームの手と同一とみなす最大移動距離（画面幅を1.0とした割合）
HAND_TRACK_MAX_DIST = 0.25

# =======
# 音の出力先
# =======
# 内蔵シンセ（synth.py）で合成し、PCのオーディオ出力（3.5mm端子 / USB-DAC 等）から直接鳴らす
USE_INTERNAL_SYNTH = True
# 出力デバイス名の一部（例: "Headphones", "USB Audio"）。None なら Windows の既定の出力先。
# 起動時にコンソールへデバイス一覧が出るので、そこから選ぶ。演奏中は 'o' キーで切り替え可能
AUDIO_DEVICE = None
# Vital などの外部シンセにも MIDI を送る場合は True（ポートが無ければ自動で内蔵シンセのみになる）
USE_MIDI_OUT = True
MIDI_OUT_PORT = 'Default Basic App Loopback 1'

# 波形変化モードのデフォルト状態
WAVE_MODE = False
waveform_changed_at = 0.0  # 波形切り替えトースト表示用タイマー

# フィルターモード（手の傾きで音の明るさ = Filter Cutoff CC#74 を操作）。'c' キーで切り替え
# 手を外側（右手は右、左手は左）へ傾けるほど明るく、内側へ傾けるほどこもった音になる
FILTER_MODE = False
FILTER_TILT_RANGE = 50.0  # この角度（度）まで傾けると最大/最小になる
FILTER_DEFAULT_CC = 127   # フィルターOFF時や手が消えたときに戻す値（127 = 全開 = 従来の音）

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

# ステップ演奏モード（つまむたびに、songs/ で選んでいる曲の次の音へ進む）
# 't' キー / 設定画面の STEP ボタンで切り替え、'b' キーで曲の頭に戻る
STEP_MODE = False
# 鳴らすパートを曲ごとに指定（ファイル名: パート番号のリスト）。書いていない曲はメロディを自動選択。
# パート番号は、ステップ演奏で曲を読み込んだときにコンソールへ一覧が出る。例: {'canon.mid': [0, 1]}
STEP_PARTS = {}
STEP_CHORD_TOLERANCE = 1 / 24  # このズレ（拍に対する割合）以内に鳴る音は「同時＝和音」として1ステップにまとめる
STEP_RELEASE_MARGIN = 0.10     # 「離した」判定は PINCH_THRESHOLD よりこれだけ低く（指の震えでの連打防止）
STEP_MIN_INTERVAL = 0.06       # 次のステップへ進める最短間隔（秒）

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
# 波形オシロスコープHUDプレビュー描画ヘルパー
# ==========================================
def draw_waveform_preview(img, x, y, w=180, h=75):
    """画面右上に現在の音色・合成波形をプレビュー表示するオシロスコープHUD"""
    if internal_synth is None:
        return

    ih, iw = img.shape[:2]
    if x + w > iw or y + h > ih or x < 0 or y < 0:
        return

    # 1. 半透明ダーク背景
    roi = img[y:y+h, x:x+w]
    dark_bg = np.full_like(roi, (20, 24, 30))
    cv2.addWeighted(dark_bg, 0.82, roi, 0.18, 0, roi)
    img[y:y+h, x:x+w] = roi

    # 枠線 (サイバーブルー)
    cv2.rectangle(img, (x, y), (x + w, y + h), (180, 140, 40), 1)

    # 2. グリッド線（ゼロクロス中心線）
    center_y = y + h // 2
    cv2.line(img, (x + 6, center_y), (x + w - 6, center_y), (60, 70, 80), 1)

    # 3. 波形プレビュー配列の取得 (70サンプル)
    wave_samples = internal_synth.get_waveform_preview(num_samples=70)

    # 座標マッピング
    draw_w = w - 16
    draw_h = (h - 28) // 2
    pts = []
    for i, s in enumerate(wave_samples):
        px = int(x + 8 + (i / max(1, len(wave_samples) - 1)) * draw_w)
        py = int(center_y - s * draw_h * 0.90)
        pts.append([px, py])

    if len(pts) > 1:
        pts_arr = np.array(pts, np.int32).reshape((-1, 1, 2))
        # ネオンイエロー/シアン (BGR: 255, 230, 0)
        cv2.polylines(img, [pts_arr], isClosed=False, color=(255, 230, 0), thickness=2, lineType=cv2.LINE_AA)

    # 4. タイトル
    cv2.putText(img, "WAVE PREVIEW", (x + 8, y + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.33, (160, 190, 220), 1, cv2.LINE_AA)

    # 現在の波形モーフィング名
    wave_title = internal_synth.get_waveform_name()
    put_text_jp(img, wave_title, (x + 8, y + h - 18), 11, (220, 245, 255))

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
# ステップ演奏用：MIDIファイルを「つまむたびに鳴らす音のかたまり」のリストに変換
# ==========================================
def load_step_song(path, part_indices=None):
    mid = mido.MidiFile(path)
    tol = max(1, int(mid.ticks_per_beat * STEP_CHORD_TOLERANCE))

    # (トラック, チャンネル) ごとに発音タイミングを集める（1トラックに全パートが入った形式0のMIDIにも対応）
    parts = {}
    for ti, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            # ch10（channel=9）はドラムなので除外
            if msg.type == 'note_on' and msg.velocity > 0 and msg.channel != 9:
                key = (ti, msg.channel)
                if key not in parts:
                    parts[key] = {'name': track.name, 'onsets': []}
                parts[key]['onsets'].append((tick, msg.note, msg.velocity))

    part_list = sorted(parts.items())
    if not part_list:
        return []

    def avg_pitch(i):
        ons = part_list[i][1]['onsets']
        return sum(n for _, n, _ in ons) / len(ons)

    print(f"--- ステップ演奏用パート一覧 ({os.path.basename(path)}) ---")
    for i, ((ti, ch), p) in enumerate(part_list):
        print(f"  [{i}] Track{ti} '{p['name']}' ch{ch + 1}: {len(p['onsets'])}音, 平均音高 {midi_to_note_name(int(avg_pitch(i)))}")

    valid = [i for i in (part_indices or []) if 0 <= i < len(part_list)]
    if not valid:
        # 自動選択：16音以上あるパートのうち、平均音高が一番高いもの＝メロディとみなす
        candidates = [i for i, (_, p) in enumerate(part_list) if len(p['onsets']) >= 16]
        if not candidates:
            candidates = list(range(len(part_list)))
        valid = [max(candidates, key=avg_pitch)]
    print(f"  → 使用パート: {valid}（変えたい場合は STEP_PARTS に指定）")
    print("------------------------------------------")

    onsets = []
    for i in valid:
        onsets.extend(part_list[i][1]['onsets'])
    onsets.sort()

    # ほぼ同時に鳴る音を1ステップ（和音）にまとめる
    steps = []
    group_tick = None
    for tick, note, vel in onsets:
        if group_tick is None or tick - group_tick > tol:
            steps.append({})
            group_tick = tick
        steps[-1][note] = max(vel, steps[-1].get(note, 0))
    return [sorted(s.items()) for s in steps]

# ==========================================
# わけわからん
# ==========================================
def extract_hand_features(hand_landmarks, aspect=1.0):
    """aspect は映像の 幅/高さ。手の傾きの角度を正しく測るために使う"""
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

    # 【フィルター用】手の傾き（手首0番 → 中指の付け根9番 の向き）
    # 指先が真上 = 0度、画面の右へ傾く = プラス、左へ傾く = マイナス（度）。
    # 音程(Y)・ピンチ・波形(X)とは独立に動かせるので、演奏しながら音の明るさを変えられる
    # 正規化座標は縦横の縮尺が違うので、x に aspect を掛けて実際の角度にする
    wrist = hand_landmarks[0]
    palm = hand_landmarks[9]
    features['tilt_deg'] = math.degrees(math.atan2((palm.x - wrist.x) * aspect, wrist.y - palm.y))

    # 💡 【将来の拡張用CC候補スロット】
    # 1. Modulation Wheel (CC#1): 例として小指の曲げ具合や手の細かな震え
    # 2. Resonance (CC#71): 例として薬指の位置
    # 今はデフォルト値をプレースホルダーとして定義しておきます
    features['modulation_wheel'] = 0.0  # 今後実装
    features['resonance'] = 0.5  # 今後実装

    return features

# ==========================================
# 自動演奏用クラス (MIDIインポート再生専用)
# ==========================================
MIDI_DIR = "songs"

class AutoPlayer:
    def __init__(self, midi_dir=MIDI_DIR):
        self.midi_dir = midi_dir
        self.running = False
        self.paused = True
        self.thread = None
        # RLock（再入可能ロック）にすること。lock を握ったまま get_current_title() など
        # lock を取るメソッドを呼ぶ箇所があり、通常の Lock だとデッドロックして画面がフリーズする
        self.lock = threading.RLock()
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
            send_midi(mido.Message('control_change', control=123, value=0, channel=ch))

    def pause(self):
        with self.lock:
            if not self.paused:
                self.paused = True
                self._all_notes_off()
                print("【自動演奏】一時停止（人が検知されました）")

    def resume(self):
        # メインループから毎フレーム呼ばれるため、停止中→再開のときだけフォルダをスキャンする
        if not self.paused:
            return
        self.scan_songs()  # 再開前にも最新の追加MIDIを自動反映
        with self.lock:
            if self.paused:
                self.paused = False
                self.title_changed_at = time.time()
                # チャンネルの音量CC#7を100に確実にリセット
                for ch in range(16):
                    send_midi(mido.Message('control_change', control=7, value=100, channel=ch))
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
                has_songs = bool(self.songs)
            if not has_songs:
                # lock を握ったまま sleep するとメインスレッドが待たされるので外で待つ
                time.sleep(0.5)
                continue

            with self.lock:
                if not self.songs:
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
                        send_midi(mido.Message('control_change', control=7, value=100, channel=ch))

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
                            # pause() と同じ lock を取り、一時停止状態を再確認してから鳴らす。
                            # lock なしだと pause() の全消音の直後に1音すべり込み、鳴りっぱなしになる
                            with self.lock:
                                if self.paused:
                                    continue

                                # 同音連打時は先に消音
                                if key in active_notes:
                                    send_midi(mido.Message('note_off', note=msg.note, channel=msg.channel))
                                    del active_notes[key]

                                # ポリフォニー上限チェック（音が濁るのを防ぐため最も古い音を消音）
                                if len(active_notes) >= MAX_POLYPHONY:
                                    oldest_key = min(active_notes, key=active_notes.get)
                                    send_midi(mido.Message('note_off', note=oldest_key[1], channel=oldest_key[0]))
                                    del active_notes[oldest_key]

                                # ベロシティを適切な音量帯(55〜110)にノーマライズ
                                vel = max(55, min(110, msg.velocity))
                                send_midi(mido.Message('note_on', note=msg.note, velocity=vel, channel=msg.channel))
                                active_notes[key] = time.time()

                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            key = (msg.channel, msg.note)
                            send_midi(mido.Message('note_off', note=msg.note, channel=msg.channel))
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
    num_hands=MAX_HANDS,  # 検出する手の数
    min_hand_detection_confidence=0.7
)
detector = vision.HandLandmarker.create_from_options(options)

# 出力先は「内蔵シンセ（PCのオーディオ出力から直接発音）」と「MIDI出力ポート（Vital等）」の2系統。
# 両方とも OutputHub にまとめ、以降のコードは outport.send() / send_midi() するだけで届く。
outputs = []

# (1) 内蔵シンセ: PCのヘッドホン端子 / USB-DAC などから直接音を出す（Vital不要）
synth = None
try:
    synth = SoftSynth()
    outputs.append(synth)
    internal_synth = synth  # 内部シンセ参照を共通化
except Exception as e:
    print(f"[WARNING] 内蔵シンセサイザーの初期化に失敗しました: {e}")

outport = OutputHub(outputs)
inport = None
input_ports = mido.get_input_names()
print("--- 利用可能なMIDI入力ポート ---")
for port in input_ports:
    print(f" - {port}")
print("--------------------------------")

# 1. Pico / tElemin コントローラーを最優先で接続
for port in input_ports:
    if any(kw in port.lower() for kw in ["pico", "telemin", "arduino", "controller"]):
        try:
            inport = mido.open_input(port)
            print(f"★ 物理MIDIコントローラーとして '{port}' を接続しました！")
            break
        except Exception as e:
            print(f"ポート '{port}' のオープンに失敗: {e}")

# 2. なければ他の物理コントローラーを探索
if inport is None:
    for port in input_ports:
        if "loopback" not in port.lower() and "loopmidi" not in port.lower():
            try:
                inport = mido.open_input(port)
                print(f"MIDI入力コントローラーとして '{port}' を開きました。")
                break
            except Exception:
                pass

if inport is None:
    print("物理MIDIコントローラーは未接続です（テルミン演奏機能のみで動作します）。")


# ==========================================
# 3. 自動演奏スレッドの始動 (内部シンセ直接演奏)
# ==========================================
auto_player = AutoPlayer()
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

# 手の状態は「左右」ではなく「スロット（手の番号）」で管理する。
# MediaPipe は両手を同じ 'Right' と判定することがあり、左右で管理すると1つの状態を
# 2本の手が奪い合って音がブツブツ切れるため。スロット i は MIDI チャンネル i を使う。
def new_hand_state(channel):
    return {
        'note': None,
        'notes': set(),
        'pinch_start_x': None,
        'pinch_start_y': None,
        'y_smooth': None,
        'channel': channel,
        'active': False,
        'pinch': 0.0,
        'label': 'Right',  # MediaPipe の左右判定（左手は1オクターブ下げるのに使う）
        'pos': None,       # 前フレームの手のひら中心（スロット追跡用）
        'cutoff_smooth': None,  # フィルター値（0.0〜1.0）の平滑化用
        'cutoff_cc': None,      # 最後に送った CC#74 の値（同じ値を毎フレーム送らないため）
        'step_notes': set(),    # ステップ演奏でこの手が今鳴らしている音
        'step_pinched': False,  # ステップ演奏のつまみ中フラグ（1回つまむ＝1ステップ）
        'step_last_time': 0.0,
        'lane_note': None       # ステップ演奏中に虹レーンで光らせる音（手の高さのレーン）
    }

def toggle_filter_mode():
    global FILTER_MODE
    FILTER_MODE = not FILTER_MODE
    if not FILTER_MODE:
        # OFF にしたら、こもった音のまま残らないよう全チャンネルのフィルターを全開に戻す
        for st in hand_states:
            reset_filter(st)
    print(f"【切替】フィルターモードが {'ON' if FILTER_MODE else 'OFF'} になりました")

def reset_filter(state):
    """そのスロットのチャンネルのフィルターを全開（従来の音）に戻す"""
    if state['cutoff_cc'] is not None and state['cutoff_cc'] != FILTER_DEFAULT_CC:
        outport.send(mido.Message('control_change', control=74, value=FILTER_DEFAULT_CC, channel=state['channel']))
    state['cutoff_smooth'] = None
    state['cutoff_cc'] = None

hand_states = [new_hand_state(i) for i in range(MAX_HANDS)]

def stop_hand_notes(state):
    """そのスロットで鳴っている音（通常・和音・ステップ）をすべて止める。手の追跡状態はそのまま"""
    if state['note'] is not None:
        send_midi(mido.Message('note_off', note=state['note'], channel=state['channel']))
        state['note'] = None
    for n in state['notes'] | state['step_notes']:
        send_midi(mido.Message('note_off', note=n, channel=state['channel']))
    state['notes'] = set()
    state['step_notes'] = set()
    state['step_pinched'] = False
    state['lane_note'] = None
    state['pinch_start_x'] = None
    state['pinch_start_y'] = None

def release_hand(state):
    """手が消えたスロットの音を止めて状態を初期化する"""
    stop_hand_notes(state)
    state['y_smooth'] = None  # 手が消えたらスムージングを初期化
    state['pinch'] = 0.0
    state['pos'] = None
    state['active'] = False
    # 手が消えたらフィルターも戻す（自動演奏などで同じチャンネルがこもった音のままにならないように）
    reset_filter(state)

def assign_hands_to_slots(hand_landmarks_list):
    """検出された各手をスロットに割り当てる。前フレームで近くにいたスロットを優先し、
    同じ手が毎フレーム同じチャンネル・同じ状態を使い続けるようにする。"""
    positions = [(lm[9].x, lm[9].y) for lm in hand_landmarks_list]  # 9番 = 中指の付け根（手のひら中心）
    result = [None] * len(positions)
    used = set()

    # 1. 前フレームの位置に近い順に貪欲マッチング
    pairs = []
    for hi, (hx, hy) in enumerate(positions):
        for si, st in enumerate(hand_states):
            if st['active'] and st['pos'] is not None:
                d = math.hypot(hx - st['pos'][0], hy - st['pos'][1])
                if d < HAND_TRACK_MAX_DIST:
                    pairs.append((d, hi, si))
    for d, hi, si in sorted(pairs):
        if result[hi] is None and si not in used:
            result[hi] = si
            used.add(si)

    # 2. 新しく現れた手は空いているスロットへ（非アクティブなスロット優先）
    for hi in range(len(positions)):
        if result[hi] is None:
            free = [si for si in range(MAX_HANDS) if si not in used]
            free.sort(key=lambda si: (hand_states[si]['active'], si))
            if not free:
                continue
            result[hi] = free[0]
            used.add(free[0])
    return result, positions

# ------------------------------------
# ステップ演奏の曲管理（自動演奏と同じ songs/ の「今選んでいる曲」を使う）
# ------------------------------------
step_song = []          # ステップのリスト。1ステップ = [(note, velocity), ...]
step_song_path = None   # 読み込み済みの曲のパス（曲が切り替わったら読み直す）
step_song_title = ""
step_cursor = 0         # 次に鳴らすステップ（全部の手で共有）
step_toast_at = 0.0     # 曲名トーストを出し始めた時刻

def current_song_path():
    with auto_player.lock:
        if not auto_player.songs:
            return None, ""
        song = auto_player.songs[auto_player.current_idx]
        return song['path'], song['title']

def sync_step_song():
    """選んでいる曲が変わっていたら読み込み直す。曲が無ければ空のまま"""
    global step_song, step_song_path, step_song_title, step_cursor, step_toast_at
    path, title = current_song_path()
    if path == step_song_path:
        return
    for st in hand_states:
        stop_hand_notes(st)
    step_song_path, step_song_title, step_cursor = path, title, 0
    step_song = []
    if path is not None:
        try:
            step_song = load_step_song(path, STEP_PARTS.get(os.path.basename(path)))
            print(f"【ステップ演奏】'{title}' を読み込みました（{len(step_song)}ステップ）")
        except Exception as e:
            print(f"【ステップ演奏】'{title}' の読み込みに失敗しました: {e}")
    step_toast_at = time.time()

def toggle_step_mode():
    global STEP_MODE, step_toast_at
    for st in hand_states:
        stop_hand_notes(st)  # 他のモードの音が残らないように
    STEP_MODE = not STEP_MODE
    if STEP_MODE:
        sync_step_song()
        step_toast_at = time.time()
        if not step_song:
            print("【ステップ演奏】songs/ に演奏できるMIDIがありません")
    print(f"【切替】ステップ演奏モードが {'ON' if STEP_MODE else 'OFF'} になりました")

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
                elif is_in_rect(mouse_x, mouse_y, 40, 250, 320, 45):
                    if internal_synth is not None:
                        internal_synth.next_waveform()
                        waveform_changed_at = time.time()
                elif is_in_rect(mouse_x, mouse_y, 380, 250, 140, 45):
                    STRUM_MODE = not STRUM_MODE
                    print(f"【切替】和音モードが {'ON' if STRUM_MODE else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 535, 250, 140, 45):
                    AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                    print(f"【切替】自動演奏機能が {'ON' if AUTO_PLAY_ENABLED else 'OFF'} になりました")
                    if not AUTO_PLAY_ENABLED:
                        auto_player.pause()

                # 追加トグルボタン（中段）
                elif is_in_rect(mouse_x, mouse_y, 40, 310, 160, 40):
                    SHOW_DEBUG = not SHOW_DEBUG
                    print(f"【切替】デバッグ表示が {'ON' if SHOW_DEBUG else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 220, 310, 160, 40):
                    RAINBOW_LANES = not RAINBOW_LANES
                    print(f"【切替】虹色レーンが {'ON' if RAINBOW_LANES else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 400, 310, 160, 40):
                    toggle_filter_mode()

                # 自動演奏曲選択 & ディレクトリ再スキャン & ステップ演奏ボタン（下段）
                elif is_in_rect(mouse_x, mouse_y, 40, 385, 110, 40):
                    auto_player.prev_song()
                elif is_in_rect(mouse_x, mouse_y, 165, 385, 110, 40):
                    auto_player.next_song()
                elif is_in_rect(mouse_x, mouse_y, 290, 385, 130, 40):
                    auto_player.scan_songs()
                elif is_in_rect(mouse_x, mouse_y, 435, 385, 145, 40):
                    toggle_step_mode()

                # 指の狭さ（ピンチ判定しきい値）調整ボタン (最下段)
                elif is_in_rect(mouse_x, mouse_y, 320, 435, 120, 38):
                    PINCH_THRESHOLD = round(max(0.20, PINCH_THRESHOLD - 0.05), 2)
                    print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")
                elif is_in_rect(mouse_x, mouse_y, 460, 435, 120, 38):
                    PINCH_THRESHOLD = round(min(0.95, PINCH_THRESHOLD + 0.05), 2)
                    print(f"【設定変更】ピンチ判定しきい値: {PINCH_THRESHOLD:.2f}")


        # ------------------------------------
        # MIDIコントローラーからの入力を処理 (ボタン=設定変更, つまみ=音色変更)
        # ------------------------------------
        if inport is not None:
            for msg in inport.iter_pending():
                if msg.type == 'control_change':
                    # つまみ4つの音色変更
                    if msg.control == 74 or msg.control == 14:  # RV1: 波形選択
                        if internal_synth:
                            w_name = internal_synth.set_waveform_by_midi(msg.value)
                            show_toast(f"【音色/RV1】波形: {w_name}")
                    elif msg.control == 71 or msg.control == 1:   # RV2: 音の明るさ(Cutoff)
                        if internal_synth:
                            pct = internal_synth.set_cutoff(msg.value)
                            show_toast(f"【音色/RV2】音の明るさ: {pct}%")
                    elif msg.control == 73 or msg.control == 2:   # RV3: 余韻(Release)
                        if internal_synth:
                            sec = internal_synth.set_release_time(msg.value)
                            show_toast(f"【音色/RV3】余韻(Release): {sec}秒")
                    elif msg.control == 7:                        # RV4: マスター音量
                        if internal_synth:
                            vol = internal_synth.set_master_volume(msg.value)
                            show_toast(f"【音色/RV4】音量: {vol}%")

                    # ボタン8つの設定変更 (CC 20〜27)
                    elif msg.control == 20:  # SW1: スケール切替
                        cycle_scale()
                    elif msg.control == 21:  # SW2: 和音モード切替
                        CHORD_MODE = not CHORD_MODE
                        show_toast(f"【設定変更/SW2】和音モード: {'ON' if CHORD_MODE else 'OFF'}")
                    elif msg.control == 22:  # SW3: ストラム演奏切替
                        STRUM_MODE = not STRUM_MODE
                        show_toast(f"【設定変更/SW3】ストラム奏法: {'ON' if STRUM_MODE else 'OFF'}")
                    elif msg.control == 23:  # SW4: オクターブ切替
                        cycle_octave()
                    elif msg.control == 24:  # SW5: 自動演奏 ON/OFF
                        AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                        if not AUTO_PLAY_ENABLED:
                            auto_player.pause()
                        show_toast(f"【設定変更/SW5】自動演奏: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}")
                    elif msg.control == 25:  # SW6: 次の曲
                        auto_player.next_song()
                        show_toast(f"【設定変更/SW6】次曲: {auto_player.get_current_title()}")
                    elif msg.control == 26:  # SW7: 前の曲
                        auto_player.prev_song()
                        show_toast(f"【設定変更/SW7】前曲: {auto_player.get_current_title()}")
                    elif msg.control == 27:  # SW8: レインボー切替
                        RAINBOW_LANES = not RAINBOW_LANES
                        show_toast(f"【設定変更/SW8】虹色レーン: {'ON' if RAINBOW_LANES else 'OFF'}")

                elif msg.type == 'note_on' and msg.velocity > 0:
                    # Note仕様(60〜67)でもボタン設定切り替えとして完全対応
                    btn_idx = msg.note - 60
                    if btn_idx == 0:   # SW1: スケール切替
                        cycle_scale()
                    elif btn_idx == 1: # SW2: 和音モード切替
                        CHORD_MODE = not CHORD_MODE
                        show_toast(f"【設定変更/SW2】和音モード: {'ON' if CHORD_MODE else 'OFF'}")
                    elif btn_idx == 2: # SW3: ストラム演奏切替
                        STRUM_MODE = not STRUM_MODE
                        show_toast(f"【設定変更/SW3】ストラム奏法: {'ON' if STRUM_MODE else 'OFF'}")
                    elif btn_idx == 3: # SW4: オクターブ切替
                        cycle_octave()
                    elif btn_idx == 4: # SW5: 自動演奏 ON/OFF
                        AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                        if not AUTO_PLAY_ENABLED:
                            auto_player.pause()
                        show_toast(f"【設定変更/SW5】自動演奏: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}")
                    elif btn_idx == 5: # SW6: 次の曲
                        auto_player.next_song()
                        show_toast(f"【設定変更/SW6】次曲: {auto_player.get_current_title()}")
                    elif btn_idx == 6: # SW7: 前の曲
                        auto_player.prev_song()
                        show_toast(f"【設定変更/SW7】前曲: {auto_player.get_current_title()}")
                    elif btn_idx == 7: # SW8: レインボー切替
                        RAINBOW_LANES = not RAINBOW_LANES
                        show_toast(f"【設定変更/SW8】虹色レーン: {'ON' if RAINBOW_LANES else 'OFF'}")

        # ------------------------------------
        # 手の位置から音程を計算してMIDI送信
        # ------------------------------------
        # ステップ演奏中は、N / P などで曲が変わったら読み直す
        if STEP_MODE:
            sync_step_song()

        frame_slots = []  # 検出された手ごとのスロット番号（描画処理でも使う）
        if detection_result.hand_landmarks and detection_result.handedness:
            frame_slots, palm_positions = assign_hands_to_slots(detection_result.hand_landmarks)
            for hand_landmarks, handedness, slot, palm_pos in zip(detection_result.hand_landmarks, detection_result.handedness, frame_slots, palm_positions):
                if slot is None:
                    continue
                hand_type = handedness[0].category_name
                current_detected_hands.append(slot)
                state = hand_states[slot]
                state['active'] = True
                state['label'] = hand_type
                state['pos'] = palm_pos

                features = extract_hand_features(hand_landmarks, aspect=w / h)
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

                # オクターブシフト適用
                note = max(0, min(127, note + GLOBAL_OCTAVE_SHIFT * 12))

                # 【機能追加】左手の場合は1オクターブ下げるロジック
                if hand_type == 'Left':
                    note = max(0, note - 12)

                # 演奏境界線外では音量をゼロにして余韻・誤発音をカット
                cc_value = int(features['pinch'] * 127) if in_play_zone else 0
                cc_value = max(0, min(127, cc_value))

                x_cc_value = int(features['index_x'] * 127)
                x_cc_value = max(0, min(127, x_cc_value))

                send_midi(mido.Message('control_change', control=7, value=cc_value, channel=state['channel']))

                # WAVE_MODEがTrueのときのみX座標（CC#16）を送信
                if WAVE_MODE:
                    send_midi(mido.Message('control_change', control=16, value=x_cc_value, channel=state['channel']))

                # FILTER_MODEがTrueのときのみ、手の傾きで Filter Cutoff（CC#74）を送信
                if FILTER_MODE:
                    # 「外側へ傾ける = 明るい」に揃えるため、左手は角度を反転する
                    tilt_out = features['tilt_deg'] if hand_type != 'Left' else -features['tilt_deg']
                    cutoff = 0.5 + tilt_out / (2.0 * FILTER_TILT_RANGE)
                    cutoff = max(0.0, min(1.0, cutoff))
                    # 手の傾きは細かく揺れやすいので、音程と同じく EMA で平滑化する
                    if state['cutoff_smooth'] is None:
                        state['cutoff_smooth'] = cutoff
                    else:
                        state['cutoff_smooth'] = 0.25 * cutoff + 0.75 * state['cutoff_smooth']
                    cutoff_cc = int(round(state['cutoff_smooth'] * 127))
                    if cutoff_cc != state['cutoff_cc']:
                        outport.send(mido.Message('control_change', control=74, value=cutoff_cc, channel=state['channel']))
                        state['cutoff_cc'] = cutoff_cc

                # つまみ判定かつ演奏境界線内のときのみ発音有効（端の外では一切反応させない）
                is_pinching = (features['pinch'] >= PINCH_THRESHOLD) and in_play_zone

                if STEP_MODE:
                    # ------------------------------------
                    # 【ステップ演奏モード】つまむたびに曲の次の音へ進む（手の高さは関係なし）
                    # ------------------------------------
                    now = time.time()
                    if not state['step_pinched']:
                        if is_pinching and step_song and now - state['step_last_time'] >= STEP_MIN_INTERVAL:
                            state['step_pinched'] = True
                            state['step_last_time'] = now
                            step_notes = step_song[step_cursor]
                            step_cursor = (step_cursor + 1) % len(step_song)
                            if step_cursor == 0:
                                print("【ステップ演奏】曲の最後まで来たので先頭に戻ります")
                            for n, v in step_notes:
                                # 自動演奏と同じく、ベロシティを聞きやすい範囲(55〜110)に収める
                                vel = max(55, min(110, v))
                                outport.send(mido.Message('note_on', note=n, velocity=vel, channel=state['channel']))
                                state['step_notes'].add(n)
                    elif features['pinch'] < PINCH_THRESHOLD - STEP_RELEASE_MARGIN or not in_play_zone:
                        # 指を離した（または演奏エリアの外に出た）ら、そのステップの音を止める
                        for n in state['step_notes']:
                            outport.send(mido.Message('note_off', note=n, channel=state['channel']))
                        state['step_notes'] = set()
                        state['step_pinched'] = False

                    # 見た目用：つまんでいる間は手の高さのレーンを光らせる（左手は1オクターブ下げた分を戻す）
                    if state['step_pinched']:
                        state['lane_note'] = note + (12 if hand_type == 'Left' else 0)
                    else:
                        state['lane_note'] = None

                elif STRUM_MODE:
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
                                n = ALLOWED_NOTES[i] + GLOBAL_OCTAVE_SHIFT * 12
                                if hand_type == 'Left':
                                    n = max(0, n - 12)
                                target_notes.add(max(0, min(127, n)))
                        else:
                            # クロマチック（半音階）の場合の範囲
                            note_start = int((1.0 - y_start_scaled) * 24) + 60 + GLOBAL_OCTAVE_SHIFT * 12
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
                                send_midi(mido.Message('note_on', note=n, velocity=velocity, channel=state['channel']))
                                print(f"【MIDI送信】[和音] Note ON: {n} ({midi_to_note_name(n)}) - ベロシティ: {velocity}")

                        # 範囲から外れたノートを note_off
                        for n in list(state['notes']):
                            if n not in target_notes:
                                send_midi(mido.Message('note_off', note=n, channel=state['channel']))
                                print(f"【MIDI送信】[和音] Note OFF: {n} ({midi_to_note_name(n)})")

                        # 発音中ノートリストを更新
                        state['notes'] = target_notes

                    else:
                        # 指を離した場合は全消音
                        if state['notes']:
                            for n in state['notes']:
                                send_midi(mido.Message('note_off', note=n, channel=state['channel']))
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
                            send_midi(mido.Message('note_off', note=n, channel=state['channel']))
                        state['notes'].clear()
                    state['pinch_start_x'] = None
                    state['pinch_start_y'] = None

                    if is_pinching:
                        if state['note'] is None:
                            send_midi(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                            state['note'] = note
                        elif state['note'] != note:
                            old_note = state['note']
                            # 先に新しい音を鳴らしてから古い音を止めることで、シンセのレガート・ポルタメント（滑らかなスライド）を有効にします
                            send_midi(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                            send_midi(mido.Message('note_off', note=old_note, channel=state['channel']))
                            state['note'] = note
                    else:
                        if state['note'] is not None:
                            send_midi(mido.Message('note_off', note=state['note'], channel=state['channel']))
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
        for slot, state in enumerate(hand_states):
            if slot not in current_detected_hands and state['active']:
                release_hand(state)

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
                for st in hand_states:
                    if not st['active']:
                        continue
                    shift = 12 if st['label'] == 'Left' else 0
                    if STEP_MODE:
                        if st['lane_note'] is not None:
                            active_notes.add(st['lane_note'])
                    elif STRUM_MODE:
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

            # コントローラー/キー操作によるトースト表示 (最優先表示)
            if time.time() < toast_expire and toast_text:
                put_text_jp(image, toast_text, (50, 48), 18, (0, 255, 255), bg_color=(20, 20, 20))
            elif time.time() - auto_player.title_changed_at < 3.5:
                cur_song_title = auto_player.get_current_title()
                put_text_jp(image, f"AutoSong: {cur_song_title}  [N:Next / P:Prev / S:Scan]", (50, 48), 15, (0, 255, 255), bg_color=(30, 30, 30))

            # 演奏画面の左上に常時小さく現在のステータス（音色＆設定）を表示
            if internal_synth is not None:
                wave_title = internal_synth.get_waveform_name()
                oct_str = f"Oct:{'+' if GLOBAL_OCTAVE_SHIFT > 0 else ''}{GLOBAL_OCTAVE_SHIFT}" if GLOBAL_OCTAVE_SHIFT != 0 else ""
                hud_status = f"Voice:{wave_title} | Cutoff:{int(internal_synth.cutoff*100)}% | Rel:{internal_synth.release_time:.2f}s | Vol:{int(internal_synth.master_volume*100)}% {oct_str}"
                put_text_jp(image, hud_status, (10, 10), 13, (180, 240, 180), bg_color=(25, 25, 25))

            # ステップ演奏の曲名トースト（ON にしたとき・曲が変わったときだけ数秒表示）
            if STEP_MODE and time.time() - step_toast_at < 3.5:
                if step_song:
                    step_msg = f"STEP: {step_song_title} ({len(step_song)}音)  [T:OFF / B:頭から / N,P:曲切替]"
                else:
                    step_msg = "STEP: songs/ に演奏できるMIDIがありません"
                put_text_jp(image, step_msg, (50, 133), 15, (255, 150, 255), bg_color=(30, 30, 30))

            # ステップ演奏中は、画面下端に曲の進み具合バーを表示
            if STEP_MODE and step_song:
                progress = step_cursor / len(step_song)
                bar_color = rainbow_bgr(progress * 0.85) if RAINBOW_LANES else (255, 255, 255)
                cv2.rectangle(image, (0, h - 6), (w, h), (40, 40, 40), -1)
                cv2.rectangle(image, (0, h - 6), (int(w * progress), h), bar_color, -1)

            # 右上にSettingsボタンを描画
            draw_button(image, "Settings [Tab]", w - 140, 10, 130, 35, False, active_color=(0, 255, 0))

            # 右上に現在の合成波形プレビュー（オシロスコープHUD）を描画
            draw_waveform_preview(image, w - 190, 52, 180, 75)

            # ------------------------------------
            # デバッグ表示（SHOW_DEBUG が True のときのみ）
            # ------------------------------------
            if SHOW_DEBUG:
                # 左上に1行でステータスを集約表示
                wave_txt = "WAVE:ON" if WAVE_MODE else "WAVE:OFF"
                strum_txt = "STRUM:ON" if STRUM_MODE else "STRUM:OFF"
                auto_txt = "AUTO:ON" if AUTO_PLAY_ENABLED else "AUTO:OFF"
                filter_txt = "FILTER:ON" if FILTER_MODE else "FILTER:OFF"
                step_txt = f"STEP:{step_cursor}/{len(step_song)}" if STEP_MODE else "STEP:OFF"
                status_line = f"[{CURRENT_KEY}] | {wave_txt} | {filter_txt} | {strum_txt} | {step_txt} | {auto_txt} | TH:{PINCH_THRESHOLD:.2f}"
                cv2.putText(image, status_line, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

                # 手ごとの発音状況（H1 = MIDI ch1, H2 = ch2 ...）
                hand_txts = []
                for slot, st in enumerate(hand_states):
                    if st['active']:
                        lr = 'L' if st['label'] == 'Left' else 'R'
                        hand_txts.append(f"H{slot + 1}({lr}):{midi_to_note_name(st['note'])}")
                cv2.putText(image, "  ".join(hand_txts) if hand_txts else "No hands", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

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

            # 内蔵シンセの出力先
            audio_txt = internal_synth.get_device_name() if internal_synth is not None else "OFF"
            put_text_jp(image, f"Audio Out [O]: {audio_txt}", (40, 52), 14, (180, 255, 180))

            # 右上に現在の合成波形プレビュー
            draw_waveform_preview(image, w - 195, 10, 185, 75)

            # スケール選択ボタン (左列 1〜3)
            draw_button(image, "1. C Major", 40, 80, 250, 40, CURRENT_KEY == 'C_major')
            draw_button(image, "2. A Minor", 40, 135, 250, 40, CURRENT_KEY == 'A_minor')
            draw_button(image, "3. G Major", 40, 190, 250, 40, CURRENT_KEY == 'G_major')

            # スケール選択ボタン (右列 4〜6)
            draw_button(image, "4. Pentatonic Minor", 320, 80, 280, 40, CURRENT_KEY == 'A_pentatonic_minor')
            draw_button(image, "5. Ryukyu Scale", 320, 135, 280, 40, CURRENT_KEY == 'Ryukyu')
            draw_button(image, "6. Miyakobushi Scale", 320, 190, 280, 40, CURRENT_KEY == 'Miyakobushi')

            # モード切替トグルボタン (上段)
            wave_name = internal_synth.get_waveform_name() if internal_synth else "Default"
            draw_button(image, f"VOICE [W]: {wave_name}", 40, 250, 320, 45, True, active_color=(0, 255, 255))
            draw_button(image, f"STRUM: {'ON' if STRUM_MODE else 'OFF'}", 380, 250, 140, 45, STRUM_MODE)
            draw_button(image, f"AUTO: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}", 535, 250, 140, 45, AUTO_PLAY_ENABLED)

            # 追加トグルボタン (中段)
            draw_button(image, f"DEBUG: {'ON' if SHOW_DEBUG else 'OFF'}", 40, 310, 160, 40, SHOW_DEBUG)
            draw_button(image, f"RAINBOW: {'ON' if RAINBOW_LANES else 'OFF'}", 220, 310, 160, 40, RAINBOW_LANES)
            draw_button(image, f"FILTER: {'ON' if FILTER_MODE else 'OFF'}", 400, 310, 160, 40, FILTER_MODE)

            # 自動演奏ソング選択コントロール (下段)
            cur_song_title = auto_player.get_current_title()
            put_text_jp(image, f"AutoSong: {cur_song_title}", (40, 358), 16, (255, 255, 255))
            draw_button(image, "< Prev [P]", 40, 385, 110, 40, False, active_color=(0, 255, 255))
            draw_button(image, "Next [N] >", 165, 385, 110, 40, False, active_color=(0, 255, 255))
            draw_button(image, "Rescan [S]", 290, 385, 130, 40, False, active_color=(0, 200, 255))
            draw_button(image, f"STEP [T]: {'ON' if STEP_MODE else 'OFF'}", 435, 385, 145, 40, STEP_MODE, active_color=(255, 150, 255))

            # 指の狭さ（ピンチ判定しきい値）コントロール (下段)
            now_txt = " ".join(f"H{i + 1}:{int(st['pinch'] * 100)}%" for i, st in enumerate(hand_states) if st['active']) or "-"
            put_text_jp(image, f"Pinch Threshold: {PINCH_THRESHOLD:.2f} (Now {now_txt})", (40, 442), 15, (255, 255, 255))
            draw_button(image, "[-] Down", 320, 435, 120, 38, False, active_color=(255, 200, 0))
            draw_button(image, "[+] Up", 460, 435, 120, 38, False, active_color=(255, 200, 0))

            # MIDIコントローラー操作ガイド表示 (最下段)
            guide_y = 485
            put_text_jp(image, "【MIDIコントローラー機能割当】", (40, guide_y), 15, (0, 255, 200))
            btn_guide = "ボタン: SW1:音階 | SW2:和音 | SW3:ストラム | SW4:オクターブ | SW5:自動演奏 | SW6/7:曲送り戻し | SW8:虹色"
            knob_guide = "つまみ: RV1:波形選択 | RV2:音の明るさ(Cutoff) | RV3:余韻(Release) | RV4:音量(Volume)"
            put_text_jp(image, btn_guide, (40, guide_y + 24), 13, (220, 220, 220))
            put_text_jp(image, knob_guide, (40, guide_y + 44), 13, (220, 220, 220))

        # ------------------------------------
        # 手の描画処理（演奏画面のときのみ表示）
        # ------------------------------------
        if UI_STATE == 'PLAY' and detection_result.hand_landmarks and detection_result.handedness:
            for hand_landmarks, slot in zip(detection_result.hand_landmarks, frame_slots):
                if slot is None:
                    continue
                state = hand_states[slot]

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

                if STEP_MODE:
                    is_active_sound = bool(state['step_notes'])
                elif STRUM_MODE:
                    is_active_sound = bool(state['notes'])
                else:
                    is_active_sound = state['note'] is not None

                if SHOW_DEBUG:
                    # デバッグ時：発音中は緑、無音は赤の円（当たり判定の確認用）
                    color = (0, 255, 0) if is_active_sound else (0, 0, 255)
                    cv2.circle(image, (idx_x, idx_y), 15, color, -1)
                    cv2.putText(image, f"Pinch:{pinch_pct}%", (idx_x + 20, idx_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)

                    if STEP_MODE:
                        if state['step_notes']:
                            notes_str = ", ".join([midi_to_note_name(n) for n in sorted(state['step_notes'])])
                            cv2.putText(image, f"STEP: {notes_str}", (idx_x + 20, idx_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 150, 255), 2)
                    elif STRUM_MODE:
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

                # フィルターモード中は、マーカーの下に音の明るさメーター（暗い青 → 明るい黄）を表示
                if FILTER_MODE and state['cutoff_smooth'] is not None:
                    bar_w, bar_h = 50, 6
                    bx, by = idx_x - bar_w // 2, idx_y + 22
                    fill = int(bar_w * state['cutoff_smooth'])
                    v = state['cutoff_smooth']
                    bar_color = (int(200 * (1 - v)), int(80 + 175 * v), int(255 * v))  # BGR
                    cv2.rectangle(image, (bx, by), (bx + bar_w, by + bar_h), (40, 40, 40), -1)
                    cv2.rectangle(image, (bx, by), (bx + fill, by + bar_h), bar_color, -1)
                    cv2.rectangle(image, (bx, by), (bx + bar_w, by + bar_h), (255, 255, 255), 1)

        # 映像を表示（黒帯なしでウィンドウ・全画面に完全追従）
        cv2.imshow('Theremin Camera', image)

        key = cv2.waitKey(5)

        # ×ボタンでウィンドウが閉じられたら終了（そのままだと次の imshow でウィンドウが復活して閉じられない）
        if cv2.getWindowProperty('Theremin Camera', cv2.WND_PROP_VISIBLE) < 1:
            break

        if key != -1:
            key = key & 0xFF
            if key == 27:
                break
            elif key == ord('1'):
                CURRENT_KEY = 'C_major'
                show_toast("【音階切替】ハ長調 (C Major)")
            elif key == ord('2'):
                CURRENT_KEY = 'A_minor'
                show_toast("【音階切替】イ短調 (A Minor)")
            elif key == ord('3'):
                CURRENT_KEY = 'G_major'
                show_toast("【音階切替】ト長調 (G Major)")
            elif key == ord('4'):
                CURRENT_KEY = 'A_pentatonic_minor'
                show_toast("【音階切替】ペンタトニック・マイナー")
            elif key == ord('5'):
                CURRENT_KEY = 'Ryukyu'
                show_toast("【音階切替】琉球音階 (Ryukyu)")
            elif key == ord('6'):
                CURRENT_KEY = 'Miyakobushi'
                show_toast("【音階切替】都節音階 (Miyakobushi)")
            elif key == ord('7'):
                CURRENT_KEY = 'D_major'
                show_toast("【音階切替】ニ長調 (D Major / カノン)")
            elif key == ord('w'):
                if internal_synth is not None:
                    internal_synth.next_waveform()
                    show_toast(f"【波形切替】{internal_synth.get_waveform_name()}")
                else:
                    WAVE_MODE = not WAVE_MODE
                    show_toast(f"【切替】波形モード: {'ON' if WAVE_MODE else 'OFF'}")
            elif key == ord('o'):
                cycle_octave()
            elif key == ord('h'):
                STRUM_MODE = not STRUM_MODE
                show_toast(f"【切替】和音ストラム奏法: {'ON' if STRUM_MODE else 'OFF'}")
            elif key == ord('m'):
                AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                if not AUTO_PLAY_ENABLED:
                    auto_player.pause()
                show_toast(f"【切替】自動演奏機能: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}")
            elif key == ord('d'):
                SHOW_DEBUG = not SHOW_DEBUG
                show_toast(f"【切替】デバッグ表示: {'ON' if SHOW_DEBUG else 'OFF'}")
            elif key == ord('r'):
                RAINBOW_LANES = not RAINBOW_LANES
                show_toast(f"【切替】虹色レーン: {'ON' if RAINBOW_LANES else 'OFF'}")
            elif key == ord('n') or key == ord('['):
                auto_player.next_song()
                show_toast(f"【次曲】{auto_player.get_current_title()}")
            elif key == ord('p') or key == ord(']'):
                auto_player.prev_song()
                show_toast(f"【前曲】{auto_player.get_current_title()}")
            elif key == ord('s'):
                auto_player.scan_songs()
                show_toast("【曲スキャン】songs/ を再スキャンしました")
            elif key == ord('-') or key == ord('_'):
                PINCH_THRESHOLD = round(max(0.20, PINCH_THRESHOLD - 0.05), 2)
                show_toast(f"【感度調整】ピンチしきい値: {PINCH_THRESHOLD:.2f}")
            elif key == ord('=') or key == ord('+'):
                PINCH_THRESHOLD = round(min(0.95, PINCH_THRESHOLD + 0.05), 2)
                show_toast(f"【感度調整】ピンチしきい値: {PINCH_THRESHOLD:.2f}")
            elif key == ord('c'):
                toggle_filter_mode()
            elif key == ord('t'):
                toggle_step_mode()
            elif key == ord('b'):
                for st in hand_states:
                    stop_hand_notes(st)
                step_cursor = 0
                step_toast_at = time.time()
                show_toast("【ステップ演奏】曲の頭に戻りました")
            elif key == ord('o'):
                if internal_synth is not None:
                    dev_name = internal_synth.cycle_device()
                    show_toast(f"【音声出力】{dev_name}")
            elif key == ord('f'):
                FULLSCREEN = not FULLSCREEN
                apply_window_mode('Theremin Camera', FULLSCREEN)
                show_toast(f"【全画面表示】{'ON' if FULLSCREEN else 'OFF'}")

            elif key == 9:  # Tabキーのキーコード (9)
                UI_STATE = 'SETTINGS' if UI_STATE == 'PLAY' else 'PLAY'
                show_toast(f"【画面切替】{UI_STATE} モード")

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
    for state in hand_states:
        release_hand(state)

    # 内部シンセサイザーの完全停止
    if internal_synth is not None:
        internal_synth.all_notes_off()
        internal_synth.close()

    if inport is not None:
        inport.close()
    detector.close()
    print("終了しました。")
