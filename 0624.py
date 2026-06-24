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

    # 【今後の拡張用】手首(
