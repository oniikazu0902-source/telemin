# tElemin 物理MIDIコントローラー (Hardware & Firmware)

Raspberry Pi Pico（RP2040）および MCP3004（ADC）を用いた、tElemin 専用の物理MIDIコントローラーです。

---

## ディレクトリ構成
- `firmware/MIDIUSB_buzzer/`: Raspberry Pi Pico 用 Arduino ファームウェア（スケッチ）
- `hardware/jikkenn/`: KiCad 設計データ（回路図・PCB基板・ガーバーデータ）

---

## ハードウェア仕様
- **マイコン**: Raspberry Pi Pico (RP2040)
- **ADC**: MCP3004 (SPI接続: GP16=MISO, GP17=CS, GP18=SCK, GP19=MOSI)
- **ディスプレイ**: 0.96インチ OLED SSD1306 (I2C接続: GP20=SDA, GP21=SCL) ※未接続でも自動検知して安全に動作
- **ボタン (8個)**: タクトスイッチ（GP0 〜 GP7）
- **ポテンショメータ (4個)**: 10kΩ 可変抵抗（MCP3004 CH3〜CH0）

---

## コントローラー機能割り当て

### つまみ 4個 (RV1 〜 RV4)：【音色変更】
| つまみ | チャンネル | MIDI CC | 機能 | 詳細 |
| :--- | :---: | :---: | :--- | :--- |
| **RV1** | CH3 | **CC 74** | **波形無段階モーフィング** | Sine ⇄ Triangle ⇄ Saw ⇄ Square ⇄ Theremin ⇄ Bell |
| **RV2** | CH2 | **CC 71** | **音の明るさ (Cutoff)** | ローパスフィルター（0% こもった音 〜 100% クリアな音） |
| **RV3** | CH1 | **CC 73** | **余韻 (Release)** | 余韻の長さ（0.02秒 〜 1.20秒） |
| **RV4** | CH0 | **CC 7**  | **マスター音量 (Volume)** | 0%（完全無音） 〜 100%（最大音量） |

### ボタン 8個 (SW1 〜 SW8)：【設定の書き換え】
| ボタン | ピン | MIDI CC | 機能 |
| :--- | :---: | :---: | :--- |
| **SW1** | GP0 | **CC 20** | **音階（スケール）切替**（ハ長調 → イ短調 → ト長調 → ニ長調 → ペンタ → 琉球 → 都節） |
| **SW2** | GP1 | **CC 21** | **和音モード (CHORD)** ON / OFF |
| **SW3** | GP2 | **CC 22** | **ストラム奏法 (STRUM)** ON / OFF |
| **SW4** | GP3 | **CC 23** | **オクターブ切替**（Normal → +1 Oct → -1 Oct） |
| **SW5** | GP4 | **CC 24** | **自動演奏 (AUTO)** ON / OFF |
| **SW6** | GP5 | **CC 25** | **次の曲へ (Next Song)** |
| **SW7** | GP6 | **CC 26** | **前の曲へ (Prev Song)** |
| **SW8** | GP7 | **CC 27** | **虹色レーン演出** ON / OFF |

---

## ファームウェア書き込み方法 (Arduino CLI)
```powershell
# コンパイル
arduino-cli compile -b rp2040:rp2040:rpipico:usbstack=tinyusb "controller/firmware/MIDIUSB_buzzer"

# 書き込み (PicoのCOMポートを指定)
arduino-cli upload -p COM5 -b rp2040:rp2040:rpipico:usbstack=tinyusb "controller/firmware/MIDIUSB_buzzer"
```
