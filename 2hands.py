import cv2
import math
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido

# =======
# 和音を定義する配列
# =======

CHORD_MODE = True
# C3(48), E3(52), G3(55), C4(60), E4(64), G4(67), C5(72), E5(76), G5(79), C6(84)
# C3(48), D3(50), E3(52), F3(53), G3(55), A4(57), B4(59), C4(60)
ALLOWED_NOTES = [48, 50, 52, 53, 55, 57, 59, 60]


# ==========================================
# わけわからん
# ==========================================
def extract_hand_features(hand_landmarks):
    """
    21個の関節データから、演奏に使うパラメータを計算して辞書で返す。
    """
    features = {}

    # 【音程用】人差し指の先端(8番)のY座標 (0.0=上, 1.0=下)
    features['index_y'] = hand_landmarks[8].y

    # 【音量・エフェクト用】親指(4番)と人差し指(8番)の距離（ピンチ具合）
    thumb = hand_landmarks[4]
    index = hand_landmarks[8]
    pinch_dist = math.hypot(thumb.x - index.x, thumb.y - index.y)
    features['pinch'] = min(1.0, pinch_dist * 3.0)

    # 【今後の拡張用】手首(0番)のY座標など
    features['wrist_y'] = hand_landmarks[0].y

    return features

# ==========================================
# 1. AIモデルのセットアップ（Tasks API）
# ==========================================
# 先ほどダウンロードした task ファイルを読み込みます
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
    # (loopMIDIアプリ内で作成したポート名です)
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
    if detection_result.hand_landmarks and detection_result.hand_handedness:
        for hand_landmarks, handedness in zip(detection_result.hand_landmarks, detection_result.hand_handedness):
            # IndexErrorを防ぐ安全な取り出し方
            hand_type = handedness[0].category_name
            current_detected_hands.append(hand_type)
            state = hand_states[hand_type]
            state['active'] = True
    
            features = extract_hand_features(hand_landmarks)
            
            # Y座標を取得
            # (一番上が0.0、下が1.0)
            y = features['index_y']
            
            if CHORD_MODE:
                # 和音だけ
                # Cメジャー
                index = int((1.0 - y) * len(ALLOWED_NOTES))
                # 範囲以外を防止する処理
                index = max(0, min(len(ALLOWED_NOTES) - 1, index))
                note = ALLOWED_NOTES[index]
            else:
                # Y座標をMIDIノートナンバー (例: C4(60) ~ C6(84)) に変換
                # 手を上にかざす(yが小さい)ほど音が高くなるように計算
                note = int((1.0 - y) * 24) + 60 
                note = max(0, min(127, note))

            cc_value = int(features['pinch'] * 127)
            cc_value = max(0, min(127, cc_value))

            # 💡【復活させた1行】音量(手の開き)のMIDI送信
            outport.send(mido.Message('control_change', control=7, value=cc_value, channel=state['channel']))
            
            # MIDI信号を送信
            if note != state['note']:
                if state['note'] is not None:
                    outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
                outport.send(mido.Message('note_on', note=note, velocity=100, channel=state['channel']))
                state['note'] = note

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
    cv2.imshow('Theremin Camera', image)
    if cv2.waitKey(5) & 0xFF == 27: # Escキーで終了
        break

# 終了処理
cap.release()
cv2.destroyAllWindows()
for hand_type in ['Right', 'Left']:
    state = hand_states[hand_type]
    if state['note'] is not None:
        outport.send(mido.Message('note_off', note=state['note'], channel=state['channel']))
outport.close()
detector.close()
