from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import pykakasi
import os
from bs4 import BeautifulSoup

app = FastAPI(title="Japanese Lyrics Romanizer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GENIUS_TOKEN = os.environ.get("GENIUS_TOKEN")
kks = pykakasi.Kakasi()


def romanize(text: str) -> str:
    """Convert Japanese text to romaji using pykakasi."""
    result = kks.convert(text)
    romaji_parts = []
    for item in result:
        # Use hepburn romaji; fall back to original if not Japanese
        romaji_parts.append(item["hepburn"] if item["hepburn"] else item["orig"])
    return " ".join(romaji_parts)


def romanize_lyrics(lyrics: str) -> str:
    """Romanize each line of lyrics, preserving line breaks."""
    lines = lyrics.split("\n")
    romanized_lines = []
    for line in lines:
        if line.strip() == "":
            romanized_lines.append("")
        else:
            romanized_lines.append(romanize(line))
    return "\n".join(romanized_lines)


async def search_genius(song: str, artist: str) -> dict:
    """Search Genius for a song and return the top result."""
    headers = {"Authorization": f"Bearer {GENIUS_TOKEN}"}
    query = f"{song} {artist}".strip()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.genius.com/search",
            headers=headers,
            params={"q": query},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

    hits = data.get("response", {}).get("hits", [])
    if not hits:
        return None

    top = hits[0]["result"]
    return {
        "title": top["title"],
        "artist": top["primary_artist"]["name"],
        "url": top["url"],
        "thumbnail": top.get("song_art_image_thumbnail_url", ""),
    }


async def fetch_lyrics_from_url(url: str) -> str:
    """Scrape lyrics from a Genius song page."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            follow_redirects=True,
        )
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Genius wraps lyrics in containers with data-lyrics-container attribute
    containers = soup.find_all("div", attrs={"data-lyrics-container": "true"})
    if not containers:
        raise HTTPException(status_code=404, detail="Lyrics not found on page")

    lines = []
    for container in containers:
        for br in container.find_all("br"):
            br.replace_with("\n")
        lines.append(container.get_text())

    return "\n".join(lines).strip()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/lyrics")
async def get_lyrics(song: str, artist: str = ""):
    """
    Fetch Japanese lyrics from Genius and return both original and romanized versions.
    
    Params:
      song   - song title (required)
      artist - artist name (optional but improves accuracy)
    """
    if not GENIUS_TOKEN:
        raise HTTPException(status_code=500, detail="GENIUS_TOKEN not set on server")

    # 1. Search Genius for the song
    song_info = await search_genius(song, artist)
    if not song_info:
        raise HTTPException(status_code=404, detail="Song not found on Genius")

    # 2. Fetch the raw lyrics
    raw_lyrics = await fetch_lyrics_from_url(song_info["url"])

    # 3. Romanize
    romanized = romanize_lyrics(raw_lyrics)

    return {
        "title": song_info["title"],
        "artist": song_info["artist"],
        "thumbnail": song_info["thumbnail"],
        "genius_url": song_info["url"],
        "original_lyrics": raw_lyrics,
        "romanized_lyrics": romanized,
    }
