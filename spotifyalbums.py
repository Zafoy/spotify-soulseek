import os
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Spotify API credentials from .env
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'user-library-read'

# Authenticate with Spotify
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE
))

# Load existing track index if present
track_index_path = "track_index.json"
if os.path.exists(track_index_path):
    with open(track_index_path, "r", encoding="utf-8") as f:
        track_index = json.load(f)
else:
    track_index = {}

print("Fetching saved albums...")

results = sp.current_user_saved_albums()
while results:
    for item in results['items']:
        album = item['album']
        album_id = album['id']
        album_title = album['name']
        album_artist = ", ".join(artist['name'] for artist in album['artists'])

        # Get full track listing for the album
        track_results = sp.album_tracks(album_id)
        while track_results:
            for track in track_results['items']:
                track_id = track['id']
                track_name = track['name']
                track_artist = ", ".join(artist['name'] for artist in track['artists'])

                # Add or update track in the global index
                if track_id not in track_index:
                    track_index[track_id] = {
                        "name": track_name,
                        "artist": track_artist,
                        "album": album_title,
                        "sources": []
                    }

                # Avoid duplicate source entries
                source_entry = {
                    "type": "album",
                    "album_title": album_title
                }
                if source_entry not in track_index[track_id]["sources"]:
                    track_index[track_id]["sources"].append(source_entry)

            if track_results.get('next'):
                track_results = sp.next(track_results)
            else:
                break

    if results.get('next'):
        results = sp.next(results)
    else:
        break

# Save unified track index
with open(track_index_path, "w", encoding="utf-8") as f:
    json.dump(track_index, f, indent=2, ensure_ascii=False)

print(f"Updated track_index.json with {len(track_index)} unique tracks from saved albums.")
