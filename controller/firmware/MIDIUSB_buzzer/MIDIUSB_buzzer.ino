#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <MIDI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_MCP3008.h>

// ==========================================
//  ピン配置の定義 - KiCad設計・基板シルク準拠
// ==========================================
// MCP3004 (ADC) SPIピン
#define MCP_SCK_PIN   18  // GP18 (Pad 24)
#define MCP_MOSI_PIN  19  // GP19 (Pad 25)
#define MCP_MISO_PIN  16  // GP16 (Pad 21)
#define MCP_CS_PIN    17  // GP17 (Pad 22)

// OLEDディスプレイ (SSD1306) I2Cピン
#define OLED_SDA_PIN  20  // GP20 (Pad 26)
#define OLED_SCL_PIN  21  // GP21 (Pad 27)

// Raspberry Pi Pico オンボードLED
#define PICO_LED_PIN  25

// ブザー (オプション)
#define BUZZER_PIN    15
#define ENABLE_BUZZER false

// ==========================================
//  各種インスタンス・定数
// ==========================================
Adafruit_USBD_MIDI usb_midi;
MIDI_CREATE_INSTANCE(Adafruit_USBD_MIDI, usb_midi, MIDI);

Adafruit_MCP3008 mcp;

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET     -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

#define MIDI_CHANNEL 1

// ==========================================
//  8つのボタン（設定書き換え用）
// ==========================================
// 物理配置:
//   [上段 (奥)]   SW8(GP7)    SW6(GP5)    SW4(GP3)    SW2(GP1)
//   [下段 (手前)]  SW7(GP6)    SW5(GP4)    SW3(GP2)    SW1(GP0)
#define SW1_PIN  0   // GP0 -> スケール切替
#define SW2_PIN  1   // GP1 -> 和音モード切替
#define SW3_PIN  2   // GP2 -> ストラム奏法切替
#define SW4_PIN  3   // GP3 -> オクターブ切替
#define SW5_PIN  4   // GP4 -> 自動演奏 ON/OFF
#define SW6_PIN  5   // GP5 -> 次の曲
#define SW7_PIN  6   // GP6 -> 前の曲
#define SW8_PIN  7   // GP7 -> 虹色レーン切替

const int NUM_BUTTONS = 8;
const int buttonPins[NUM_BUTTONS] = {
  SW1_PIN, SW2_PIN, SW3_PIN, SW4_PIN,
  SW5_PIN, SW6_PIN, SW7_PIN, SW8_PIN
};

const byte buttonCCs[NUM_BUTTONS] = {
  20, // SW1: スケール切替 (CC 20)
  21, // SW2: 和音モード (CC 21)
  22, // SW3: ストラム演奏 (CC 22)
  23, // SW4: オクターブ切替 (CC 23)
  24, // SW5: 自動演奏 ON/OFF (CC 24)
  25, // SW6: 次の曲 (CC 25)
  26, // SW7: 前の曲 (CC 26)
  27  // SW8: 虹色レーン (CC 27)
};

const char* buttonLabels[NUM_BUTTONS] = {
  "SW1: SCALE",
  "SW2: CHORD",
  "SW3: STRUM",
  "SW4: OCTAVE",
  "SW5: AUTO PLAY",
  "SW6: NEXT SONG",
  "SW7: PREV SONG",
  "SW8: RAINBOW"
};

// デバウンス用
bool buttonStates[NUM_BUTTONS]     = {HIGH, HIGH, HIGH, HIGH, HIGH, HIGH, HIGH, HIGH};
bool lastButtonStates[NUM_BUTTONS] = {HIGH, HIGH, HIGH, HIGH, HIGH, HIGH, HIGH, HIGH};
unsigned long lastDebounceTimes[NUM_BUTTONS] = {0, 0, 0, 0, 0, 0, 0, 0};
const unsigned long debounceDelay = 25; // 25ms

// コントローラー内部状態
const char* lastActionName = "Ready";
const byte potCCs[4] = { 74, 71, 73, 7 }; // RV1:Wave, RV2:Tone, RV3:Rel, RV4:Vol
int lastPotValues[4] = {-1, -1, -1, -1};
int rawPotAverages[4] = {0, 0, 0, 0};
unsigned long ledBlinkUntil = 0;

bool displayReady = false;
bool needDisplayUpdate = true;
void updateDisplay();

