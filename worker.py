#!/usr/bin/env python3
"""Railway-friendly worker that runs the YouTube bot every 30 minutes."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == "" or "PASTE_" in str(value)):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SUPABASE_URL = env("SUPABASE_URL", required=True)
SUPABASE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", required=True)
JOB_NAME = env("JOB_NAME", "youtube_video_scheduler")
RUN_INTERVAL_SECONDS = int(env("RUN_INTERVAL_SECONDS", "1800"))
CHECK_INTERVAL_SECONDS = int(env("CHECK_INTERVAL_SECONDS", "60"))
MAX_RUN_SECONDS = int(env("MAX_RUN_SECONDS", "900"))


def parse_iso(value):
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now():
    return datetime.now(timezone.utc)


def get_state(sb):
    try:
        rows = sb.table("scheduler_state").select("*").eq("job_name", JOB_NAME).limit(1).execute().data
    except Exception as exc:  # pragma: no cover - runtime dependency on Supabase schema
        raise RuntimeError(
            "scheduler_state table is missing. Run the SQL in schema.sql before starting this worker. "
            f"Original error: {exc}"
        ) from exc

    if not rows:
        return None
    return rows[0]


def ensure_state(sb):
    row = get_state(sb)
    if row is not None:
        return row

    created = (
        sb.table("scheduler_state")
        .insert({
            "job_name": JOB_NAME,
            "status": "idle",
            "last_run_at": None,
            "last_error": None,
        })
        .execute()
        .data
    )
    return created[0]


def seconds_until_run(state):
    if state.get("status") == "running":
        return None

    if state.get("status") == "failed":
        last_attempt_at = parse_iso(state.get("updated_at"))
        if last_attempt_at is not None:
            reference_time = last_attempt_at
        else:
            reference_time = parse_iso(state.get("last_run_at"))
    else:
        reference_time = parse_iso(state.get("last_run_at"))

    if reference_time is None:
        return 0

    elapsed = (utc_now() - reference_time).total_seconds()
    return max(0, int(RUN_INTERVAL_SECONDS - elapsed))


def should_run(state):
    remaining = seconds_until_run(state)
    return remaining == 0


def running_age_seconds(state):
    started_at = parse_iso(state.get("updated_at"))
    if started_at is None:
        return None
    return (utc_now() - started_at).total_seconds()


def is_stale_running(state):
    if state.get("status") != "running":
        return False
    age = running_age_seconds(state)
    return age is None or age >= MAX_RUN_SECONDS


def mark_running(sb):
    row = ensure_state(sb)
    sb.table("scheduler_state").update({
        "status": "running",
        "last_error": None,
        "updated_at": utc_now().isoformat(),
    }).eq("job_name", JOB_NAME).execute()
    return row


def mark_success(sb):
    sb.table("scheduler_state").update({
        "status": "idle",
        "last_run_at": utc_now().isoformat(),
        "last_error": None,
        "updated_at": utc_now().isoformat(),
    }).eq("job_name", JOB_NAME).execute()


def mark_failure(sb, error_text):
    message = str(error_text)[:1000]
    sb.table("scheduler_state").update({
        "status": "failed",
        "last_error": message,
        "updated_at": utc_now().isoformat(),
    }).eq("job_name", JOB_NAME).execute()


def recover_stale_run(sb, state):
    age = running_age_seconds(state)
    age_text = "unknown age" if age is None else f"{int(age)} seconds"
    message = f"Recovered stale running state after {age_text}; previous worker may have stopped unexpectedly."
    sb.table("scheduler_state").update({
        "status": "idle",
        "last_error": message,
        "updated_at": utc_now().isoformat(),
    }).eq("job_name", JOB_NAME).execute()
    print(f"[scheduler] {message}")


def run_once():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    state = ensure_state(sb)

    if not should_run(state):
        print(f"[scheduler] Skipping run; last run too recent for {JOB_NAME}.")
        return False

    print(f"[scheduler] Starting scheduled run for {JOB_NAME}...")
    try:
        mark_running(sb)
        subprocess.run([sys.executable, "bot.py"], check=True, timeout=MAX_RUN_SECONDS)
        mark_success(sb)
        print(f"[scheduler] Completed run for {JOB_NAME}.")
        return True
    except Exception as exc:
        mark_failure(sb, exc)
        print(f"[scheduler] Run failed for {JOB_NAME}: {exc}")
        return False


def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    ensure_state(sb)

    print(f"[scheduler] Starting background runner for {JOB_NAME}.")
    print(f"[scheduler] Interval: {RUN_INTERVAL_SECONDS} sec | Poll: {CHECK_INTERVAL_SECONDS} sec")

    while True:
        try:
            state = ensure_state(sb)
            if is_stale_running(state):
                recover_stale_run(sb, state)
                state = ensure_state(sb)

            if should_run(state):
                run_once()
            else:
                remaining = seconds_until_run(state)
                if remaining is None:
                    print(f"[scheduler] A run is already marked active for {JOB_NAME}.")
                else:
                    minutes, seconds = divmod(remaining, 60)
                    print(f"[scheduler] Waiting for next trigger. Next run in {minutes:02d}:{seconds:02d}.")
        except Exception as exc:
            print(f"[scheduler] Error while checking schedule: {exc}")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
