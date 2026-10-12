#!/usr/bin/env python3
"""
Auto YouTube motivational-video bot.

Order of work:
  1. OpenRouter writes a new ~90 second motivational speech
  2. Supabase is checked so the speech is never a repeat, then it is saved
  3. Kokoro reads it aloud (British male voice: bm_lewis)
  4. FFmpeg joins background video + quiet music + voice + animated captions
  5. The video is posted to YouTube and the result is saved in Supabase

Fill in your keys in the .env file (copy .env.example to .env). Nothing else
needs editing.

Run:      python bot.py
Test run: python bot.py --no-upload     (makes the video, skips YouTube)
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ============================================================================
#  SETTINGS  (all read from .env — see .env.example)
# ============================================================================
def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == "" or "PASTE_" in str(value)):
        sys.exit(f"[config] Missing value for {name}. Fill it in inside your .env file.")
    return value


OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", required=True)
OPENROUTER_MODEL = env("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")

SUPABASE_URL = env("SUPABASE_URL", required=True)
SUPABASE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", required=True)  # service_role key

KOKORO_MODEL = env("KOKORO_MODEL_PATH", "kokoro-v1.0.onnx")
KOKORO_VOICES = env("KOKORO_VOICES_PATH", "voices-v1.0.bin")
KOKORO_VOICE = env("KOKORO_VOICE", "am_onyx")
KOKORO_SPEED = float(env("KOKORO_SPEED", "0.95"))

BACKGROUND_VIDEO = env("BACKGROUND_VIDEO", "assets/background.mp4")  # file OR folder
BACKGROUND_MUSIC = env("BACKGROUND_MUSIC", "assets/music.mp3")      # file OR folder
MUSIC_VOLUME = float(env("MUSIC_VOLUME", "0.12"))                   # 0.0 - 1.0

VIDEO_WIDTH = int(env("VIDEO_WIDTH", "1080"))    # 1080x1920 = vertical (Shorts)
VIDEO_HEIGHT = int(env("VIDEO_HEIGHT", "1920"))  # use 1920x1080 for normal videos
VIDEO_PRESET = env("VIDEO_PRESET", "veryfast")   # ffmpeg libx264 preset (veryfast|medium|slow)
VIDEO_CRF = int(env("VIDEO_CRF", "23"))          # quality (19-28 typical, higher = smaller/faster)

YOUTUBE_CLIENT_ID = env("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = env("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = env("YOUTUBE_REFRESH_TOKEN")
YOUTUBE_PRIVACY = env("YOUTUBE_PRIVACY", "private")  # private | unlisted | public
YOUTUBE_CATEGORY_ID = env("YOUTUBE_CATEGORY_ID", "22")

OUTPUT_DIR = Path(env("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
#  STEP 1 + 2 — OpenRouter writes the speech, Supabase prevents repeats
# ============================================================================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.lower())).strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def recent_speeches(sb, limit=60):
    rows = (
        sb.table("speeches")
        .select("title,topic")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )
    return rows or []


def ask_openrouter(avoid):
    avoid_text = "\n".join(f"- {r.get('title')} ({r.get('topic')})" for r in avoid) or "none yet"
    system = (
        "You are a world-class motivational speechwriter. You write powerful, "
        "original spoken-word speeches meant to be read aloud by a calm, deep British male voice."
    )
    user = f"""Write ONE brand-new motivational speech of about 220 words (about 90 seconds when spoken).

Rules:
- Spoken style: short punchy sentences, strong rhythm, no stage directions, no emojis, no markdown.
- Plain text only. Use normal punctuation (periods, commas, question marks).
- Start with a hook in the first sentence. End with a memorable closing line.
- Pick a topic and angle that is clearly DIFFERENT from these previous ones:
{avoid_text}

