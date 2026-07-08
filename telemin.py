import cv2
import math
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido
import threading
import time
import os

# =======
# 和音や音階リストを定義する配列
# =======

CHORD_MODE = True
ALLOWED_NOTES = [48, 50, 52, 53, 55, 57, 59, 60]

KEY_PRESETS = {
    'C_major': [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67], # ハ長調（C, D, E, F, G, A, B...）
    'A_minor': [45, 47, 48, 50, 52, 53, 56, 57, 59, 60, 62, 64], # イ短調（A, B, C, D, E, F, G...）
    'G_major': [43, 45, 47, 48, 50, 52, 54, 55, 57, 59, 60, 62], # ト長調（Fが#する例: 54番）
    'A_pentatonic_minor': [45, 48, 50, 52, 55, 57, 60, 62, 64, 67, 69, 72], # 藍調/ロック（A, C, D, E, G...）
    'Ryukyu': [48, 52, 53, 55, 59, 60, 64, 65, 67, 71, 72, 76],             # 琉球音階（沖縄風: C, E, F, G, B...）
    'Miyakobushi': [50, 51, 55, 57, 58, 62, 63, 67, 69, 70, 74, 75]         # 都節音階（和風: D, Eb, G, A, Bb...）
}
CURRENT_KEY = 'C_major'

