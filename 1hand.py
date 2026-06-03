import cv2
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
# current_notes = {'Right':None, 'Left':None}
current_note = None

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

    # ==========================================
    # 4. 手の位置から音程を計算してMIDI送信
    # ==========================================
    if detection_result.hand_landmarks:
        # 画面に映っている1つ目の手（インデックス0）だけを取得
        hand_landmarks = detection_result.hand_landmarks[0]
        
        # 人差し指の先端（8番）のY座標を取得
        y = hand_landmarks[8].y
        
        if CHORD_MODE:
            # 和音（ALLOWED_NOTES）のリストから手の高さに応じて音を選ぶ
            index = int((1.0 - y) * len(ALLOWED_NOTES))
            index = max(0, min(len(ALLOWED_NOTES) - 1, index))
            note = ALLOWED_NOTES[index]
        else:
            # 無制限モード：Y座標をMIDIノートナンバー (C4(60) ~ C6(84)) に変換
            note = int((1.0 - y) * 24) + 60 
            note = max(0, min(127, note))
        
        # 音程が変わった時だけMIDI信号を送信
        if note != current_note:
            if current_note is not None:
                outport.send(mido.Message('note_off', note=current_note))
            outport.send(mido.Message('note_on', note=note, velocity=100))
            current_note = note
    else:
        # 手が画面から消えたら音を止める
        if current_note is not None:
             outport.send(mido.Message('note_off', note=current_note))
             current_note = None
    # ==========================================
    # 5. カメラ映像の表示
    # ==========================================
    cv2.imshow('Theremin Camera', image)
    if cv2.waitKey(5) & 0xFF == 27: # Escキーで終了
        break

# 終了処理
cap.release()
cv2.destroyAllWindows()
if current_note is not None:
    outport.send(mido.Message('note_off', note=current_note))
outport.close()
detector.close()
