from pathlib import Path
import os
import urllib.request

# These files are too large to keep in GitHub, so Railway downloads them at runtime.
FILES = {
    "kokoro-v1.0.onnx": "https://example.com/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://example.com/voices-v1.0.bin",
}


def download_file(url: str, dest: Path):
    if dest.exists():
        print(f"[prepare_models] Already present: {dest.name}")
        return

    print(f"[prepare_models] Downloading {dest.name} from {url}")
    urllib.request.urlretrieve(url, dest)
    print(f"[prepare_models] Saved {dest.name}")


def main():
    for filename, url in FILES.items():
        download_file(url, Path(filename))

    print("[prepare_models] Model download complete.")


if __name__ == "__main__":
    main()
