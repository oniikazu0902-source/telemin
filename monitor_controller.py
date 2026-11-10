import time
import threading
import serial
import mido

print("=" * 60)
print("  tElemin Controller Live Diagnostic Tool")
print("=" * 60)

stop_event = threading.Event()

def monitor_serial():
    try:
        s = serial.Serial('COM5', 115200, timeout=0.5)
        s.dtr = True
        s.rts = True
        print("[COM5] シリアルポートオープン成功。ハートビートを監視中...")
        while not stop_event.is_set():
            line = s.readline()
            if line:
                text = line.decode('utf-8', errors='ignore').strip()
                if "[MIDI TX]" in text:
                    print(f" >>> [Pico内部MIDI送信] {text}")
                elif "[HEARTBEAT]" in text:
                    # 変化があった場合や定期的に表示
                    pass
            time.sleep(0.01)
        s.close()
    except Exception as e:
        print(f"[COM5 Error] {e}")

def monitor_midi():
    pico_ports = [p for p in mido.get_input_names() if "pico" in p.lower()]
    if not pico_ports:
        print("[MIDI Error] Pico MIDI入力ポートが見つかりません！")
        return
    port_name = pico_ports[0]
    try:
        with mido.open_input(port_name) as inport:
            print(f"[MIDI] Windows MIDIポート '{port_name}' オープン成功。メッセージ待機中...")
            while not stop_event.is_set():
                for msg in inport.iter_pending():
                    print(f" ★★★ [Windows MIDI受信] {msg}")
                time.sleep(0.01)
    except Exception as e:
        print(f"[MIDI Error] {e}")

# 別スレッドで両方を監視
t_ser = threading.Thread(target=monitor_serial, daemon=True)
t_midi = threading.Thread(target=monitor_midi, daemon=True)

t_ser.start()
t_midi.start()

print("\n--- 監視中（30秒間）---")
print("お手元の基板のボタン（8個のキー）のどれかを押すか、4個のつまみを回してください！")
print("------------------------\n")

try:
    for i in range(30):
        time.sleep(1)
except KeyboardInterrupt:
    pass

stop_event.set()
time.sleep(0.5)
print("\n監視を終了しました。")