# HUDメニュー表示用定義
SCALE_MENU_ITEMS = [
    ('1', 'C_major', 'C Major (ハ長調)'),
    ('2', 'A_minor', 'A Minor (イ短調)'),
    ('3', 'G_major', 'G Major (ト長調)'),
    ('4', 'A_pentatonic_minor', 'Pentatonic (ペンタトニック)'),
    ('5', 'Ryukyu', 'Ryukyu (琉球音階)'),
    ('6', 'Miyakobushi', 'Miyakobushi (都節音階)')
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

# UIの状態管理 ('PLAY' = 演奏画面, 'SETTINGS' = 設定画面)
UI_STATE = 'PLAY'

# マウス操作用変数
mouse_x, mouse_y = -1, -1
mouse_clicked = False

def on_mouse(event, x, y, flags, param):
    global mouse_x, mouse_y, mouse_clicked
    if event == cv2.EVENT_LBUTTONDOWN:
        mouse_x, mouse_y = x, y
        mouse_clicked = True

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
    features['filter_cutoff'] = max(0.0, min(1.0, 1.0 - hand_landmarks[12].y)) # 中指の高さでカットオフ
    features['modulation_wheel'] = 0.0 # 今後実装
    features['resonance'] = 0.5        # 今後実装

    return features


# ==========================================
# YMO「ライディーン」自動生演奏用クラス
# ==========================================
RYDEEN_MELODY = [
    # (Note, Duration) の簡易シーケンス
    (67, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (74, 0.4), (71, 0.4),
    (69, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (67, 0.6),
    (0, 0.2),
    (67, 0.2), (67, 0.2), (69, 0.2), (71, 0.2), (74, 0.4), (76, 0.4),
    (79, 0.2), (76, 0.2), (74, 0.2), (71, 0.2), (74, 0.6), (0, 0.4)
]

class AutoPlayer:
    def __init__(self, outport):
        self.outport = outport
        self.running = False
        self.paused = True
        self.thread = None
        self.lock = threading.Lock()
        
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._play_loop, daemon=True)
        self.thread.start()
        
    def pause(self):
        with self.lock:
            if not self.paused:
                self.paused = True
                # 全チャンネル消音
                for ch in range(16):
                    self.outport.send(mido.Message('control_change', control=123, value=0, channel=ch))
                print("【自動演奏】一時停止（人が検知されました）")
                
    def resume(self):
        with self.lock:
            if self.paused:
                self.paused = False
                print("【自動演奏】再開（無人状態です）")
                
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def _play_loop(self):
        mid_file_path = 'rydeen.mid'
        
        while self.running:
            if os.path.exists(mid_file_path):
                try:
                    mid = mido.MidiFile(mid_file_path)
                    print("【自動演奏】rydeen.mid を再生中...")
                    for msg in mid.play():
                        if not self.running:
                            break
                        while self.paused and self.running:
                            time.sleep(0.1)
                        if not self.paused:
                            self.outport.send(msg)
                except Exception as e:
                    print(f"MIDIファイル再生エラー: {e}")
                    time.sleep(1)
            else:
                # 簡易メロディデータのループ再生
                for note, duration in RYDEEN_MELODY:
                    if not self.running:
                        break
                    
                    while self.paused and self.running:
                        time.sleep(0.1)
                        
                    if note > 0 and not self.paused:
                        # チャンネル3 (channel=2) を自動演奏で使用
                        self.outport.send(mido.Message('note_on', note=note, velocity=70, channel=2))
                        
                    steps = int(duration / 0.05)
                    for _ in range(steps):
                        if not self.running or self.paused:
                            break
                        time.sleep(0.05)
                        
                    if note > 0:
                        self.outport.send(mido.Message('note_off', note=note, channel=2))
                        
                    # 音の間の僅かな休符
                    time.sleep(0.02)


# ==========================================
# 1. AIモデルのセットアップ（Tasks API）
# ==========================================
base_options = python.BaseOptions(model_asset_path='hand_landmarker.task')
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=2, # 検出する手の数
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
    print(f"  - {port}")
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
cv2.namedWindow('Theremin Camera')
cv2.setMouseCallback('Theremin Camera', on_mouse)

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
    'Left':  {
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
                if is_in_rect(mouse_x, mouse_y, w - 130, 10, 120, 35):
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
                elif is_in_rect(mouse_x, mouse_y, 40, 300, 160, 50):
                    WAVE_MODE = not WAVE_MODE
                    print(f"【切替】波形モードが {'ON' if WAVE_MODE else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 220, 300, 160, 50):
                    STRUM_MODE = not STRUM_MODE
                    print(f"【切替】和音モードが {'ON' if STRUM_MODE else 'OFF'} になりました")
                elif is_in_rect(mouse_x, mouse_y, 400, 300, 160, 50):
                    AUTO_PLAY_ENABLED = not AUTO_PLAY_ENABLED
                    print(f"【切替】自動演奏機能が {'ON' if AUTO_PLAY_ENABLED else 'OFF'} になりました")
                    if not AUTO_PLAY_ENABLED:
                        auto_player.pause()

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
        # 手の位置から音程を計算してMIDI送信
        # ------------------------------------
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
        h, w, _ = image.shape

        if UI_STATE == 'PLAY':
            # ------------------------------------
            # 演奏画面 (PLAY)
            # ------------------------------------
            # 音程の境界線（薄いガイドライン）と音名表示の描画
            notes_in_scale = KEY_PRESETS[CURRENT_KEY] if CHORD_MODE else [60 + i for i in range(24)]
            N = len(notes_in_scale)
            for i in range(N):
                # 各音程レーンの上下境界を計算
                y_scaled_top = (N - 1 - i) / N
                y_scaled_bottom = (N - i) / N
                
                y_top = Y_MIN_LIMIT + y_scaled_top * (Y_MAX_LIMIT - Y_MIN_LIMIT)
                y_bottom = Y_MIN_LIMIT + y_scaled_bottom * (Y_MAX_LIMIT - Y_MIN_LIMIT)
                
                # レーンの境界線を描画（最上部/最下部を除く内側の線）
                if i < N - 1:
                    y_px = int(h * y_top)
                    cv2.line(image, (0, y_px), (w, y_px), (55, 55, 55), 1)
                
                # レーンの中央に音名（C4, D4等）を表示
                y_center_px = int(h * (y_top + y_bottom) / 2)
                note_val = notes_in_scale[i]
                note_name = midi_to_note_name(note_val)
                cv2.putText(image, note_name, (15, y_center_px + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (110, 110, 110), 1)

            # 有効演奏エリアの最上部・最下部の境界線
            cv2.line(image, (0, int(h * Y_MIN_LIMIT)), (w, int(h * Y_MIN_LIMIT)), (0, 0, 255), 2)
            cv2.line(image, (0, int(h * Y_MAX_LIMIT)), (w, int(h * Y_MAX_LIMIT)), (0, 0, 255), 2)
            
            # クリーン化：左上に1行でステータスを集約表示
            wave_txt = "WAVE:ON" if WAVE_MODE else "WAVE:OFF"
            strum_txt = "STRUM:ON" if STRUM_MODE else "STRUM:OFF"
            auto_txt = "AUTO:ON" if AUTO_PLAY_ENABLED else "AUTO:OFF"
            status_line = f"[{CURRENT_KEY}] | {wave_txt} | {strum_txt} | {auto_txt}"
            cv2.putText(image, status_line, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
            
            r_note = hand_states['Right']['note']
            l_note = hand_states['Left']['note']
            cv2.putText(image, f"R-Note: {midi_to_note_name(r_note)}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            cv2.putText(image, f"L-Note: {midi_to_note_name(l_note)}", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 2)

            # 右上にSettingsボタンを描画
            draw_button(image, "Settings [Tab]", w - 140, 10, 130, 35, False, active_color=(0, 255, 0))

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

            # モード切替トグルボタン (下部)
            draw_button(image, f"WAVE: {'ON' if WAVE_MODE else 'OFF'}", 40, 290, 160, 50, WAVE_MODE)
            draw_button(image, f"STRUM: {'ON' if STRUM_MODE else 'OFF'}", 220, 290, 160, 50, STRUM_MODE)
            draw_button(image, f"AUTO PLAY: {'ON' if AUTO_PLAY_ENABLED else 'OFF'}", 400, 290, 160, 50, AUTO_PLAY_ENABLED)

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
                
                # 和音モード中の「ひも（直線）」描画処理
                if STRUM_MODE and state['pinch_start_x'] is not None and state['pinch_start_y'] is not None:
                    start_px_x = int(state['pinch_start_x'] * w)
                    start_px_y = int(state['pinch_start_y'] * h)
                    # ひもをシアン (255, 255, 0) で描画
                    cv2.line(image, (start_px_x, start_px_y), (idx_x, idx_y), (255, 255, 0), 3)
                    # つまみ開始アンカーを赤い円で描画
                    cv2.circle(image, (start_px_x, start_px_y), 8, (0, 0, 255), -1)

                is_active_sound = (state['notes'] if STRUM_MODE else state['note'] is not None)
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
