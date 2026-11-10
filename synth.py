# ==========================================
# tElemin 内蔵シンセサイザー (synth.py)
# ==========================================
# Vital などの外部シンセを使わずに、Python 自身で高音質・超低遅延で音を合成し、
# PC のオーディオ出力（イヤホン端子 / USB-DAC など）から直接鳴らします。
#
# 特徴:
#   1. 波形無段階モーフィング: Sine ⇄ Triangle ⇄ Sawtooth ⇄ Square ⇄ Theremin ⇄ Bell
#      アナログつまみ（RV1）で滑らかに音色が連続変化。
#   2. オシロスコープHUDプレビュー: 画面右上に現在の合成波形をリアルタイム描画可能。
#   3. 知覚マスター音量制御: オーディオテーパー（2乗カーブ）による自然な音量調整。
#   4. チャンネル別音量 & フィルター (CC#74/CC#71): 手の傾き(FILTER_MODE)や連弾に対応。
#   5. OutputHub: 内部シンセと外部MIDI出力ポートへの同時・透過的ルーティング。
#   6. デバイス切り替え: 'o' キーでオーディオ出力先を瞬時に変更。
# ==========================================
import threading
import numpy as np
import time
import math

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    from scipy.signal import lfilter
except ImportError:
    lfilter = None

# 波形定数
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
    """MIDIノート番号（浮動小数点可）を周波数(Hz)に変換"""
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))

def generate_single_waveform(wave_idx, phases):
    """単一の基本波形を位相アレイ (0〜2π) から生成"""
    if wave_idx == WAVE_SINE:
        return np.sin(phases)
    elif wave_idx == WAVE_TRIANGLE:
        return 2.0 * np.abs(2.0 * (phases / (2.0 * np.pi) - np.floor(phases / (2.0 * np.pi) + 0.5))) - 1.0
    elif wave_idx == WAVE_SAWTOOTH:
        return 2.0 * (phases / (2.0 * np.pi) - np.floor(phases / (2.0 * np.pi) + 0.5))
    elif wave_idx == WAVE_SQUARE:
        return np.where(phases < np.pi, 0.75, -0.75)
    elif wave_idx == WAVE_THEREMIN:
        s_fund = np.sin(phases)
        s_h2 = 0.22 * np.sin(2.0 * phases)
        s_h3 = 0.10 * np.sin(3.0 * phases)
        return np.tanh((s_fund + s_h2 + s_h3) * 1.3) * 0.85
    elif wave_idx == WAVE_BELL:
        s1 = np.sin(phases)
        s2 = 0.4 * np.sin(phases * 2.76)
        s3 = 0.2 * np.sin(phases * 5.40)
        return (s1 + s2 + s3) * 0.65
    return np.sin(phases)

def generate_morphed_waveform(morph_pos, phases):
    """波形の滑らかな連続モーフィング（無段階クロスフェード補間）"""
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
    """波形モーフィング位置の文字列表現"""
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


class _Voice:
    __slots__ = ('note', 'channel', 'velocity', 'phase', 'current_freq', 'target_freq', 'env_val', 'env_state', 'held', 'age')

    def __init__(self, note, channel, velocity, age, freq):
        self.note = note
        self.channel = channel
        self.velocity = velocity
        self.phase = 0.0
        self.current_freq = freq
        self.target_freq = freq
        self.env_val = 0.0
        self.env_state = 1  # 1=ATTACK, 2=DECAY, 3=SUSTAIN, 4=RELEASE
        self.held = True
        self.age = age


