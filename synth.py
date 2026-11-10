# ------------------------------------------------------------------
# synth.py - tElemin 高音質・超低遅延ポリフォニック・ソフトウェアシンセサイザー
#   sounddevice + numpy によるリアルタイムストリーミング合成
#   波形: 無段階モーフィング (Sine ~ Triangle ~ Saw ~ Square ~ Theremin ~ Bell)
#   音色操作: 波形モーフィング(RV1), 明るさCutoff(RV2), 余韻Release(RV3), マスター音量(RV4)
# ------------------------------------------------------------------
import numpy as np
import sounddevice as sd
import threading
import time
import math

# 波形インデックス定数
WAVE_SINE = 0
WAVE_TRIANGLE = 1
WAVE_SAWTOOTH = 2
WAVE_SQUARE = 3
WAVE_THEREMIN = 4
WAVE_BELL = 5

WAVE_NAMES = [
    "Sine (純音・テルミン)",
    "Triangle (三角波・フルート)",
    "Sawtooth (ノコギリ波・弦楽器)",
    "Square (矩形波・8bitリード)",
    "Vintage Theremin (真空管テルミン)",
    "Bell / Pluck (ベル・ハープ)"
]

WAVE_SHORT_NAMES = [
    "Sine",
    "Triangle",
    "Sawtooth",
    "Square",
    "Theremin",
    "Bell"
]

def midi_to_freq(note):
    """MIDIノート番号（浮動小数点も可）を周波数(Hz)に変換"""
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))

def generate_single_waveform(wave_idx, phases):
    """単一の基本波形を位相アレイ (0〜2π) から生成"""
    if wave_idx == WAVE_SINE:
        return np.sin(phases)
    elif wave_idx == WAVE_TRIANGLE:
        # 三角波: -1.0 〜 +1.0
        return 2.0 * np.abs(2.0 * (phases / (2.0 * np.pi) - np.floor(phases / (2.0 * np.pi) + 0.5))) - 1.0
    elif wave_idx == WAVE_SAWTOOTH:
        # ノコギリ波: -1.0 〜 +1.0
        return 2.0 * (phases / (2.0 * np.pi) - np.floor(phases / (2.0 * np.pi) + 0.5))
    elif wave_idx == WAVE_SQUARE:
        # 矩形波
        return np.where(phases < np.pi, 0.75, -0.75)
    elif wave_idx == WAVE_THEREMIN:
        # 真空管テルミン（基本波＋わずかな2次・3次倍音＋温かみサチュレーション）
        s_fund = np.sin(phases)
        s_h2 = 0.22 * np.sin(2.0 * phases)
        s_h3 = 0.10 * np.sin(3.0 * phases)
        raw = s_fund + s_h2 + s_h3
        return np.tanh(raw * 1.3) * 0.85
    elif wave_idx == WAVE_BELL:
        # ベル/プラック（基音＋非調和倍音）
        s1 = np.sin(phases)
        s2 = 0.4 * np.sin(phases * 2.76)
        s3 = 0.2 * np.sin(phases * 5.40)
        return (s1 + s2 + s3) * 0.65
    return np.sin(phases)

def generate_morphed_waveform(morph_pos, phases):
    """波形の滑らかな連続モーフィング（無段階クロスフェード補間）
    morph_pos: 0.0 (Sine) 〜 1.0 (Triangle) 〜 2.0 (Saw) 〜 3.0 (Square) 〜 4.0 (Theremin) 〜 5.0 (Bell)
    """
    max_idx = len(WAVE_NAMES) - 1
    m = max(0.0, min(float(max_idx), float(morph_pos)))
    idx0 = int(math.floor(m))
    idx1 = min(max_idx, idx0 + 1)
    frac = m - idx0

    if frac < 0.005 or idx0 == idx1:
        return generate_single_waveform(idx0, phases)

    w0 = generate_single_waveform(idx0, phases)
    w1 = generate_single_waveform(idx1, phases)
    return w0 * (1.0 - frac) + w1 * frac

def get_morph_description(morph_pos):
    """現在の波形モーフィング位置の文字列表現"""
    max_idx = len(WAVE_NAMES) - 1
    m = max(0.0, min(float(max_idx), float(morph_pos)))
    idx0 = int(math.floor(m))
    idx1 = min(max_idx, idx0 + 1)
    frac = m - idx0

    if frac < 0.05 or idx0 == idx1:
        return WAVE_NAMES[idx0]
    elif frac > 0.95:
        return WAVE_NAMES[idx1]
    else:
        pct = int(round(frac * 100))
        return f"{WAVE_SHORT_NAMES[idx0]} → {WAVE_SHORT_NAMES[idx1]} ({pct}%)"


