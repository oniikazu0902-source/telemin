# ==========================================
# tElemin 内蔵シンセサイザー
# ==========================================
# Vital などの外部シンセを使わずに、Python 自身で音を合成して
# PC のオーディオ出力（ヘッドホン端子 / USB-DAC など）から直接鳴らす。
#
# mido.Message をそのまま受け取る send() を持っているので、
# MIDI 出力ポートと同じ感覚で使える（OutputHub で MIDI ポートと同時送信も可能）。
#
# 対応メッセージ:
#   note_on / note_off      : 発音・消音（チャンネルごと独立、ポリフォニー対応）
#   CC#7  (Volume)          : チャンネル音量（ピンチの強さ）
#   CC#16 (WAVE)            : 波形を サイン波(0) → ノコギリ波(127) に連続変化
#   CC#1  (Modulation)      : ビブラートの深さ
#   CC#123 / CC#120         : 全消音
#   pitchwheel              : ±2半音のピッチベンド
# ==========================================
import threading
import numpy as np

try:
    import sounddevice as sd
except ImportError:  # 未インストールでも telemin.py 自体は起動できるようにする
    sd = None


class _Voice:
    __slots__ = ('note', 'channel', 'velocity', 'phase', 'level', 'held', 'age')

    def __init__(self, note, channel, velocity, age):
        self.note = note
        self.channel = channel
        self.velocity = velocity
        self.phase = 0.0
        self.level = 0.0
        self.held = True
        self.age = age