// ==========================================
//  MIDI送信ヘルパー
// ==========================================
void controlChange(byte channel, byte control, byte value) {
  MIDI.sendControlChange(control, value, channel);
  Serial.printf("[MIDI TX] CC Ch:%d, CC:%d, Val:%d\n", channel, control, value);
}

// ==========================================
//  セットアップ
// ==========================================
void setup() {
  pinMode(PICO_LED_PIN, OUTPUT);
  digitalWrite(PICO_LED_PIN, HIGH);

  Serial.begin(115200);

  // USB MIDI初期化
  usb_midi.setStringDescriptor("tElemin Physical Controller");
  MIDI.begin(MIDI_CHANNEL_OMNI);

  // USB再列挙（PCに確実に再認識させる）
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(20);
    TinyUSBDevice.attach();
  }

  // ボタンピン初期化（外付けプルアップ＋内部プルアップで安定化）
  for (int i = 0; i < NUM_BUTTONS; i++) {
    pinMode(buttonPins[i], INPUT_PULLUP);
    buttonStates[i] = digitalRead(buttonPins[i]);
    lastButtonStates[i] = buttonStates[i];
  }

  // MCP3004 ADC (ソフトウェアSPIで初期化)
  mcp.begin(MCP_SCK_PIN, MCP_MOSI_PIN, MCP_MISO_PIN, MCP_CS_PIN);

  // ポット初期値読み取り＆PCへ初回送信
  for (int i = 0; i < 4; i++) {
    int adcCh = 3 - i; // 回路図: RV1->CH3, RV2->CH2, RV3->CH1, RV4->CH0
    int raw = mcp.readADC(adcCh);
    rawPotAverages[i] = raw;
    int midiVal = constrain(map(raw, 0, 1023, 0, 127), 0, 127);
    lastPotValues[i] = midiVal;
    controlChange(MIDI_CHANNEL, potCCs[i], midiVal);
  }

  // ブザーピン初期化
  #if ENABLE_BUZZER
    pinMode(BUZZER_PIN, OUTPUT);
    digitalWrite(BUZZER_PIN, LOW);
  #endif

  // I2C & OLED 安全初期化
  pinMode(OLED_SDA_PIN, INPUT_PULLDOWN);
  pinMode(OLED_SCL_PIN, INPUT_PULLDOWN);
  delay(10);
  int sdaPd = digitalRead(OLED_SDA_PIN);
  int sclPd = digitalRead(OLED_SCL_PIN);

  pinMode(OLED_SDA_PIN, INPUT_PULLUP);
  pinMode(OLED_SCL_PIN, INPUT_PULLUP);

  if (sdaPd == HIGH && sclPd == HIGH) {
    Wire.setSDA(OLED_SDA_PIN);
    Wire.setSCL(OLED_SCL_PIN);
    Wire.begin();
    Wire.setClock(100000);
    Wire.setTimeout(10);

    uint8_t foundAddr = 0;
    Wire.beginTransmission(0x3C);
    if (Wire.endTransmission() == 0) {
      foundAddr = 0x3C;
    } else {
      Wire.beginTransmission(0x3D);
      if (Wire.endTransmission() == 0) {
        foundAddr = 0x3D;
      }
    }

    if (foundAddr != 0) {
      if (display.begin(SSD1306_SWITCHCAPVCC, foundAddr)) {
        displayReady = true;
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        updateDisplay();
      }
    }
  } else {
    displayReady = false;
  }

  Serial.println(F("\n========================================"));
  Serial.println(F("  tElemin Controller Ready!             "));
  Serial.println(F("  Buttons: Setting Switches (CC 20-27) "));
  Serial.println(F("  Knobs  : Timbre Controls (CC 74,71,73,7)"));
  Serial.printf("  OLED   : %s\n", displayReady ? "CONNECTED" : "OFFLINE (Headless mode)");
  Serial.println(F("========================================"));

  // 起動完了シグナル：LEDを3回短く点滅
  for (int b = 0; b < 3; b++) {
    digitalWrite(PICO_LED_PIN, LOW);
    delay(100);
    digitalWrite(PICO_LED_PIN, HIGH);
    delay(100);
  }
}

