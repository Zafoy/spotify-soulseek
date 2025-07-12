import os
import json
from collections import defaultdict

# Load track index
with open("track_index.json", "r", encoding="utf-8") as f:
    track_index = json.load(f)

# Output directories
PLAYLIST_DIR = "Playlists"
ALBUM_DIR = "Albums"
TRACK_DIR = "Tracks"

# Make directories if they don't exist
os.makedirs(PLAYLIST_DIR, exist_ok=True)
os.makedirs(ALBUM_DIR, exist_ok=True)

# Collect tracks by source
playlists = defaultdict(list)
albums = defaultdict(list)

for track_id, info in track_index.items():
    path = os.path.join("music", f"{track_id}.mp3") # path to track

    for source in info["sources"]:
        if source["type"] == "playlist":
            playlists[source["playlist_name"]].append(path)
        elif source["type"] == "album":
            albums[source["album_title"]].append(path)

# Helper to write M3U
def write_m3u(filename, tracks):
    with open(filename, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for track in tracks:
            f.write(track + "\n")

# Write playlist M3Us
for name, tracks in playlists.items():
    filename = os.path.join(PLAYLIST_DIR, f"{name}.m3u")
    print(f"Playlist generated: {name}")
    write_m3u(filename, tracks)

# Write album M3Us
for name, tracks in albums.items():
    filename = os.path.join(ALBUM_DIR, f"{name}.m3u")
    print(f"Album generated: {name}")
    write_m3u(filename, tracks)

print("M3U playlists generated.")