class Voice:
    """単一の発音ボイス"""
    def __init__(self, voice_id, sample_rate=44100):
        self.voice_id = voice_id
        self.sample_rate = sample_rate
        self.active = False
        self.target_freq = 440.0
        self.current_freq = 440.0
        self.phase = 0.0
        self.velocity = 0.8
        self.gate = False
        self.channel = 0
        self.note = 60
        self.age = 0.0

        # ADSR (秒)
        self.attack_time = 0.015
        self.decay_time = 0.1
        self.sustain_level = 0.85
        self.release_time = 0.08

        # 状態: 0=OFF, 1=ATTACK, 2=DECAY, 3=SUSTAIN, 4=RELEASE
        self.env_state = 0
        self.env_val = 0.0

    def note_on(self, freq, velocity, channel=0, note=60):
        self.target_freq = freq
        if not self.active:
            self.current_freq = freq
            self.phase = 0.0
            self.env_val = 0.0
        self.velocity = max(0.05, min(1.0, float(velocity) / 127.0 if velocity > 1.0 else float(velocity)))
        self.channel = channel
        self.note = note
        self.gate = True
        self.active = True
        self.env_state = 1  # ATTACK
        self.age = 0.0

    def set_freq(self, freq):
        """テルミンの手の動きによる連続ピッチ変更"""
        self.target_freq = freq

    def note_off(self):
        self.gate = False
        self.env_state = 4  # RELEASE