// ==========================================
//  メインループ
// ==========================================
void loop() {
  MIDI.read();
  unsigned long now = millis();

  // 1. ボタン8つの処理（設定書き換えコマンド送信）
  for (int i = 0; i < NUM_BUTTONS; i++) {
    int reading = digitalRead(buttonPins[i]);

    if (reading != lastButtonStates[i]) {
      lastDebounceTimes[i] = now;
    }

    if ((now - lastDebounceTimes[i]) > debounceDelay) {
      if (reading != buttonStates[i]) {
        buttonStates[i] = reading;

        // 押下 = LOW (プルアップ回路)
        bool isPressed = (buttonStates[i] == LOW);

        if (isPressed) {
          // ボタン押下時に設定変更コマンド(CC)を送信
          controlChange(MIDI_CHANNEL, buttonCCs[i], 127);
          lastActionName = buttonLabels[i];
          needDisplayUpdate = true;
          ledBlinkUntil = now + 150; // 押下時点灯
        }
      }
    }
    lastButtonStates[i] = reading;
  }

  // 2. つまみ4つの処理（音色変更パラメータ送信）
  // 回路図: RV1->CH3, RV2->CH2, RV3->CH1, RV4->CH0
  for (int i = 0; i < 4; i++) {
    int adcCh = 3 - i;
    int raw = mcp.readADC(adcCh);

    // 指数移動平均フィルタ (ノイズ低減)
    rawPotAverages[i] = (rawPotAverages[i] * 3 + raw) / 4;
    int midiVal = map(rawPotAverages[i], 0, 1023, 0, 127);
    midiVal = constrain(midiVal, 0, 127);

    // 端点(0および127)は差が1でも確実に検知、それ以外はジッター防止で差が2以上
    bool edgeReached = (midiVal == 0 && lastPotValues[i] != 0) || (midiVal == 127 && lastPotValues[i] != 127);
    if (abs(midiVal - lastPotValues[i]) >= 2 || edgeReached) {
      lastPotValues[i] = midiVal;
      controlChange(MIDI_CHANNEL, potCCs[i], midiVal);
      needDisplayUpdate = true;
    }
  }

  // 3. OLEDディスプレイ更新
  if (needDisplayUpdate && displayReady) {
    updateDisplay();
    needDisplayUpdate = false;
  }

  // 4. LEDインジケータ（動作ハートビート＆押下フィードバック）
  bool anyPressed = false;
  for (int i = 0; i < NUM_BUTTONS; i++) {
    if (buttonStates[i] == LOW) {
      anyPressed = true;
      break;
    }
  }
  if (anyPressed || now < ledBlinkUntil) {
    digitalWrite(PICO_LED_PIN, HIGH);
  } else {
    // 500ms周期で点滅（プログラムが正常動作中であることを可視化）
    digitalWrite(PICO_LED_PIN, ((now / 500) % 2 == 0) ? HIGH : LOW);
  }

  // 5. 1秒周期のハートビートログ（シリアル確認用）
  static unsigned long lastHeartbeat = 0;
  if (now - lastHeartbeat >= 1000) {
    lastHeartbeat = now;
    Serial.printf("[HEARTBEAT] SW(1-8): %d%d%d%d%d%d%d%d | RV(Wave,Tone,Rel,Vol): %d %d %d %d\n",
      digitalRead(SW1_PIN), digitalRead(SW2_PIN), digitalRead(SW3_PIN), digitalRead(SW4_PIN),
      digitalRead(SW5_PIN), digitalRead(SW6_PIN), digitalRead(SW7_PIN), digitalRead(SW8_PIN),
      lastPotValues[0], lastPotValues[1], lastPotValues[2], lastPotValues[3]);
  }

  delay(5);
}

// ==========================================
//  OLED画面描画
// ==========================================
void updateDisplay() {
  if (!displayReady) return;
  display.clearDisplay();

  display.setTextSize(1);
  display.setCursor(0, 0);
  display.print(F("--- tElemin CONTROLLER ---"));

  display.setTextSize(1);
  display.setCursor(0, 16);
  display.print(F("LAST ACT:"));
  display.setTextSize(2);
  display.setCursor(0, 28);
  display.print(lastActionName);

  display.setTextSize(1);
  display.setCursor(0, 48);
  display.printf("WAVE:%-3d TONE:%-3d", lastPotValues[0], lastPotValues[1]);
  display.setCursor(0, 56);
  display.printf("REL :%-3d VOL :%-3d", lastPotValues[2], lastPotValues[3]);

  display.display();
}