class SoftSynth:
    """高音質・超低遅延ポリフォニック・ソフトウェアシンセサイザー"""
    MAX_VOICES = 24
    ATTACK_SEC = 0.012
    DEFAULT_RELEASE_SEC = 0.12
    MASTER_GAIN = 0.85
    VIBRATO_HZ = 5.5

    CUTOFF_MIN_HZ = 180.0
    CUTOFF_MAX_HZ = 14000.0
    CUTOFF_SMOOTH = 0.35

    def __init__(self, device=None, blocksize=256, samplerate=44100):
        self.blocksize = blocksize
        self.samplerate = samplerate
        self.lock = threading.Lock()
        self.voices = {}  # (channel, note) -> _Voice
        self._age = 0

        # 音色・マスターパラメータ
        self.waveform_morph = 4.0   # 0.0〜5.0 (初期値: Vintage Theremin)
        self.master_volume = 0.80   # 0.0〜1.0 (初期値: 80%)
        self.cutoff = 1.0          # グローバルCutoff (0.05〜1.0)
        self.release_time = self.DEFAULT_RELEASE_SEC
        self.pan = 0.5
        self.enabled = True

        # チャンネル別パラメータ (16ch)
        self.ch_volume = [1.0] * 16
        self._ch_volume_now = list(self.ch_volume)
        self.ch_wave = [self.waveform_morph] * 16
        self.ch_bend = [0.0] * 16
        self.ch_mod = [0.0] * 16
        self.ch_cutoff = [1.0] * 16
        self._ch_cutoff_now = [1.0] * 16
        self.ch_reso = [0.0] * 16
        self._filter_zi = [np.zeros(2) for _ in range(16)]
        self._filter_coef = [None] * 16

        self._vib_phase = 0.0
        self.time_sec = 0.0
        self.device = device
        self.device_name = "Default"
        self.stream = None

        if sd is not None:
            self._start_stream()

    def _start_stream(self):
        if sd is None:
            return
        try:
            self.stream = sd.OutputStream(
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                channels=2,
                dtype='float32',
                callback=self._audio_callback,
                device=self.device,
                latency='low'
            )
            self.stream.start()
            dev_info = sd.query_devices(self.device, 'output') if self.device is not None else sd.query_devices(kind='output')
            self.device_name = dev_info['name']
            self.samplerate = int(self.stream.samplerate)
            print(f"[SoftSynth] 開始: {self.device_name} ({self.samplerate}Hz, Buffer: {self.blocksize})")
        except Exception as e:
            print(f"[SoftSynth] オーディオデバイス初期化エラー: {e}")
            self.stream = None

    def cycle_device(self):
        """オーディオ出力デバイスを次の候補へ切り替える ('o' キー)"""
        if sd is None:
            return "No sounddevice"
        try:
            devs = [i for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0]
            if not devs:
                return "No output device"
            cur_idx = devs.index(self.device) if self.device in devs else -1
            next_dev = devs[(cur_idx + 1) % len(devs)]
            self.close()
            self.device = next_dev
            self._start_stream()
            return self.device_name
        except Exception as e:
            return f"Error: {e}"

    def get_device_name(self):
        return self.device_name

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # ==========================================
    #  MIDI メッセージ受信インターフェース (send)
    # ==========================================
    def send(self, msg):
        """mido.Message を受け取ってシンセを直接駆動"""
        if msg.type == 'note_on':
            if msg.velocity > 0:
                self.note_on(msg.note, msg.velocity, channel=getattr(msg, 'channel', 0))
            else:
                self.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif msg.type == 'note_off':
            self.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif msg.type == 'pitchwheel':
            ch = getattr(msg, 'channel', 0)
            with self.lock:
                self.ch_bend[ch] = (msg.pitch / 8192.0) * 2.0
        elif msg.type == 'control_change':
            ch = getattr(msg, 'channel', 0)
            if msg.control == 1:  # モジュレーション (ビブラート)
                with self.lock:
                    self.ch_mod[ch] = msg.value / 127.0
            elif msg.control == 7:  # チャンネル音量
                with self.lock:
                    self.ch_volume[ch] = msg.value / 127.0
            elif msg.control == 16:  # CC16 波形
                self.set_waveform_by_midi(msg.value)
            elif msg.control == 74:  # フィルターカットオフ
                with self.lock:
                    self.ch_cutoff[ch] = msg.value / 127.0
                    self.cutoff = self.ch_cutoff[ch]
            elif msg.control == 71:  # レゾナンス
                with self.lock:
                    self.ch_reso[ch] = msg.value / 127.0
            elif msg.control == 73:  # リリース
                self.set_release_time(msg.value)
            elif msg.control in (120, 123):  # All Notes Off
                self.all_notes_off()

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
            for ch in range(16):
                self.ch_wave[ch] = self.waveform_morph
            return get_morph_description(self.waveform_morph)

    def set_waveform_by_midi(self, midi_val):
        return self.set_waveform_morph(midi_val)

    def set_waveform(self, wave_type):
        with self.lock:
            self.waveform_morph = float(max(0, min(5, int(wave_type))))
            for ch in range(16):
                self.ch_wave[ch] = self.waveform_morph
            return get_morph_description(self.waveform_morph)

    def next_waveform(self):
        with self.lock:
            cur_idx = int(round(self.waveform_morph))
            next_idx = (cur_idx + 1) % len(WAVE_NAMES)
            self.waveform_morph = float(next_idx)
            for ch in range(16):
                self.ch_wave[ch] = self.waveform_morph
            return get_morph_description(self.waveform_morph)

    def get_waveform_name(self):
        with self.lock:
            return get_morph_description(self.waveform_morph)

    def get_waveform_preview(self, num_samples=70):
        """画面右上HUDプレビュー描画用の波形配列 (-1.0〜1.0) を生成"""
        with self.lock:
            morph = self.waveform_morph
            cutoff_val = self.cutoff

        phases = np.linspace(0, 3.0 * np.pi, num_samples, endpoint=False, dtype=np.float32)
        wave = generate_morphed_waveform(morph, phases)

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
        """フィルターカットオフ (0.0〜1.0 または 0〜127)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                norm = max(0.05, min(1.0, float(val) / 127.0))
            else:
                norm = max(0.05, min(1.0, float(val)))
            self.cutoff = norm
            for ch in range(16):
                self.ch_cutoff[ch] = norm
            return int(round(norm * 100))

    def set_release_time(self, val):
        """余韻・リリース時間 (0.02秒〜1.2秒)"""
        with self.lock:
            if isinstance(val, (int, np.integer)) or val > 1.0:
                norm = max(0.0, min(1.0, float(val) / 127.0))
            else:
                norm = max(0.0, min(1.0, float(val)))
            self.release_time = 0.02 + (norm ** 2) * 1.18
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
        freq = midi_to_freq(note)
        vel = max(0.05, min(1.0, float(velocity) / 127.0 if velocity > 1.0 else float(velocity)))
        key = (channel, note)
        with self.lock:
            self._age += 1
            if key in self.voices:
                v = self.voices[key]
                v.velocity = vel
                v.target_freq = freq
                v.held = True
                v.env_state = 1
                v.age = self._age
                return

            if len(self.voices) >= self.MAX_VOICES:
                oldest_key = min(self.voices, key=lambda k: (self.voices[k].held, self.voices[k].age))
                del self.voices[oldest_key]

            self.voices[key] = _Voice(note, channel, vel, self._age, freq)

    def note_off(self, note, channel=0):
        key = (channel, note)
        with self.lock:
            if key in self.voices:
                v = self.voices[key]
                v.held = False
                v.env_state = 4  # RELEASE

    def all_notes_off(self):
        with self.lock:
            for v in self.voices.values():
                v.held = False
                v.env_state = 4

    # テルミン直接追従用
    def set_hand_pitch(self, slot_idx, freq_or_note, velocity=0.85):
        note = int(round(freq_or_note)) if freq_or_note < 130 else 60
        freq = midi_to_freq(freq_or_note) if freq_or_note < 130 else freq_or_note
        key = (slot_idx, note)
        with self.lock:
            if key in self.voices:
                v = self.voices[key]
                v.target_freq = freq
                v.velocity = max(0.1, min(1.0, velocity))
            else:
                self.note_on(note, int(velocity * 127), channel=slot_idx)

    def release_hand(self, slot_idx):
        with self.lock:
            to_release = [k for k in self.voices if k[0] == slot_idx]
            for k in to_release:
                self.voices[k].held = False
                self.voices[k].env_state = 4

    # ==========================================
    #  オーディオ合成コールバック
    # ==========================================
    def _audio_callback(self, outdata, frames, time_info, status):
        if not self.enabled:
            outdata.fill(0)
            return

        dt = 1.0 / self.samplerate
        sample_times = np.arange(frames, dtype=np.float32) * dt
        k = np.arange(frames, dtype=np.float32)
        sr = float(self.samplerate)

        with self.lock:
            morph_pos = self.waveform_morph
            master_vol = self.master_volume
            pan = self.pan

            # LFO (ビブラート)
            lfo = np.sin(2.0 * np.pi * (self._vib_phase + k * (self.VIBRATO_HZ / sr)))
            self._vib_phase = (self._vib_phase + frames * (self.VIBRATO_HZ / sr)) % 1.0

            # チャンネル音量スムージング
            vol_ramp = []
            for ch in range(16):
                target = self.ch_volume[ch]
                start = self._ch_volume_now[ch]
                self._ch_volume_now[ch] = target
                vol_ramp.append(np.linspace(start, target, frames, endpoint=False, dtype=np.float32))

            ch_mix = {}
            dead = []

            for key, v in list(self.voices.items()):
                ch = v.channel
                v.age += frames * dt

                # ポルタメント
                v.current_freq += (v.target_freq - v.current_freq) * 0.15
                semis = (v.current_freq - 440.0) / 440.0 * 12.0 if v.current_freq > 0 else 0.0
                semis += self.ch_bend[ch]

                freq = v.current_freq * (2.0 ** (self.ch_bend[ch] / 12.0))
                inc = freq / sr
                mod = self.ch_mod[ch]

                if mod > 0:
                    inc_arr = inc * (1.0 + 0.012 * mod * lfo)
                    phases = (v.phase + np.cumsum(inc_arr) * (2.0 * np.pi)) % (2.0 * np.pi)
                    v.phase = phases[-1]
                else:
                    d_phase = 2.0 * np.pi * inc
                    phases = (v.phase + k * d_phase) % (2.0 * np.pi)
                    v.phase = (v.phase + frames * d_phase) % (2.0 * np.pi)

                # 波形合成
                ch_morph = self.ch_wave[ch]
                sample = generate_morphed_waveform(ch_morph, phases)
                if ch_morph > 4.0:
                    bell_weight = ch_morph - 4.0
                    decay = np.exp(-v.age * 3.5)
                    sample = sample * (1.0 - bell_weight + bell_weight * decay)

                # ADSR エンベロープ
                env = np.empty(frames, dtype=np.float32)
                rel_time = max(0.02, self.release_time)
                att_step = 1.0 / (self.ATTACK_SEC * sr)
                rel_step = 1.0 / (rel_time * sr)

                for i in range(frames):
                    if v.env_state == 1:  # ATTACK
                        v.env_val += att_step
                        if v.env_val >= 1.0:
                            v.env_val = 1.0
                            v.env_state = 3  # SUSTAIN
                    elif v.env_state == 3:  # SUSTAIN
                        if not v.held:
                            v.env_state = 4  # RELEASE
                    elif v.env_state == 4:  # RELEASE
                        v.env_val -= rel_step
                        if v.env_val <= 0.0:
                            v.env_val = 0.0
                            dead.append(key)
                            break
                    else:
                        v.env_val = 0.0
                        dead.append(key)
                        break
                    env[i] = v.env_val

                if v.env_val > 0.0:
                    voice_out = sample * env * v.velocity * vol_ramp[ch]
                    if ch in ch_mix:
                        ch_mix[ch] += voice_out
                    else:
                        ch_mix[ch] = voice_out

            for key in dead:
                self.voices.pop(key, None)

            self.time_sec += frames * dt

        # チャンネル別フィルター
        mixed_total = np.zeros(frames, dtype=np.float32)
        for ch in range(16):
            if ch not in ch_mix:
                self._filter_zi[ch][:] = 0.0
                self._ch_cutoff_now[ch] = self.ch_cutoff[ch]
                continue
            mixed_total += self._apply_filter(ch, ch_mix[ch])

        # リミッター ＆ 知覚マスターボリューム (2乗カーブ)
        limited = np.tanh(mixed_total * self.MASTER_GAIN)
        output_mono = limited * (master_vol ** 2)

        left_gain = math.cos(pan * math.pi * 0.5)
        right_gain = math.sin(pan * math.pi * 0.5)

        outdata[:, 0] = output_mono * left_gain
        outdata[:, 1] = output_mono * right_gain

    def _apply_filter(self, ch, x):
        """ローパスフィルター (scipy RBJフィルター または移動平均)"""
        cutoff_val = self.ch_cutoff[ch]
        if lfilter is not None:
            now = self._ch_cutoff_now[ch]
            now += (cutoff_val - now) * self.CUTOFF_SMOOTH
            self._ch_cutoff_now[ch] = now
            reso = self.ch_reso[ch]
            if now >= 0.999 and reso <= 0.001:
                self._filter_zi[ch][:] = 0.0
                return x

            cached = self._filter_coef[ch]
            if cached is None or abs(cached[0] - now) > 1e-4 or cached[1] != reso:
                fc = self.CUTOFF_MIN_HZ * (self.CUTOFF_MAX_HZ / self.CUTOFF_MIN_HZ) ** now
                fc = min(fc, self.samplerate * 0.45)
                q = 0.707 + reso * 7.0
                w0 = 2.0 * np.pi * fc / self.samplerate
                alpha = np.sin(w0) / (2.0 * q)
                cos_w0 = np.cos(w0)
                a0 = 1.0 + alpha
                b = np.array([(1.0 - cos_w0) / 2.0, 1.0 - cos_w0, (1.0 - cos_w0) / 2.0]) / a0
                a = np.array([1.0, -2.0 * cos_w0 / a0, (1.0 - alpha) / a0])
                cached = (now, reso, b, a)
                self._filter_coef[ch] = cached

            y, self._filter_zi[ch] = lfilter(cached[2], cached[3], x, zi=self._filter_zi[ch])
            return y
        else:
            if cutoff_val < 0.95:
                k_size = int((1.0 - cutoff_val) * 8.0) + 1
                if k_size > 1:
                    kernel = np.ones(k_size, dtype=np.float32) / k_size
                    filtered = np.convolve(x, kernel, mode='same')
                    blend = 1.0 - cutoff_val
                    return x * (1.0 - blend) + filtered * blend
            return x


class OutputHub:
    """複数の出力先（内蔵シンセ / MIDIポート）へ同じMIDIメッセージを配るハブ"""
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def send(self, msg):
        for o in self.outputs:
            try:
                o.send(msg)
            except Exception as e:
                pass

    def cycle_device(self):
        for o in self.outputs:
            if hasattr(o, 'cycle_device'):
                return o.cycle_device()
        return "N/A"

    def close(self):
        for o in self.outputs:
            try:
                o.close()
            except Exception:
                pass


# 互換エイリアス
InternalSynth = SoftSynth