class InternalSynth:
    """ポリフォニック・ソフトウェアシンセサイザー (無段階波形モーフィング対応)"""
    def __init__(self, sample_rate=44100, blocksize=256, max_voices=24):
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.max_voices = max_voices
        self.waveform_morph = 4.0   # 0.0〜5.0 (初期値: Vintage Theremin)
        self.master_volume = 0.80   # 0.0〜1.0 (初期値: 80%)
        self.cutoff = 1.0          # 0.05〜1.0 (音の明るさ / LPF)
        self.release_time = 0.08   # 0.02〜1.2秒 (余韻)
        self.pan = 0.5             # 0.0 (左) 〜 1.0 (右)
        self.enabled = True

        self.voices = [Voice(i, sample_rate) for i in range(max_voices)]
        self.lock = threading.Lock()

        # ハンド追跡用ボイス辞書 (slot_index -> voice)
        self.hand_voices = {}

        # 連続時間
        self.time_sec = 0.0

        # sounddevice 出力ストリーム
        self.stream = None
        self._start_stream()

    def _start_stream(self):
        try:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.blocksize,
                channels=2,
                dtype='float32',
                callback=self._audio_callback,
                latency='low'
            )
            self.stream.start()
            print(f"[InternalSynth] 内蔵シンセサイザー開始 (SR: {self.sample_rate}Hz, Buffer: {self.blocksize})")
        except Exception as e:
            print(f"[InternalSynth] オーディオデバイス初期化エラー: {e}")
            self.stream = None

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # ==========================================
    #  音色パラメータ制御 API
    # ==========================================
    def set_waveform_morph(self, val):
        """波形モーフィング位置の設定 (0.0〜5.0 または 0〜127)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 5.0:
                self.waveform_morph = max(0.0, min(5.0, (float(val) / 127.0) * 5.0))
            else:
                self.waveform_morph = max(0.0, min(5.0, float(val)))
            return get_morph_description(self.waveform_morph)

    def set_waveform_by_midi(self, midi_val):
        """MIDIつまみ値 (0〜127) から波形を無段階モーフィング"""
        return self.set_waveform_morph(midi_val)

    def set_waveform(self, wave_type):
        """ステップ切り替え (互換性用)"""
        with self.lock:
            self.waveform_morph = float(max(0, min(5, int(wave_type))))
            return get_morph_description(self.waveform_morph)

    def next_waveform(self):
        """次の基本波形へステップ進める"""
        with self.lock:
            cur_idx = int(round(self.waveform_morph))
            next_idx = (cur_idx + 1) % len(WAVE_NAMES)
            self.waveform_morph = float(next_idx)
            return get_morph_description(self.waveform_morph)

    def get_waveform_name(self):
        """現在の波形名を取得"""
        with self.lock:
            return get_morph_description(self.waveform_morph)

    def get_waveform_preview(self, num_samples=100):
        """画面右上HUDプレビュー描画用の波形配列 (-1.0〜1.0) を生成"""
        with self.lock:
            morph = self.waveform_morph
            cutoff_val = self.cutoff

        # 1.5周期分 (0 〜 3π) の位相
        phases = np.linspace(0, 3.0 * np.pi, num_samples, endpoint=False, dtype=np.float32)
        wave = generate_morphed_waveform(morph, phases)

        # Cutoffフィルターの視覚的反映
        if cutoff_val < 0.95:
            k_size = int((1.0 - cutoff_val) * 8.0) + 1
            if k_size > 1:
                kernel = np.ones(k_size, dtype=np.float32) / k_size
                filtered = np.convolve(wave, kernel, mode='same')
                blend = 1.0 - cutoff_val
                wave = wave * (1.0 - blend) + filtered * blend
        return wave

    def set_master_volume(self, val):
        """マスター音量 (0.0〜1.0 または 0〜127)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                self.master_volume = max(0.0, min(1.0, float(val) / 127.0))
            else:
                self.master_volume = max(0.0, min(1.0, float(val)))
            return int(round(self.master_volume * 100))

    def set_cutoff(self, val):
        """フィルターカットオフ / 音の明るさ (0.0〜1.0 または 0〜127)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                self.cutoff = max(0.05, min(1.0, float(val) / 127.0))
            else:
                self.cutoff = max(0.05, min(1.0, float(val)))
            return int(round(self.cutoff * 100))

    def set_release_time(self, val):
        """余韻・リリース時間 (0.02秒〜1.2秒)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                norm = max(0.0, min(1.0, float(val) / 127.0))
            else:
                norm = max(0.0, min(1.0, float(val)))
            # 指数カーブで 0.02秒 〜 1.2秒
            self.release_time = 0.02 + (norm ** 2) * 1.18
            for v in self.voices:
                v.release_time = self.release_time
            return round(self.release_time, 2)

    def set_pan(self, val):
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                self.pan = max(0.0, min(1.0, float(val) / 127.0))
            else:
                self.pan = max(0.0, min(1.0, float(val)))

    # ==========================================
    #  ノート発音・制御 API
    # ==========================================
    def note_on(self, note, velocity=100, channel=0):
        """MIDIノート番号で発音"""
        freq = midi_to_freq(note)
        with self.lock:
            # 既に同じ (channel, note) を鳴らしているボイスがあれば再トリガー
            for v in self.voices:
                if v.active and v.channel == channel and v.note == note:
                    v.release_time = self.release_time
                    v.note_on(freq, velocity, channel, note)
                    return

            # 空いているボイスを探す
            for v in self.voices:
                if not v.active:
                    v.release_time = self.release_time
                    v.note_on(freq, velocity, channel, note)
                    return

            # 空きが無ければ最も古いリリース中または古いボイスを奪う
            oldest_v = min(self.voices, key=lambda x: (x.env_state != 4, x.age))
            oldest_v.release_time = self.release_time
            oldest_v.note_on(freq, velocity, channel, note)

    def note_off(self, note, channel=0):
        """MIDIノート番号で消音"""
        with self.lock:
            for v in self.voices:
                if v.active and v.channel == channel and v.note == note:
                    v.note_off()

    def all_notes_off(self):
        """全音消音"""
        with self.lock:
            for v in self.voices:
                if v.active:
                    v.note_off()
            self.hand_voices.clear()

    # ==========================================
    #  テルミン専用ハンド追従 API
    # ==========================================
    def set_hand_pitch(self, slot_idx, freq_or_note, velocity=0.85):
        """テルミンの手の動きによる連続ピッチ発音・追従"""
        freq = midi_to_freq(freq_or_note) if freq_or_note < 130 else freq_or_note
        with self.lock:
            if slot_idx in self.hand_voices:
                v = self.hand_voices[slot_idx]
                if v.active and v.env_state != 4:
                    v.set_freq(freq)
                    v.velocity = max(0.1, min(1.0, velocity))
                    return

            # 新規割り当て
            for v in self.voices:
                if not v.active:
                    v.release_time = self.release_time
                    v.note_on(freq, velocity, channel=slot_idx, note=int(round(freq_or_note)))
                    self.hand_voices[slot_idx] = v
                    return

            oldest_v = min(self.voices, key=lambda x: (x.env_state != 4, x.age))
            oldest_v.release_time = self.release_time
            oldest_v.note_on(freq, velocity, channel=slot_idx, note=int(round(freq_or_note)))
            self.hand_voices[slot_idx] = oldest_v

    def release_hand(self, slot_idx):
        """手が画面外に出た/離れたときの消音"""
        with self.lock:
            if slot_idx in self.hand_voices:
                v = self.hand_voices.pop(slot_idx)
                v.note_off()

    # ==========================================
    #  リアルタイム波形合成コールバック (sounddevice)
    # ==========================================
    def _audio_callback(self, outdata, frames, time_info, status):
        if not self.enabled:
            outdata.fill(0)
            return

        dt = 1.0 / self.sample_rate
        sample_times = np.arange(frames, dtype=np.float32) * dt
        mixed_buffer = np.zeros(frames, dtype=np.float32)

        with self.lock:
            morph_pos = self.waveform_morph
            master_vol = self.master_volume
            cutoff_val = self.cutoff
            pan = self.pan

            for v in self.voices:
                if not v.active:
                    continue

                v.age += frames * dt

                # ポルタメント（周波数スムーズ追従）
                smooth_factor = 0.15
                target_f = v.target_freq
                cur_f = v.current_freq + (target_f - v.current_freq) * smooth_factor
                v.current_freq = cur_f

                # 位相アレイ生成
                d_phase = 2.0 * np.pi * cur_f * dt
                phases = (v.phase + np.arange(frames, dtype=np.float32) * d_phase) % (2.0 * np.pi)
                v.phase = (v.phase + frames * d_phase) % (2.0 * np.pi)

                # 滑らかな連続モーフィング波形の合成
                sample = generate_morphed_waveform(morph_pos, phases)

                # ベル成分が混ざっている場合は自然な減衰をブレンド
                if morph_pos > 4.0:
                    bell_weight = morph_pos - 4.0
                    decay = np.exp(-v.age * 3.5)
                    sample = sample * (1.0 - bell_weight + bell_weight * decay)

                # ADSR エンベロープ計算
                env = np.empty(frames, dtype=np.float32)
                for i in range(frames):
                    if v.env_state == 1:  # ATTACK
                        v.env_val += (1.0 / (v.attack_time * self.sample_rate))
                        if v.env_val >= 1.0:
                            v.env_val = 1.0
                            v.env_state = 2  # DECAY
                    elif v.env_state == 2:  # DECAY
                        v.env_val -= ((1.0 - v.sustain_level) / (v.decay_time * self.sample_rate))
                        if v.env_val <= v.sustain_level:
                            v.env_val = v.sustain_level
                            v.env_state = 3  # SUSTAIN
                    elif v.env_state == 3:  # SUSTAIN
                        v.env_val = v.sustain_level
                    elif v.env_state == 4:  # RELEASE
                        v.env_val -= (v.sustain_level / (v.release_time * self.sample_rate))
                        if v.env_val <= 0.0:
                            v.env_val = 0.0
                            v.env_state = 0
                            v.active = False
                            break
                    else:
                        v.env_val = 0.0
                        v.active = False
                        break
                    env[i] = v.env_val

                if v.active:
                    mixed_buffer += sample * env * v.velocity

            self.time_sec += frames * dt

        # ------------------------------------------
        # 音の明るさ (Cutoff Low-Pass フィルター)
        # ------------------------------------------
        if cutoff_val < 0.95:
            k_size = int((1.0 - cutoff_val) * 8.0) + 1
            if k_size > 1:
                kernel = np.ones(k_size, dtype=np.float32) / k_size
                filtered = np.convolve(mixed_buffer, kernel, mode='same')
                blend = 1.0 - cutoff_val
                mixed_buffer = mixed_buffer * (1.0 - blend) + filtered * blend

        # ------------------------------------------
        # 出力リミッター ＆ マスターボリューム
        # ------------------------------------------
        # 1. まずソフトクリッピング（音割れ防止）
        limited = np.tanh(mixed_buffer * 0.85)

        # 2. マスターボリューム適用（知覚音量に合わせた2乗カーブ）
        vol_gain = (master_vol ** 2)
        output_mono = limited * vol_gain

        # ステレオパンニング
        left_gain = math.cos(pan * math.pi * 0.5)
        right_gain = math.sin(pan * math.pi * 0.5)

        outdata[:, 0] = output_mono * left_gain
        outdata[:, 1] = output_mono * right_gain
