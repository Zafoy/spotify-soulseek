import os
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Spotify parameters
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'user-library-read playlist-read-private'

# Spotify authentication
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
))

# Load or initialize track index
track_index_path = "track_index.json"
if os.path.exists(track_index_path):
    with open(track_index_path, "r", encoding="utf-8") as f:
        track_index = json.load(f)
else:
    track_index = {}

# Function for handling playlists
def process_playlist_tracks(playlist_id, playlist_name):
    tracks = sp.playlist_tracks(playlist_id)
    while tracks:
        for item in tracks.get('items', []):
            track = item.get('track')
            if not track or not track.get('id'):
                continue

            track_id = track['id']
            track_name = track.get('name', 'Unknown Track')
            track_artist = ", ".join(a.get('name', '') for a in track.get('artists', []) if a.get('name'))
            album_name = track.get('album', {}).get('name', 'Unknown Album')

            if track_id not in track_index:
                track_index[track_id] = {
                    "name": track_name,
                    "artist": track_artist,
                    "album": album_name,
                    "sources": []
                }

            # Add playlist source if not already linked
            if not any(s.get("type") == "playlist" and s.get("playlist_name") == playlist_name for s in track_index[track_id]["sources"]):
                track_index[track_id]["sources"].append({
                    "type": "playlist",
                    "playlist_name": playlist_name
                })

        tracks = sp.next(tracks) if tracks.get('next') else None

# Function for handling albums
def process_album_tracks(album_id, album_name):
    tracks = sp.album_tracks(album_id)
    while tracks:
        for track in tracks.get('items', []):
            track_id = track.get('id')
            if not track_id:
                continue
            track_name = track.get('name', 'Unknown Track')
            track_artist = ", ".join(a.get('name', '') for a in track.get('artists', []) if a.get('name'))

            if track_id not in track_index:
                track_index[track_id] = {
                    "name": track_name,
                    "artist": track_artist,
                    "album": album_name,
                    "sources": []
                }

            # Add album source if not already linked
            if not any(s.get("type") == "album" and s.get("album_name") == album_name for s in track_index[track_id]["sources"]):
                track_index[track_id]["sources"].append({
                    "type": "album",
                    "album_name": album_name
                })

        tracks = sp.next(tracks) if tracks.get('next') else None

#                            #
# --- Playlist selection --- #
#                            #

playlist_items = []
results = sp.current_user_playlists()
while results:
    playlist_items.extend(results.get('items', []))
    results = sp.next(results) if results and results.get('next') else None

print("\nPlaylists:")
for i, playlist in enumerate(playlist_items):
    print(f"{i:2}: {playlist.get('name', 'Unknown Playlist')}")

selected = input("\nEnter comma-separated playlist numbers to include (or 'all'): ").strip().lower()
if selected == "all":
    selected_playlists = playlist_items
else:
    max_index = len(playlist_items) - 1
    selected_indices = set()
    for part in selected.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 0 <= idx <= max_index:
                selected_indices.add(idx)
            else:
                print(f"Warning: playlist index {idx} out of range, skipping.")
        else:
            print(f"Warning: invalid input '{part}', skipping.")
    selected_playlists = [p for i, p in enumerate(playlist_items) if i in selected_indices]

for playlist in selected_playlists:
    pid = playlist.get('id')
    pname = playlist.get('name', 'Unknown Playlist')
    print(f"\nProcessing playlist: {pname}")
    if pid:
        process_playlist_tracks(pid, pname)

#                         #
# --- Album selection --- #
#                         #

album_items = []
results = sp.current_user_saved_albums()
while results:
    album_items.extend(results.get('items', []))
    results = sp.next(results) if results and results.get('next') else None

print("\nAlbums:")
for i, item in enumerate(album_items):
    album = item.get('album', {})
    print(f"{i:2}: {album.get('name', 'Unknown Album')}")

selected = input("\nEnter comma-separated album numbers to include (or 'all'): ").strip().lower()
if selected == "all":
    selected_albums = album_items
else:
    max_index = len(album_items) - 1
    selected_indices = set()
    for part in selected.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part)
            if 0 <= idx <= max_index:
                selected_indices.add(idx)
            else:
                print(f"Warning: album index {idx} out of range, skipping.")
        else:
            print(f"Warning: invalid input '{part}', skipping.")
    selected_albums = [p for i, p in enumerate(album_items) if i in selected_indices]

for item in selected_albums:
    album = item.get('album')
    if not album:
        print("Warning: album data missing for selected item, skipping.")
        continue
    aid = album.get('id')
    aname = album.get('name', 'Unknown Album')
    print(f"\nProcessing album: {aname}")
    if aid:
        process_album_tracks(aid, aname)

# Save updated track index once at the end
with open(track_index_path, "w", encoding="utf-8") as f:
    json.dump(track_index, f, indent=2, ensure_ascii=False)

print("\nUpdated track_index.json with selected playlists and albums.")