Reply with ONLY valid JSON, nothing before or after it, in exactly this shape:
{{"topic": "2-4 word topic", "title": "YouTube title under 80 characters", "description": "2-3 sentence YouTube description", "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"], "speech": "the full speech text"}}"""

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 1.0,
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("OpenRouter did not return JSON")
    data = json.loads(content[start : end + 1])
    for key in ("topic", "title", "speech"):
        if not str(data.get(key, "")).strip():
            raise ValueError(f"OpenRouter JSON missing '{key}'")
    data.setdefault("description", "")
    data.setdefault("tags", [])
    return data


def get_unique_speech(sb, max_tries=6):
    """Generate speeches until one is not already in Supabase, then save it."""
    avoid = recent_speeches(sb)
    for attempt in range(1, max_tries + 1):
        print(f"[1/5] Asking OpenRouter for a new speech (try {attempt}/{max_tries})...")
        data = ask_openrouter(avoid)
        h = text_hash(data["speech"])

        dup = sb.table("speeches").select("id").eq("text_hash", h).limit(1).execute().data
        if dup:
            print("      Repeat speech detected, trying again.")
            avoid.append({"title": data["title"], "topic": data["topic"]})
            continue

        print("[2/5] Speech is new — saving to Supabase.")
        row = (
            sb.table("speeches")
            .insert(
                {
                    "topic": data["topic"].strip(),
                    "title": data["title"].strip()[:100],
                    "text": data["speech"].strip(),
                    "text_hash": h,
                    "description": data["description"].strip(),
                    "tags": [str(t).strip() for t in data["tags"]][:15],
                }
            )
            .execute()
            .data[0]
        )
        return row
    raise RuntimeError("Could not get a unique speech after several tries.")


# ============================================================================
#  STEP 3 — Kokoro voice (bm_lewis)
# ============================================================================
def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def synthesize(text, wav_path):
    """Returns list of (sentence, start_seconds, duration_seconds) and total duration."""
    from kokoro_onnx import Kokoro

    for f in (KOKORO_MODEL, KOKORO_VOICES):
        if not Path(f).exists():
            sys.exit(f"[kokoro] File not found: {f}  (see README / .env.example)")

    print(f"[3/5] Kokoro is reading the speech aloud ({KOKORO_VOICE})...")
    kokoro = Kokoro(KOKORO_MODEL, KOKORO_VOICES)

    pause = 0.30
    chunks, timings, cursor, sample_rate = [], [], 0.0, 24000
    for sentence in split_sentences(text):
        samples, sample_rate = kokoro.create(
            sentence, voice=KOKORO_VOICE, speed=KOKORO_SPEED, lang="en-gb"
        )
        dur = len(samples) / sample_rate
        timings.append((sentence, cursor, dur))
        chunks.append(samples)
        gap = np.zeros(int(sample_rate * pause), dtype=samples.dtype)
        chunks.append(gap)
        cursor += dur + pause

    audio = np.concatenate(chunks)
    sf.write(wav_path, audio, sample_rate)
    total = len(audio) / sample_rate
    print(f"      Voice length: {total:.1f}s")
    return timings, total


# ============================================================================
#  STEP 4 — Animated captions (ASS karaoke) + FFmpeg
# ============================================================================
def ass_time(t):
    t = max(t, 0)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_captions(timings, ass_path, words_per_line=4):
    # Gold words light up as they are spoken; white = not yet spoken.
    font_size = int(VIDEO_HEIGHT * 0.048)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_WIDTH}
PlayResY: {VIDEO_HEIGHT}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,{font_size},&H0000D7FF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,{max(4, font_size // 10)},2,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for sentence, start, dur in timings:
        words = sentence.split()
        if not words:
            continue
        weights = [max(len(w), 2) for w in words]
        total_w = sum(weights)
        # absolute start time for each word
        t = start
        word_times = []
        for w, wt in zip(words, weights):
            wd = dur * wt / total_w
            word_times.append((w, t, wd))
            t += wd

        for i in range(0, len(word_times), words_per_line):
            group = word_times[i : i + words_per_line]
            g_start = group[0][1]
            g_end = group[-1][1] + group[-1][2]
            text = ""
            for w, _, wd in group:
                cs = max(int(round(wd * 100)), 1)
                text += f"{{\\kf{cs}}}{w.upper()} "
            events.append(
                f"Dialogue: 0,{ass_time(g_start)},{ass_time(g_end)},Cap,,0,0,0,,"
                f"{{\\fad(80,40)\\fscx92\\fscy92\\t(0,120,\\fscx100\\fscy100)}}{text.strip()}"
            )
    Path(ass_path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def pick_media(path_str, exts, label):
    p = Path(path_str)
    if p.is_dir():
        files = [f for f in p.iterdir() if f.suffix.lower() in exts]
        if not files:
            sys.exit(f"[media] No {label} files found in folder: {p}")
        return random.choice(files)
    if not p.exists():
        sys.exit(f"[media] {label} file not found: {p}")
    return p


def render_video(voice_wav, ass_file, duration, out_mp4, workdir):
    video = pick_media(BACKGROUND_VIDEO, {".mp4", ".mov", ".mkv", ".webm"}, "background video").resolve()
    music = pick_media(BACKGROUND_MUSIC, {".mp3", ".wav", ".m4a", ".aac", ".ogg"}, "background music").resolve()
    total = duration + 1.0  # small tail after the voice ends

    print(f"[4/5] FFmpeg is building the video ({total:.0f}s)...")
    print(f"      video: {video.name} | music: {music.name}")

    # Run inside workdir so the subtitle filename needs no path escaping (Windows safe).
    vf = (
        f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps=30,ass={ass_file.name}[v];"
        f"[2:a]volume={MUSIC_VOLUME},afade=t=in:d=2,afade=t=out:st={max(total - 3, 0):.2f}:d=3[m];"
        f"[1:a]apad[voice];"
        f"[voice][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-threads", "2",
        "-stream_loop", "-1", "-i", str(video),
        "-i", voice_wav.name,
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", vf,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.2f}",
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_mp4.resolve()),
    ]
    subprocess.run(cmd, cwd=workdir, check=True)


# ============================================================================
#  STEP 5 — YouTube upload (Google API)
# ============================================================================
def youtube_client():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    for name, value in (
        ("YOUTUBE_CLIENT_ID", YOUTUBE_CLIENT_ID),
        ("YOUTUBE_CLIENT_SECRET", YOUTUBE_CLIENT_SECRET),
        ("YOUTUBE_REFRESH_TOKEN", YOUTUBE_REFRESH_TOKEN),
    ):
        if not value or "PASTE_" in value:
            sys.exit(f"[config] Missing value for {name}. Fill it in inside your .env file.")

    # No browser login: the refresh token is exchanged for a fresh access token each run.
    creds = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(mp4_path, speech):
    from googleapiclient.http import MediaFileUpload

    print(f"[5/5] Uploading to YouTube ({YOUTUBE_PRIVACY})...")
    yt = youtube_client()
    description = (speech.get("description") or "").strip()
    description += "\n\n#Shorts #motivation #mindset"
    body = {
        "snippet": {
            "title": speech["title"][:100],
            "description": description[:4900],
            "tags": speech.get("tags") or [],
            "categoryId": YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(mp4_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"      {int(status.progress() * 100)}%")
    video_id = response["id"]
    return video_id, f"https://www.youtube.com/watch?v={video_id}"


# ============================================================================
#  MAIN
# ============================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true", help="build the video but do not post it")
    args = parser.parse_args()

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    speech = get_unique_speech(sb)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    workdir = OUTPUT_DIR / stamp
    workdir.mkdir(parents=True, exist_ok=True)

    upload_row = sb.table("uploads").insert({"speech_id": speech["id"], "status": "rendering"}).execute().data[0]

    try:
        voice_wav = workdir / "voice.wav"
        timings, duration = synthesize(speech["text"], voice_wav)

        ass_file = workdir / "captions.ass"
        build_captions(timings, ass_file)

        out_mp4 = workdir / "video.mp4"
        render_video(voice_wav, ass_file, duration, out_mp4, workdir)

        if args.no_upload:
            sb.table("uploads").update({"status": "rendered"}).eq("id", upload_row["id"]).execute()
            print(f"\nDone (no upload). Video saved at: {out_mp4}")
            return

        video_id, url = upload_to_youtube(out_mp4, speech)
        sb.table("uploads").update(
            {"status": "uploaded", "youtube_video_id": video_id, "youtube_url": url}
        ).eq("id", upload_row["id"]).execute()
        print(f"\nDone! Posted: {url}")

    except Exception as e:
        sb.table("uploads").update({"status": "failed", "error": str(e)[:1000]}).eq("id", upload_row["id"]).execute()
        raise


if __name__ == "__main__":
    main()