import cv2
import math
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido

# =======
# 和音や音階リストを定義する配列
# =======

CHORD_MODE = True
# C3(48), E3(52), G3(55), C4(60), E4(64), G4(67), C5(72), E5(76), G5(79), C6(84)
# C3(48), D3(50), E3(52), F3(53), G3(55), A4(57), B4(59), C4(60)
ALLOWED_NOTES = [48, 50, 52, 53, 55, 57, 59, 60]

KEY_PRESETS = {
    'C_major': [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67], # ハ長調（C, D, E, F, G, A, B...）
    'A_minor': [45, 47, 48, 50, 52, 53, 56, 57, 59, 60, 62, 64], # イ短調（A, B, C, D, E, F, G...）
    'G_major': [43, 45, 47, 48, 50, 52, 54, 55, 57, 59, 60, 62]  # ト長調（Fが#する例: 54番）
}
CURRENT_KEY = 'C_major'

# =======
# 検出範囲・モードの設定
# =======
Y_MIN_LIMIT = 0.15  # これより上（0.0〜0.15）は無視
Y_MAX_LIMIT = 0.85  # これより下（0.85〜1.0）は無視

PINCH_THRESHOLD = 0.7  # つまみ判定のしきい値（0.0〜1.0）

# 【追加】波形変化モードのデフォルト状態（False = 変化しない）
WAVE_MODE = False

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

    return features

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
# 2. MIDI出力ポートを開く
# ==========================================
try:
    # 'loopMIDI Port' は環境に合わせて変更してください
    outport = mido.open_output('Default Basic App Loopback 1') 
except OSError:
    print("MIDIポートが見つかりません。loopMIDIが起動しているか確認してください。")
    exit()

# ==========================================
# 3. カメラの準備
# ==========================================
cap = cv2.VideoCapture(0)
hand_states = {
    'Right': {'note': None, 'channel': 0, 'active': False},
    'Left':  {'note': None, 'channel': 1, 'active': False}
}

print("カメラに向かって手をかざしてください。Escキーで終了します。")

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

    # ==========================================
    # 4. 手の位置から音程を計算してMIDI送信
    # ==========================================
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
            
            if CHORD_MODE:
                ALLOWED_NOTES = KEY_PRESETS[CURRENT_KEY]
                index = int((1.0 - y_scaled) * len(ALLOWED_NOTES))
                index = max(0, min(len(ALLOWED_NOTES) - 1, index))
                note = ALLOWED_NOTES[index]
            else:
                note = int((1.0 - y_scaled) * 24) + 60 
                note = max(0, min(127, note))

            cc_value = int(features['pinch'] * 127)
            cc_value = max(0, min(127, cc_value))
            
            x_cc_value = int(features['index_x'] * 127)
            x_cc_value = max(0, min(127, x_cc_value))

            outport.send(mido.Message('control_change', control=7, value=cc_value, channel=state['channel']))
            
            # 【追加】WAVE_MODEがTrueのときのみX座標（CC#16）を送信
            if WAVE_MODE:
                outport.send(mido.Message('control_change', control=16, value=x_cc_value, channel=state['channel']))

            # つまんでいるかどうかの判定
            is_pinching = features['pinch'] >= PINCH_THRESHOLD

            if is_pinching:
                if state['note'] is None:
                    outport.send(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                    state['note'] = note
            else:
                if state['note'] is not None:
                    outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
                    state['note'] = None

    for hand_type in ['Right', 'Left']:
        if hand_type not in current_detected_hands:
            state = hand_states[hand_type]
            if state['active']:
                if state['note'] is not None:
                    outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
                    state['note'] = None
                state['active'] = False

    # ==========================================
    # 5. カメラ映像の表示
    # ==========================================
    h, w, _ = image.shape
    
    cv2.line(image, (0, int(h * Y_MIN_LIMIT)), (w, int(h * Y_MIN_LIMIT)), (0, 0, 255), 2)
    cv2.line(image, (0, int(h * Y_MAX_LIMIT)), (w, int(h * Y_MAX_LIMIT)), (0, 0, 255), 2)
    
    cv2.putText(image, f"KEY: {CURRENT_KEY}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    
    r_note = hand_states['Right']['note']
    l_note = hand_states['Left']['note']
    cv2.putText(image, f"Right Hand Note: {r_note if r_note else 'OFF'}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.putText(image, f"Left Hand Note:  {l_note if l_note else 'OFF'}", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 100, 0), 2)

    # 【追加】波形モードのステータス表示
    wave_status_text = "ON" if WAVE_MODE else "OFF"
    cv2.putText(image, f"WAVE MODE (w): {wave_status_text}", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

    if detection_result.hand_landmarks and detection_result.handedness:
        for hand_landmarks, handedness in zip(detection_result.hand_landmarks, detection_result.handedness):
            hand_type = handedness[0].category_name
            state = hand_states[hand_type]
            
            # 親指(4番)の画面上のピクセル座標を計算
            idx_x = int(hand_landmarks[4].x * w)
            idx_y = int(hand_landmarks[4].y * h)
            
            features = extract_hand_features(hand_landmarks)
            pinch_pct = int(features['pinch'] * 100)
            
            color = (0, 255, 0) if state['note'] is not None else (0, 0, 255)
            cv2.circle(image, (idx_x, idx_y), 15, color, -1)
            
            cv2.putText(image, f"Pinch:{pinch_pct}%", (idx_x + 20, idx_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            if state['note'] is not None:
                cv2.putText(image, f"NOTE:{state['note']}", (idx_x + 20, idx_y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    cv2.imshow('Theremin Camera', image)
    
    key = cv2.waitKey(5)
    
    if key != -1: 
        key = key & 0xFF 
        
        if key == 27:  
            break
        elif key == ord('c'):  
            CURRENT_KEY = 'C_major'
            print("【切替】ハ長調 (C Major) になりました")
        elif key == ord('a'):  
            CURRENT_KEY = 'A_minor'
            print("【切替】イ短調 (A Minor) になりました")
        elif key == ord('g'):  
            CURRENT_KEY = 'G_major'
            print("【切替】ト長調 (G Major) になりました")
        elif key == ord('w'):  # 【追加】wキーで波形モード切替
            WAVE_MODE = not WAVE_MODE
            print(f"【切替】波形モードが {'ON' if WAVE_MODE else 'OFF'} になりました")

cap.release()
cv2.destroyAllWindows()
for hand_type in ['Right', 'Left']:
    state = hand_states[hand_type]
    if state['note'] is not None:
        outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
outport.close()
detector.close()
