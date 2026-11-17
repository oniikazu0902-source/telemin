# ==========================================
# tElemin 内蔵シンセサイザー (synth.py)
# ==========================================
# Vital などの外部シンセを使わずに、Python 自身で高音質・超低遅延で音を合成し、
# PC のオーディオ出力（イヤホン端子 / USB-DAC など）から直接鳴らします。
#
# 特徴:
#   1. 波形無段階モーフィング: Sine ⇄ Triangle ⇄ Sawtooth ⇄ Square ⇄ Theremin ⇄ Bell
#      PolyBLEP アンチエイリアス＋知覚音量（RMS）均一化ゲインを適用。
#   2. オシロスコープHUDプレビュー: 画面右上に現在の合成波形＋実音と同じLPF特性を描画。
#   3. ベクトル化高速コールバック: Pythonループを排除し24声同時発音でも約1ms以下で処理。
#   4. マスター制御とチャンネル制御の分離:
#      - つまみ (RV2/RV4): master_cutoff / master_volume
#      - 手の演出・MIDI曲 (CC#74/CC#7): ch_cutoff[ch] / ch_volume[ch]（互いに干渉しない）
#   5. WASAPI優先デバイス選択 ＆ OutputHub 対応。
# ==========================================
import threading
import numpy as np
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


def _poly_blep(t, dt):
    """PolyBLEP アンチエイリアス補正（t: 0〜1正規化位相, dt: inc=freq/sr）"""
    blep = np.zeros_like(t, dtype=np.float32)
    if dt <= 0.0:
        return blep
    lo = t < dt
    if np.any(lo):
        x = t[lo] / dt
        blep[lo] = x + x - x * x - 1.0
    hi = t > (1.0 - dt)
    if np.any(hi):
        x = (t[hi] - 1.0) / dt
        blep[hi] = x * x + x + x + 1.0
    return blep


def generate_single_waveform(wave_idx, norm_phases, inc=0.0):
    """単一の基本波形を正規化位相アレイ (0.0〜1.0) から生成。
    全波形の基本波位相を sin(2πt) 相 (0度) に統一しているため、モーフィング中間での
    逆位相キャンセル（音量の急落）が起きない。
    また、A特性（聴感）ラウドネスが全波形で均一になるよう振幅補正済み。
    """
    two_pi = np.float32(2.0 * np.pi)
    if wave_idx == WAVE_SINE:
        # 正弦波: 基本波位相 0 deg
        return np.sin(two_pi * norm_phases) * np.float32(0.99)

    elif wave_idx == WAVE_TRIANGLE:
        # 三角波: 位相 +0.25 シフトで sin(2πt) 相 (0 deg) に一致
        tp = (norm_phases + np.float32(0.25)) % np.float32(1.0)
        tri = np.float32(1.0) - np.float32(2.0) * np.abs(np.float32(2.0) * tp - np.float32(1.0))
        return tri * np.float32(1.18)

    elif wave_idx == WAVE_SAWTOOTH:
        # ノコギリ波（下降形 1-2t）: 全倍音とも sin(2πnt) 相 (0 deg) のため他波形と打ち消し合わない
        # PolyBLEP（ステップ立ち上がり方向に合わせて加算）を適用
        saw = np.float32(1.0) - np.float32(2.0) * norm_phases
        if inc > 0.0:
            saw = saw + _poly_blep(norm_phases, inc)
        return saw * np.float32(0.72)

    elif wave_idx == WAVE_SQUARE:
        # 矩形波: 基本波位相 0 deg。倍音エネルギーが強いため 0.47 に抑制してA特性ラウドネスを一致
        sq = np.where(norm_phases < 0.5, np.float32(1.0), np.float32(-1.0))
        if inc > 0.0:
            sq = sq + _poly_blep(norm_phases, inc) - _poly_blep((norm_phases + np.float32(0.5)) % np.float32(1.0), inc)
        return sq * np.float32(0.47)

    elif wave_idx == WAVE_THEREMIN:
        # 真空管テルミン: 基音 + 2次/3次倍音 + ソフトサチュレーション (0 deg)
        ph = two_pi * norm_phases
        raw = np.sin(ph) + np.float32(0.22) * np.sin(np.float32(2.0) * ph) + np.float32(0.10) * np.sin(np.float32(3.0) * ph)
        return np.tanh(raw * np.float32(1.3)) * np.float32(0.92)

    elif wave_idx == WAVE_BELL:
        # ベル/オルゴール: 位相ラップでのクリックノイズを防ぐ整数倍音構成 (3, 4, 6次倍音)
        ph = two_pi * norm_phases
        s1 = np.sin(ph)
        s3 = np.float32(0.32) * np.sin(ph * np.float32(3.0))
        s4 = np.float32(0.22) * np.sin(ph * np.float32(4.0))
        s6 = np.float32(0.12) * np.sin(ph * np.float32(6.0))
        return (s1 + s3 + s4 + s6) * np.float32(0.80)

    return np.sin(two_pi * norm_phases) * np.float32(0.99)


