import sys
import time
import threading
import mido

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


def find_pico_serial_port():
    if len(sys.argv) > 1:
        return sys.argv[1]
    if serial is None:
        return None
    for p in serial.tools.list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''} {p.hwid}".lower()
        # Raspberry Pi Pico USB VID:PID = 2E8A
        if "2e8a" in desc or "pico" in desc or "rp2040" in desc:
            return p.device
    return None


print("=" * 60)
print("  tElemin Controller Live Diagnostic Tool")
print("=" * 60)

stop_event = threading.Event()


def monitor_serial():
    if serial is None:
        print("[Serial] pyserial が未インストールのためシリアル監視をスキップします。")
        return
    port_name = find_pico_serial_port()
    if not port_name:
        print("[Serial] Pico のシリアルポートが自動検出できませんでした。(引数で COMポート 指定可)")
        return
    try:
        s = serial.Serial(port_name, 115200, timeout=0.5)
        s.dtr = True
        s.rts = True
        print(f"[{port_name}] シリアルポートオープン成功。ログを監視中...")
        while not stop_event.is_set():
            line = s.readline()
            if line:
                text = line.decode('utf-8', errors='ignore').strip()
                if "[MIDI TX]" in text:
                    print(f" >>> [Pico内部MIDI送信] {text}")
            time.sleep(0.01)
        s.close()
    except Exception as e:
        print(f"[{port_name} Error] {e}")


def monitor_midi():
    pico_ports = [p for p in mido.get_input_names() if any(kw in p.lower() for kw in ("pico", "telemin", "arduino"))]
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


t_ser = threading.Thread(target=monitor_serial, daemon=True)
t_midi = threading.Thread(target=monitor_midi, daemon=True)

t_ser.start()
t_midi.start()

print("\n--- 監視中（30秒間）---")
print("お手元の基板のボタン（8個のキー）のどれかを押すか、4個のつまみを回してください！")
print("------------------------\n")

try:
    for _ in range(30):
        time.sleep(1)
except KeyboardInterrupt:
    pass

stop_event.set()
time.sleep(0.5)
print("\n監視を終了しました。")
