import os
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'playlist-read-private'

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
))

# Load existing track index
try:
    with open("track_index.json", "r", encoding="utf-8") as f:
        track_index = json.load(f)
except FileNotFoundError:
    track_index = {}

# Fetch all playlists
playlist_items = []
results = sp.current_user_playlists()
while results:
    playlist_items.extend(results['items'])
    results = sp.next(results) if results['next'] else None

# Display list of playlists
print("\nYour Playlists:")
for i, playlist in enumerate(playlist_items):
    print(f"{i:2}: {playlist['name']}")

# Prompt for selection
selected = input("\nEnter comma-separated playlist numbers to include: ")
selected_indices = {int(i.strip()) for i in selected.split(",") if i.strip().isdigit()}

selected_playlists = [p for i, p in enumerate(playlist_items) if i in selected_indices]

# Process selected playlists
for playlist in selected_playlists:
    playlist_id = playlist['id']
    playlist_name = playlist['name']
    print(f"\nProcessing playlist: {playlist_name}")

    tracks = sp.playlist_tracks(playlist_id)
    while tracks:
        for item in tracks['items']:
            track = item['track']
            if not track or not track.get("id"):
                continue

            track_id = track['id']
            track_name = track['name']
            track_artist = ", ".join(a['name'] for a in track['artists'])
            album_name = track['album']['name']

            if track_id not in track_index:
                track_index[track_id] = {
                    "name": track_name,
                    "artist": track_artist,
                    "album": album_name,
                    "sources": []
                }

            # Add source if not already listed
            already_linked = any(
                s.get("type") == "playlist" and s.get("playlist_name") == playlist_name
                for s in track_index[track_id]["sources"]
            )
            if not already_linked:
                track_index[track_id]["sources"].append({
                    "type": "playlist",
                    "playlist_name": playlist_name
                })

        tracks = sp.next(tracks) if tracks.get('next') else None

# Save updated index
with open("track_index.json", "w", encoding="utf-8") as f:
    json.dump(track_index, f, indent=2, ensure_ascii=False)

print(f"\nUpdated track_index.json with selected playlists.")