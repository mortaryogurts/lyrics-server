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
    result = kks.convert(text)
    romaji_parts = []
    for item in result:
        romaji_parts.append(item["hepburn"] if item["hepburn"] else item["orig"])
    return " ".join(romaji_parts)


def romanize_lyrics(lyrics: str) -> str:
    lines = lyrics.split("\n")
    romanized_lines = []
    for line in lines:
        if line.strip() == "":
            romanized_lines.append("")
        else:
            romanized_lines.append(romanize(line))
    return "\n".join(romanized_lines)


async def search_genius(query: str):
    """Search Genius and return all hits."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.genius.com/search",
            params={"q": query, "access_token": GENIUS_TOKEN},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("response", {}).get("hits", [])


async def fetch_lyrics_from_url(url: str) -> str:
    """Scrape lyrics from a Genius song page."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=15, follow_redirects=True)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
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
    if not GENIUS_TOKEN:
        raise HTTPException(status_code=500, detail="GENIUS_TOKEN not set on server")

    query = f"{song} {artist}".strip()

    # Strategy 1: Look for a pre-romanized version by "Genius Romanizations"
    hits = await search_genius(f"{query} romanized")

    romanized_hit = None
    japanese_hit = None

    for hit in hits:
        result = hit["result"]
        artist_name = result.get("primary_artist_names", "")
        title = result.get("title", "").lower()

        if "genius romanizations" in artist_name.lower():
            romanized_hit = result
            break

    # Strategy 2: Also search for the original Japanese version
    jp_hits = await search_genius(query)
    for hit in jp_hits:
        result = hit["result"]
        # Skip Genius Romanizations entries for the Japanese version
        if "genius romanizations" not in result.get("primary_artist_names", "").lower():
            japanese_hit = result
            break

    if not romanized_hit and not japanese_hit:
        raise HTTPException(status_code=404, detail="Song not found on Genius")

    # Use whichever we found as the primary source for metadata
    primary = romanized_hit or japanese_hit

    result_data = {
        "title": primary["title"],
        "artist": primary["primary_artist_names"],
        "thumbnail": primary.get("song_art_image_thumbnail_url", ""),
        "genius_url": primary["url"],
        "original_lyrics": "",
        "romanized_lyrics": "",
    }

    # Try to get pre-romanized lyrics from Genius Romanizations
    if romanized_hit:
        try:
            romanized_lyrics = await fetch_lyrics_from_url(romanized_hit["url"])
            result_data["romanized_lyrics"] = romanized_lyrics
        except Exception:
            romanized_hit = None  # fall through to pykakasi

    # Get original Japanese lyrics and romanize with pykakasi if needed
    if japanese_hit:
        try:
            original_lyrics = await fetch_lyrics_from_url(japanese_hit["url"])
            result_data["original_lyrics"] = original_lyrics
            # Only use pykakasi if we didn't get pre-romanized lyrics
            if not result_data["romanized_lyrics"]:
                result_data["romanized_lyrics"] = romanize_lyrics(original_lyrics)
        except Exception:
            pass

    # If we only got the romanized version and no original
    if result_data["romanized_lyrics"] and not result_data["original_lyrics"]:
        result_data["original_lyrics"] = result_data["romanized_lyrics"]

    if not result_data["romanized_lyrics"]:
        raise HTTPException(status_code=404, detail="Could not fetch lyrics. Genius may be blocking the request.")

    return result_data