def generate_morphed_waveform(morph_pos, norm_phases, inc=0.0):
    """波形の滑らかな連続モーフィング（無段階クロスフェード補間）"""
    max_idx = len(WAVE_NAMES) - 1
    m = max(0.0, min(float(max_idx), float(morph_pos)))
    idx0 = int(math.floor(m))
    idx1 = min(max_idx, idx0 + 1)
    frac = m - idx0

    if frac < 0.005 or idx0 == idx1:
        return generate_single_waveform(idx0, norm_phases, inc=inc)

    w0 = generate_single_waveform(idx0, norm_phases, inc=inc)
    w1 = generate_single_waveform(idx1, norm_phases, inc=inc)
    f = np.float32(frac)
    return w0 * (np.float32(1.0) - f) + w1 * f


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


def _compute_biquad_coeffs(cutoff_norm, reso_norm, samplerate):
    """RBJ 2次ローパスフィルター係数を計算"""
    fc = 180.0 * (14000.0 / 180.0) ** max(0.0, min(1.0, float(cutoff_norm)))
    fc = min(fc, samplerate * 0.45)
    q = 0.707 + max(0.0, min(1.0, float(reso_norm))) * 7.0
    w0 = 2.0 * math.pi * fc / samplerate
    alpha = math.sin(w0) / (2.0 * q)
    cos_w0 = math.cos(w0)
    a0 = 1.0 + alpha
    b = np.array([(1.0 - cos_w0) / 2.0, 1.0 - cos_w0, (1.0 - cos_w0) / 2.0], dtype=np.float64) / a0
    a = np.array([1.0, -2.0 * cos_w0 / a0, (1.0 - alpha) / a0], dtype=np.float64)
    return b, a


