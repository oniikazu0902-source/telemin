"""
song_fetcher.py
無料・API経由でオタクカルチャー系音楽（東方Project、ゲーム音楽等）のMIDIデータを取得・キャッシュするモジュール
"""
import os
import json
import urllib.request
import urllib.parse
import threading

DOWNLOAD_DIR = "downloaded_songs"

# GitHub オープンMIDIリポジトリ (東方Project - 原作者ZUN氏公認の二次創作MIDIコレクション)
REPO_RAW_BASE = "https://raw.githubusercontent.com/AyHa1810/touhou-midi-collection/master"
REPO_API_BASE = "https://api.github.com/repos/AyHa1810/touhou-midi-collection/contents"

# 人気オタクカルチャー神曲のプリセットカタログ（レート制限なく直接ダウンロード可能）
FEATURED_ONLINE_SONGS = [
    {
        "id": "un_owen",
        "title": "U.N.オーエンは彼女なのか？ (東方)",
        "path": "1 - Shooter Games/6 - The Embodiment of Scarlet Devil/15. U.N. Owen was her (ZUN).mid",
        "local_filename": "un_owen_zun.mid"
    },
    {
        "id": "bad_apple",
        "title": "Bad Apple!! (東方)",
        "path": "1 - Shooter Games/4 - Lotus Land Story/7. Bad Apple!! (Blargzargo).mid",
        "local_filename": "bad_apple.mid"
    },
    {
        "id": "septette",
        "title": "亡き王女の為のセプテット (東方)",
        "path": "1 - Shooter Games/6 - The Embodiment of Scarlet Devil/13. Septette for a Dead Princess (ZUN).mid",
        "local_filename": "septette_zun.mid"
    },
    {
        "id": "tomboyish",
        "title": "おてんば恋娘 (東方/チルノ)",
        "path": "1 - Shooter Games/6 - The Embodiment of Scarlet Devil/5. Tomboyish Girl in Love (ZUN).mid",
        "local_filename": "tomboyish_girl.mid"
    },
    {
        "id": "luna_dial",
        "title": "月時計 ~ ルナ・ダイアル (東方)",
        "path": "1 - Shooter Games/6 - The Embodiment of Scarlet Devil/11. Lunar Clock ~ Luna Dial (ZUN).mid",
        "local_filename": "lunar_clock.mid"
    },
    {
        "id": "native_faith",
        "title": "ネイティブフェイス (東方)",
        "path": "1 - Shooter Games/10 - Mountain of Faith/18. Native Faith (ZUN).mid",
        "local_filename": "native_faith.mid"
    }
]

def ensure_download_dir():
    """ダウンロード先ディレクトリが存在することを確認"""
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)
    return DOWNLOAD_DIR

def is_song_cached(local_filename):
    """指定された曲が既にローカルにキャッシュされているか確認"""
    path = os.path.join(DOWNLOAD_DIR, local_filename)
    return os.path.exists(path) and os.path.getsize(path) > 0

def get_cached_path(local_filename):
    """キャッシュされたMIDIのパスを返す"""
    return os.path.join(DOWNLOAD_DIR, local_filename)

def download_song(song_info, callback=None):
    """
    指定された曲情報をダウンロードしてキャッシュする
    song_info: FEATURED_ONLINE_SONGSの要素、またはカスタム辞書
    callback: 完了時に呼ばれる関数 callback(success, path_or_error)
    """
    ensure_download_dir()
    local_path = os.path.join(DOWNLOAD_DIR, song_info['local_filename'])
    
    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        if callback:
            callback(True, local_path)
        return local_path
    
    encoded_path = urllib.parse.quote(song_info['path'])
    url = f"{REPO_RAW_BASE}/{encoded_path}"
    
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'tElemin-SongFetcher/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response, open(local_path, 'wb') as out_file:
            out_file.write(response.read())
            
        print(f"【ダウンロード完了】{song_info['title']} -> {local_path}")
        if callback:
            callback(True, local_path)
        return local_path
    except Exception as e:
        print(f"【ダウンロード失敗】{song_info['title']}: {e}")
        if callback:
            callback(False, str(e))
        return None

def download_song_async(song_info, callback=None):
    """バックグラウンドスレッドで非同期ダウンロード"""
    t = threading.Thread(target=download_song, args=(song_info, callback), daemon=True)
    t.start()
    return t

def fetch_category_items_api(category_path=""):
    """
    GitHub REST APIを使用してフォルダ内のアイテムを取得（レート制限60回/時あり）
    """
    encoded_path = urllib.parse.quote(category_path)
    url = f"{REPO_API_BASE}/{encoded_path}" if category_path else REPO_API_BASE
    
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'tElemin-SongFetcher/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"【API取得失敗】{url}: {e}")
        return []

def get_available_songs():
    """
    現在利用可能な（ローカルキャッシュ済み、またはプリセットされている）オンライン曲一覧を取得
    """
    ensure_download_dir()
    songs = []
    for item in FEATURED_ONLINE_SONGS:
        cached = is_song_cached(item['local_filename'])
        songs.append({
            "id": item['id'],
            "title": item['title'],
            "is_cached": cached,
            "local_path": get_cached_path(item['local_filename']) if cached else None,
            "raw_info": item
        })
    return songs