class SoftSynth:
    MAX_VOICES = 24        # 同時発音数（超えたら最も古い音を止める）
    ATTACK_SEC = 0.012     # 立ち上がり（短すぎるとプチッと鳴る）
    RELEASE_SEC = 0.25     # 指を離した後の余韻
    MASTER_GAIN = 0.22     # 全体音量（大きくすると歪みやすい）
    DEFAULT_WAVE = 0.35    # WAVE モード OFF 時の音色（0=サイン波, 1=ノコギリ波）
    VIBRATO_HZ = 5.5

    def __init__(self, device=None, blocksize=256):
        self.blocksize = blocksize
        self.lock = threading.Lock()
        self.voices = {}  # (channel, note) -> _Voice
        self._age = 0
        self.ch_volume = [100 / 127] * 16
        self._ch_volume_now = list(self.ch_volume)  # 音量変化をなめらかにするための現在値
        self.ch_wave = [self.DEFAULT_WAVE] * 16
        self.ch_bend = [0.0] * 16   # 半音単位
        self.ch_mod = [0.0] * 16    # 0.0〜1.0
        self._vib_phase = 0.0
        self.stream = None
        self.samplerate = 48000
        self.device_name = "(none)"
        self._devices = self._list_output_devices()
        self._device_pos = 0

        if sd is None:
            print("【内蔵シンセ】sounddevice が見つかりません。`pip install sounddevice` を実行してください。")
            return

        self.print_devices()
        self._device_pos = self._find_device(device)
        self._open_stream()

    # ------------------------------
    # オーディオデバイス関連
    # ------------------------------
    @staticmethod
    def _list_output_devices():
        """出力デバイスの一覧。Windows では遅延の小さい WASAPI のデバイスを優先する"""
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
        for pos, (i, d) in enumerate(self._devices):
            print(f" [{pos}] {d['name']}")
        print("----------------------------------------")

    def _find_device(self, device):
        if not self._devices:
            return 0
        if isinstance(device, str):
            for pos, (i, d) in enumerate(self._devices):
                if device.lower() in d['name'].lower():
                    return pos
            print(f"【内蔵シンセ】'{device}' を含む出力デバイスが見つからないため、既定のデバイスを使います。")
        # ホストAPIの既定出力デバイスを探す
        try:
            hostapi = sd.query_hostapis(self._devices[0][1]['hostapi'])
            default_idx = hostapi['default_output_device']
            for pos, (i, d) in enumerate(self._devices):
                if i == default_idx:
                    return pos
        except Exception:
            pass
        return 0

    def _open_stream(self):
        if sd is None or not self._devices:
            return
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        dev_idx, dev = self._devices[self._device_pos]
        try:
            self.samplerate = int(dev['default_samplerate'])
            self.stream = sd.OutputStream(
                device=dev_idx, channels=2, samplerate=self.samplerate,
                blocksize=self.blocksize, latency='low', dtype='float32',
                callback=self._callback)
            self.stream.start()
            self.device_name = dev['name']
            print(f"【内蔵シンセ】オーディオ出力: {self.device_name} ({self.samplerate} Hz)")
        except Exception as e:
            print(f"【内蔵シンセ】オーディオ出力を開けませんでした ({dev['name']}): {e}")
            # 最後の手段として、OS の既定デバイスで開き直す
            try:
                self.samplerate = 48000
                self.stream = sd.OutputStream(
                    channels=2, samplerate=self.samplerate, blocksize=self.blocksize,
                    latency='low', dtype='float32', callback=self._callback)
                self.stream.start()
                self.device_name = "System default"
                print("【内蔵シンセ】既定のオーディオ出力で再試行しました。")
            except Exception as e2:
                self.stream = None
                self.device_name = "(error)"
                print(f"【内蔵シンセ】既定のオーディオ出力も開けませんでした: {e2}")

    def next_device(self):
        """次のオーディオ出力デバイスに切り替える（'o' キー）"""
        if not self._devices:
            return
        self.all_notes_off()
        self._device_pos = (self._device_pos + 1) % len(self._devices)
        self._open_stream()

    # ------------------------------
    # MIDI メッセージ受付
    # ------------------------------
    def send(self, msg):
        t = msg.type
        if t == 'note_on' and msg.velocity > 0:
            self._note_on(msg.channel, msg.note, msg.velocity)
        elif t == 'note_off' or (t == 'note_on' and msg.velocity == 0):
            self._note_off(msg.channel, msg.note)
        elif t == 'control_change':
            ch, val = msg.channel, msg.value / 127.0
            if msg.control == 7:
                self.ch_volume[ch] = val
            elif msg.control == 16:
                self.ch_wave[ch] = val
            elif msg.control == 1:
                self.ch_mod[ch] = val
            elif msg.control in (120, 123):
                self._channel_off(ch)
        elif t == 'pitchwheel':
            self.ch_bend[msg.channel] = msg.pitch / 8192.0 * 2.0

    def _note_on(self, ch, note, vel):
        with self.lock:
            self._age += 1
            v = self.voices.get((ch, note))
            if v is not None:
                # 同じ音の再発音は位相を引き継いでなめらかに（レガート）
                v.held = True
                v.velocity = vel / 127.0
                v.age = self._age
                return
            if len(self.voices) >= self.MAX_VOICES:
                oldest = min(self.voices, key=lambda k: self.voices[k].age)
                del self.voices[oldest]
            self.voices[(ch, note)] = _Voice(note, ch, vel / 127.0, self._age)

    def _note_off(self, ch, note):
        with self.lock:
            v = self.voices.get((ch, note))
            if v is not None:
                v.held = False

    def _channel_off(self, ch):
        with self.lock:
            for v in self.voices.values():
                if v.channel == ch:
                    v.held = False

    def all_notes_off(self):
        for ch in range(16):
            self._channel_off(ch)

    # ------------------------------
    # 音声合成（オーディオスレッドから呼ばれる）
    # ------------------------------
    def _callback(self, outdata, frames, time_info, status):
        sr = self.samplerate
        k = np.arange(1, frames + 1, dtype=np.float64)
        mix = np.zeros(frames, dtype=np.float64)
        up = 1.0 / (self.ATTACK_SEC * sr)
        down = 1.0 / (self.RELEASE_SEC * sr)

        # ビブラート用 LFO（全チャンネル共通の揺れ、深さはチャンネルごと）
        vib_inc = self.VIBRATO_HZ / sr
        lfo = np.sin(2 * np.pi * (self._vib_phase + k * vib_inc))
        self._vib_phase = (self._vib_phase + frames * vib_inc) % 1.0

        # チャンネル音量はブロック内で直線補間して「ジジジ」というノイズを防ぐ
        vol_ramp = {}

        with self.lock:
            dead = []
            for key, v in self.voices.items():
                ch = v.channel
                if ch not in vol_ramp:
                    start, end = self._ch_volume_now[ch], self.ch_volume[ch]
                    vol_ramp[ch] = start + (end - start) * (k / frames)
                    self._ch_volume_now[ch] = end

                # エンベロープ（アタック / リリース）
                if v.held:
                    env = np.minimum(v.level + up * k, 1.0)
                else:
                    env = np.maximum(v.level - down * k, 0.0)
                v.level = env[-1]
                if not v.held and v.level <= 0.0:
                    dead.append(key)
                    continue

                # 周波数（ピッチベンド + ビブラート）
                semis = v.note - 69 + self.ch_bend[ch]
                freq = 440.0 * 2.0 ** (semis / 12.0)
                inc = freq / sr
                mod = self.ch_mod[ch]
                if mod > 0:
                    inc_arr = inc * (1.0 + 0.012 * mod * lfo)  # 最大 ±約20セント
                    ph = (v.phase + np.cumsum(inc_arr)) % 1.0
                    v.phase = ph[-1]
                else:
                    ph = (v.phase + k * inc) % 1.0
                    v.phase = (v.phase + frames * inc) % 1.0

                # サイン波とノコギリ波（PolyBLEP で折り返しノイズを低減）をブレンド
                sine = np.sin(2 * np.pi * ph)
                saw = 2.0 * ph - 1.0
                dt = min(inc, 0.5)
                lo = ph < dt
                t = ph[lo] / dt
                saw[lo] -= t + t - t * t - 1.0
                hi = ph > 1.0 - dt
                t = (ph[hi] - 1.0) / dt
                saw[hi] -= t * t + t + t + 1.0

                w = self.ch_wave[ch]
                wave = (1.0 - w) * sine + w * 0.6 * saw
                mix += wave * env * v.velocity * vol_ramp[ch]

            for key in dead:
                del self.voices[key]

        # ソフトクリップで音割れを防ぐ
        out = np.tanh(mix * self.MASTER_GAIN).astype(np.float32)
        outdata[:, 0] = out
        outdata[:, 1] = out

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class OutputHub:
    """複数の出力先（内蔵シンセ / MIDI ポート）へ同じ MIDI メッセージを配る"""

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def send(self, msg):
        for o in self.outputs:
            try:
                o.send(msg)
            except Exception as e:
                print(f"出力エラー ({type(o).__name__}): {e}")

    def close(self):
        for o in self.outputs:
            try:
                o.close()
            except Exception:
                pass