class _Voice:
    __slots__ = (
        'note', 'channel', 'velocity', 'phase', 'current_freq',
        'target_freq', 'env_val', 'held', 'age', 'order'
    )

    def __init__(self, note, channel, velocity, order, freq):
        self.note = note
        self.channel = channel
        self.velocity = velocity
        self.phase = 0.0         # 正規化位相 [0.0, 1.0)
        self.current_freq = freq
        self.target_freq = freq
        self.env_val = 0.0
        self.held = True
        self.age = 0.0           # 経過秒数（0.0 からスタート）
        self.order = order       # 発音順番カウンター（ボイススティール用）


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

    def __init__(self, device=None, blocksize=256, samplerate=48000, sample_rate=None):
        if sample_rate is not None:
            samplerate = sample_rate
        self.blocksize = blocksize
        self.samplerate = samplerate
        self.lock = threading.RLock()
        self.voices = {}  # (channel, note) -> _Voice
        self._order_counter = 0

        # マスターパラメータ (物理つまみ RV1〜RV4 で操作)
        self.waveform_morph = 4.0   # 0.0〜5.0 (初期値: Vintage Theremin)
        self.master_volume = 0.80   # 0.0〜1.0 (初期値: 80%)
        self.master_cutoff = 1.0    # 0.05〜1.0 (RV2)
        self.release_time = self.DEFAULT_RELEASE_SEC
        self.pan = 0.5
        self.enabled = True

        # チャンネル別パラメータ (16ch: 手演奏のピンチ・傾きFILTER_MODE・MIDI曲で操作)
        self.ch_volume = [1.0] * 16
        self._ch_volume_now = list(self.ch_volume)
        self.ch_wave = [self.waveform_morph] * 16
        self.ch_bend = [0.0] * 16
        self.ch_mod = [0.0] * 16
        self.ch_cutoff = [1.0] * 16
        self._ch_cutoff_now = [1.0] * 16
        self.ch_reso = [0.0] * 16
        self._filter_zi = [np.zeros(2, dtype=np.float64) for _ in range(16)]
        self._filter_coef = [None] * 16

        self._vib_phase = 0.0
        self.time_sec = 0.0
        self.device = device
        self.device_name = "(none)"
        self._devices = self._list_output_devices()
        self._device_pos = 0
        self.stream = None

        if lfilter is None:
            print("【内蔵シンセ】scipy が見つからないため、フィルターは無効です。`pip install scipy` を実行してください。")
        if sd is None:
            print("【内蔵シンセ】sounddevice が見つかりません。`pip install sounddevice` を実行してください。")
            return

        self.print_devices()
        self._device_pos = self._find_device(device)
        self._open_stream()

    # ==========================================
    #  オーディオデバイス選択（WASAPI 優先）
    # ==========================================
    @staticmethod
    def _list_output_devices():
        if sd is None:
            return []
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        outs = [(i, d) for i, d in enumerate(devices) if d['max_output_channels'] > 0]
        wasapi = [i for i, h in enumerate(hostapis) if 'WASAPI' in h['name']]
        if wasapi:
            preferred = [(i, d) for i, d in outs if d['hostapi'] == wasapi[0]]
            if preferred:
                return preferred
        return outs

    def print_devices(self):
        print("--- 利用可能なオーディオ出力デバイス ---")
        for pos, (_, d) in enumerate(self._devices):
            print(f" [{pos}] {d['name']}")
        print("----------------------------------------")

    def _find_device(self, device):
        if not self._devices:
            return 0
        if isinstance(device, int):
            for pos, (i, _) in enumerate(self._devices):
                if i == device:
                    return pos
        elif isinstance(device, str):
            for pos, (_, d) in enumerate(self._devices):
                if device.lower() in d['name'].lower():
                    return pos
            print(f"【内蔵シンセ】'{device}' を含む出力デバイスが見つからないため、既定のデバイスを使います。")
        try:
            hostapi = sd.query_hostapis(self._devices[0][1]['hostapi'])
            default_idx = hostapi['default_output_device']
            for pos, (i, _) in enumerate(self._devices):
                if i == default_idx:
                    return pos
        except Exception:
            pass
        return 0

    def _open_stream(self):
        if sd is None or not self._devices:
            return
        self.close()
        dev_idx, dev = self._devices[self._device_pos]
        try:
            self.samplerate = int(dev['default_samplerate'])
            self.stream = sd.OutputStream(
                device=dev_idx,
                channels=2,
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                latency='low',
                dtype='float32',
                callback=self._audio_callback
            )
            self.stream.start()
            self.device_name = dev['name']
            print(f"[SoftSynth] 開始: {self.device_name} ({self.samplerate}Hz, Buffer: {self.blocksize})")
        except Exception as e:
            print(f"[SoftSynth] デバイスオープン失敗 ({dev['name']}): {e}")
            try:
                self.samplerate = 48000
                self.stream = sd.OutputStream(
                    channels=2,
                    samplerate=self.samplerate,
                    blocksize=self.blocksize,
                    latency='low',
                    dtype='float32',
                    callback=self._audio_callback
                )
                self.stream.start()
                self.device_name = "System default"
                print("[SoftSynth] 既定のオーディオ出力で開始しました。")
            except Exception as e2:
                self.stream = None
                self.device_name = "(error)"
                print(f"[SoftSynth] オーディオ出力初期化エラー: {e2}")

    def next_device(self):
        """オーディオ出力デバイスを次の候補へ切り替える ('o' キー)"""
        if not self._devices:
            return self.device_name
        self.all_notes_off()
        self._device_pos = (self._device_pos + 1) % len(self._devices)
        self._open_stream()
        return self.device_name

    def cycle_device(self):
        """next_device のエイリアス"""
        return self.next_device()

    def get_device_name(self):
        return self.device_name

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                print(f"[SoftSynth] ストリームクローズ警告: {e}")
            self.stream = None

    # ==========================================
    #  MIDI メッセージ受信インターフェース (send)
    # ==========================================
    def send(self, msg):
        """mido.Message を受け取ってシンセを直接駆動"""
        t = msg.type
        if t == 'note_on':
            if msg.velocity > 0:
                self.note_on(msg.note, msg.velocity, channel=getattr(msg, 'channel', 0))
            else:
                self.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif t == 'note_off':
            self.note_off(msg.note, channel=getattr(msg, 'channel', 0))
        elif t == 'pitchwheel':
            ch = getattr(msg, 'channel', 0)
            with self.lock:
                self.ch_bend[ch] = (msg.pitch / 8192.0) * 2.0
        elif t == 'control_change':
            ch = getattr(msg, 'channel', 0)
            val_norm = max(0.0, min(1.0, msg.value / 127.0))
            if msg.control == 1:  # モジュレーション (ビブラート)
                with self.lock:
                    self.ch_mod[ch] = val_norm
            elif msg.control == 7:  # チャンネル音量 (ピンチの強さ / MIDI曲トラック音量)
                with self.lock:
                    self.ch_volume[ch] = val_norm
            elif msg.control == 10:  # パン
                self.set_pan(val_norm)
            elif msg.control == 16:  # WAVE モード (0: Sine -> 1: Sawtooth 連続変化)
                with self.lock:
                    self.ch_wave[ch] = val_norm * 2.0
            elif msg.control == 74:  # チャンネル別フィルターカットオフ (FILTER_MODE)
                with self.lock:
                    self.ch_cutoff[ch] = val_norm
            elif msg.control == 71:  # チャンネル別レゾナンス
                with self.lock:
                    self.ch_reso[ch] = val_norm
            elif msg.control in (120, 123):  # All Notes Off
                self._channel_off(ch)

    # ==========================================
    #  マスター音色パラメータ制御 API (物理つまみ・UI用)
    # ==========================================
    # 注意: float/int の曖昧さを避けるため、MIDI値 (0〜127) は *_midi メソッドを使用する。
    def set_waveform_morph(self, morph_pos):
        """波形モーフィング位置を直接設定 (0.0〜5.0)"""
        with self.lock:
            self.waveform_morph = max(0.0, min(float(len(WAVE_NAMES) - 1), float(morph_pos)))
            for ch in range(16):
                self.ch_wave[ch] = self.waveform_morph
            return get_morph_description(self.waveform_morph)

    def set_waveform_by_midi(self, midi_val):
        """MIDIつまみ値 (0〜127) から波形を無段階モーフィング"""
        norm = max(0.0, min(1.0, float(midi_val) / 127.0))
        return self.set_waveform_morph(norm * float(len(WAVE_NAMES) - 1))

    def set_waveform(self, wave_type):
        return self.set_waveform_morph(max(0, min(len(WAVE_NAMES) - 1, int(wave_type))))

    def next_waveform(self):
        with self.lock:
            cur_idx = int(round(self.waveform_morph))
            next_idx = (cur_idx + 1) % len(WAVE_NAMES)
            return self.set_waveform_morph(float(next_idx))

    def get_waveform_name(self):
        with self.lock:
            return get_morph_description(self.waveform_morph)

    def set_master_volume(self, vol_norm):
        """マスター音量を設定 (0.0〜1.0)"""
        with self.lock:
            self.master_volume = max(0.0, min(1.0, float(vol_norm)))
            return int(round(self.master_volume * 100))

    def set_master_volume_midi(self, midi_val):
        """MIDIつまみ値 (0〜127) からマスター音量を設定"""
        return self.set_master_volume(float(midi_val) / 127.0)

    def set_cutoff(self, cutoff_norm):
        """マスター・フィルターカットオフを設定 (0.05〜1.0)"""
        with self.lock:
            self.master_cutoff = max(0.05, min(1.0, float(cutoff_norm)))
            return int(round(self.master_cutoff * 100))

    def set_cutoff_midi(self, midi_val):
        """MIDIつまみ値 (0〜127) からマスター・フィルターカットオフを設定"""
        return self.set_cutoff(float(midi_val) / 127.0)

    def set_release_time(self, seconds):
        """余韻・リリース時間を秒数で設定 (0.02〜1.2秒)"""
        with self.lock:
            self.release_time = max(0.02, min(1.20, float(seconds)))
            return round(self.release_time, 2)

    def set_release_midi(self, midi_val):
        """MIDIつまみ値 (0〜127) からリリース時間 (0.02〜1.20秒) を設定"""
        norm = max(0.0, min(1.0, float(midi_val) / 127.0))
        return self.set_release_time(0.02 + (norm ** 2) * 1.18)

    def set_pan(self, pan_norm):
        """パンを設定 (0.0=左 〜 1.0=右)"""
        with self.lock:
            self.pan = max(0.0, min(1.0, float(pan_norm)))

    @property
    def cutoff(self):
        return self.master_cutoff

    def get_waveform_preview(self, num_samples=70):
        """画面右上HUDプレビュー描画用の波形配列 (-1.0〜1.0) を生成。
        実音と同じ RBJ 2次ローパスフィルターを通して定常周期を切り出すため、見た目と実音の特性が一致する。
        """
        with self.lock:
            morph = self.waveform_morph
            cutoff_val = self.master_cutoff
            sr = float(self.samplerate)

        if lfilter is not None and cutoff_val < 0.99:
            # 440Hz 基準で 4 周期分を生成して 2次ローパスフィルターを掛け、定常状態に近い最後の 1.5 周期を切り出す
            preview_freq = 440.0
            samples_per_cycle = max(24, int(round(sr / preview_freq)))
            total_cycles = 4.5
            total_n = int(round(samples_per_cycle * total_cycles))
            norm_ph = (np.arange(total_n, dtype=np.float32) / samples_per_cycle) % np.float32(1.0)
            wave_full = generate_morphed_waveform(morph, norm_ph, inc=0.0).astype(np.float64)
            b, a = _compute_biquad_coeffs(cutoff_val, 0.0, sr)
            filtered = lfilter(b, a, wave_full)
            # 最後の 1.5 周期を切り出して num_samples 点にリサンプリング
            tail_n = int(round(samples_per_cycle * 1.5))
            tail = filtered[-tail_n:]
            idx = np.linspace(0, len(tail) - 1, num_samples)
            return np.interp(idx, np.arange(len(tail)), tail).astype(np.float32)

        norm_phases = np.linspace(0.0, 1.5, num_samples, endpoint=False, dtype=np.float32) % np.float32(1.0)
        return generate_morphed_waveform(morph, norm_phases, inc=0.0)

    # ==========================================
    #  ノート発音・制御 API
    # ==========================================
    def note_on(self, note, velocity=100, channel=0):
        freq = midi_to_freq(note)
        vel = max(0.05, min(1.0, float(velocity) / 127.0 if velocity > 1.0 else float(velocity)))
        key = (channel, note)
        with self.lock:
            self._order_counter += 1
            if key in self.voices:
                v = self.voices[key]
                v.velocity = vel
                v.target_freq = freq
                v.held = True
                v.age = 0.0
                v.order = self._order_counter
                return

            if len(self.voices) >= self.MAX_VOICES:
                oldest_key = min(self.voices, key=lambda k: (self.voices[k].held, self.voices[k].order))
                del self.voices[oldest_key]

            self.voices[key] = _Voice(note, channel, vel, self._order_counter, freq)

    def note_off(self, note, channel=0):
        key = (channel, note)
        with self.lock:
            if key in self.voices:
                self.voices[key].held = False

    def _channel_off(self, channel):
        with self.lock:
            for v in self.voices.values():
                if v.channel == channel:
                    v.held = False

    def all_notes_off(self):
        with self.lock:
            for v in self.voices.values():
                v.held = False

    # テルミン直接追従用 (RLock のため note_on 呼び出しでもデッドロックしない)
    def set_hand_pitch(self, slot_idx, freq_or_note, velocity=0.85):
        note = int(round(freq_or_note)) if freq_or_note < 130 else 60
        freq = midi_to_freq(freq_or_note) if freq_or_note < 130 else freq_or_note
        key = (slot_idx, note)
        with self.lock:
            if key in self.voices:
                v = self.voices[key]
                v.target_freq = freq
                v.velocity = max(0.1, min(1.0, float(velocity)))
            else:
                self.note_on(note, max(1, min(127, int(float(velocity) * 127))), channel=slot_idx)

    def release_hand(self, slot_idx):
        self._channel_off(slot_idx)

    # ==========================================
    #  リアルタイム波形合成コールバック (完全ベクトル化)
    # ==========================================
    def _audio_callback(self, outdata, frames, time_info, status):
        if not self.enabled:
            outdata.fill(0)
            return

        sr = float(self.samplerate)
        dt = 1.0 / sr
        k = np.arange(1, frames + 1, dtype=np.float32)

        ch_mix = {}

        with self.lock:
            master_vol = self.master_volume
            master_cutoff = self.master_cutoff
            pan = self.pan
            rel_time = max(0.02, self.release_time)
            up = np.float32(1.0 / (self.ATTACK_SEC * sr))
            down = np.float32(1.0 / (rel_time * sr))

            # LFO (ビブラート)
            vib_inc = self.VIBRATO_HZ / sr
            lfo = np.sin(np.float32(2.0 * math.pi) * (np.float32(self._vib_phase) + k * np.float32(vib_inc)))
            self._vib_phase = (self._vib_phase + frames * vib_inc) % 1.0

            vol_ramp = {}
            dead = []

            for key, v in self.voices.items():
                ch = v.channel
                if ch not in vol_ramp:
                    start = self._ch_volume_now[ch]
                    end = self.ch_volume[ch]
                    vol_ramp[ch] = np.float32(start) + np.float32(end - start) * (k / np.float32(frames))
                    self._ch_volume_now[ch] = end

                # ベクトル化エンベロープ（Pythonループなし・終端で必ず 0.0 へフェードアウト）
                if v.held:
                    env = np.minimum(np.float32(v.env_val) + up * k, np.float32(1.0))
                else:
                    env = np.maximum(np.float32(v.env_val) - down * k, np.float32(0.0))
                v.env_val = float(env[-1])
                if not v.held and v.env_val <= 0.0:
                    dead.append(key)
                    # このブロック内にはゼロまでのフェードスロープが残っているためそのまま出力に含めてクリックを防止

                # ポルタメント & ピッチベンド & ビブラート
                v.current_freq += (v.target_freq - v.current_freq) * 0.15
                freq = v.current_freq * (2.0 ** (self.ch_bend[ch] / 12.0))
                inc = freq / sr
                mod = self.ch_mod[ch]

                if mod > 0.0:
                    inc_arr = np.float32(inc) * (np.float32(1.0) + np.float32(0.012 * mod) * lfo)
                    ph = (np.float32(v.phase) + np.cumsum(inc_arr)) % np.float32(1.0)
                    v.phase = float(ph[-1])
                else:
                    ph = (np.float32(v.phase) + k * np.float32(inc)) % np.float32(1.0)
                    v.phase = float((v.phase + frames * inc) % 1.0)

                # 波形合成 (PolyBLEP ＆ ラウドネス補正付き)
                ch_morph = self.ch_wave[ch]
                sample = generate_morphed_waveform(ch_morph, ph, inc=inc)

                # Bell 成分が混ざっている場合の減衰 (余韻を保ちつつロングトーンでも音量が極端に落ちないよう床 0.55 を残す)
                if ch_morph > 4.0:
                    bell_weight = ch_morph - 4.0
                    t_sec = np.float32(v.age) + k * np.float32(dt)
                    decay = np.float32(0.55) + np.float32(0.45) * np.exp(-t_sec * np.float32(2.5))
                    sample = sample * (np.float32(1.0 - bell_weight) + np.float32(bell_weight) * decay)

                v.age += frames * dt

                voice_out = sample * env * np.float32(v.velocity) * vol_ramp[ch]
                if ch in ch_mix:
                    ch_mix[ch] += voice_out
                else:
                    ch_mix[ch] = voice_out

            for key in dead:
                del self.voices[key]

            self.time_sec += frames * dt
            ch_cutoffs = list(self.ch_cutoff)
            ch_resos = list(self.ch_reso)

        # チャンネル別 2次ローパスフィルター (ロック外で実行)
        mixed_total = np.zeros(frames, dtype=np.float64)
        for ch in range(16):
            if ch not in ch_mix:
                self._filter_zi[ch][:] = 0.0
                self._ch_cutoff_now[ch] = max(0.05, min(1.0, master_cutoff * ch_cutoffs[ch]))
                continue
            target_cut = max(0.05, min(1.0, master_cutoff * ch_cutoffs[ch]))
            mixed_total += self._apply_filter(ch, ch_mix[ch].astype(np.float64), target_cut, ch_resos[ch])

        # ソフトリミッター ＆ 知覚マスターボリューム (2乗カーブ)
        limited = np.tanh(mixed_total * self.MASTER_GAIN)
        output_mono = (limited * (master_vol ** 2)).astype(np.float32)

        left_gain = np.float32(math.cos(pan * math.pi * 0.5))
        right_gain = np.float32(math.sin(pan * math.pi * 0.5))

        outdata[:, 0] = output_mono * left_gain
        outdata[:, 1] = output_mono * right_gain

    def _apply_filter(self, ch, x, target_cut, reso):
        """RBJ 2次ローパスフィルター (scipy 不在時は畳み込み移動平均へフォールバック)"""
        now = self._ch_cutoff_now[ch]
        now += (target_cut - now) * self.CUTOFF_SMOOTH
        self._ch_cutoff_now[ch] = now
        if now >= 0.999 and reso <= 0.001:
            self._filter_zi[ch][:] = 0.0
            return x

        if lfilter is None:
            if now < 0.95:
                k_size = int((1.0 - now) * 8.0) + 1
                if k_size > 1:
                    kernel = np.ones(k_size, dtype=np.float64) / k_size
                    filtered = np.convolve(x, kernel, mode='same')
                    blend = 1.0 - now
                    return x * (1.0 - blend) + filtered * blend
            return x

        cached = self._filter_coef[ch]
        if cached is None or abs(cached[0] - now) > 1e-4 or cached[1] != reso:
            b, a = _compute_biquad_coeffs(now, reso, self.samplerate)
            cached = (now, reso, b, a)
            self._filter_coef[ch] = cached

        y, self._filter_zi[ch] = lfilter(cached[2], cached[3], x, zi=self._filter_zi[ch])
        return y


class OutputHub:
    """複数の出力先（内蔵シンセ / MIDIポート）へ同じMIDIメッセージを配るハブ"""
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def send(self, msg):
        for o in self.outputs:
            try:
                o.send(msg)
            except Exception as e:
                print(f"[OutputHub] 送信エラー ({type(o).__name__}): {e}")

    def cycle_device(self):
        for o in self.outputs:
            if hasattr(o, 'cycle_device'):
                return o.cycle_device()
        return "N/A"

    def close(self):
        for o in self.outputs:
            try:
                o.close()
            except Exception as e:
                print(f"[OutputHub] クローズエラー ({type(o).__name__}): {e}")


# 互換エイリアス
InternalSynth = SoftSynth
