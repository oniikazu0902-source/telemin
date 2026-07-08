import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import mido
import math  # 距離の計算に必要なので追加します

# ==========================================
# 1. AIモデルのセットアップ
# ==========================================
base_options = python.BaseOptions(model_asset_path='hand_landmarker.task')
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=1,
    min_hand_detection_confidence=0.7
)
detector = vision.HandLandmarker.create_from_options(options)

# ==========================================
# 2. MIDI出力ポートを開く
# ==========================================
try:
    # 先ほど成功したポート名に変更しています
    outport = mido.open_output('Default Basic App Loopback 1') 
except OSError:
    print("MIDIポートが見つかりません。")
    exit()

# ==========================================
# 3. カメラの準備
# ==========================================
cap = cv2.VideoCapture(0)
current_note = None
current_volume = None  # 現在の音量を記録する変数を追加

print("カメラに向かって手をかざしてください。Escキーで終了します。")

while cap.isOpened():
    success, image = cap.read()
    if not success:
        break

    image = cv2.flip(image, 1)
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
    detection_result = detector.detect(mp_image)

    # ==========================================
    # 4. 手の動きを音程と音量に変換
    # ==========================================
    if detection_result.hand_landmarks:
        hand_landmarks = detection_result.hand_landmarks[0]
        
        # ------------------------------------
        # 【新機能】手の開き具合で音量(CC7)を計算
        # ------------------------------------
        wrist = hand_landmarks[0]        # 手首の座標
        middle_tip = hand_landmarks[12]  # 中指の先端の座標
        
        # 手首と中指の先端の距離を計算（三平方の定理）
        distance = math.hypot(middle_tip.x - wrist.x, middle_tip.y - wrist.y)
        
        # 距離を0.0(グー) 〜 1.0(パー)の割合に変換
        # ※カメラとの距離によって数値が変わるため、反応が悪い場合はここを調整してください
        min_dist = 0.15 # グーにした時の大体の距離
        max_dist = 0.45 # パーにした時の大体の距離
        ratio = max(0.0, min(1.0, (distance - min_dist) / (max_dist - min_dist)))
        
        # 割合をMIDIのボリューム値 (0 〜 127) に変換
        volume = int(ratio * 127)
        
        # 音量が前回から変わった時だけ、MIDIのボリューム信号(CC#7)を送信
        if volume != current_volume:
            outport.send(mido.Message('control_change', control=7, value=volume))
            current_volume = volume

        # ------------------------------------
        # 従来通り、人差し指の高さで音程(ノート)を計算
        # ------------------------------------
        y = hand_landmarks[8].y 
        note = int((1.0 - y) * 24) + 60 
        
        if note != current_note:
            if current_note is not None:
                outport.send(mido.Message('note_off', note=current_note))
            # 音を鳴らす時の強さ(velocity)も、計算したボリュームに連動させます
            outport.send(mido.Message('note_on', note=note, velocity=volume))
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
    if cv2.waitKey(5) & 0xFF == 27:
        break

# 終了処理
cap.release()
cv2.destroyAllWindows()
if current_note is not None:
    outport.send(mido.Message('note_off', note=current_note))
outport.close()