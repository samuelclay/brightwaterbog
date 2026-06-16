#!/usr/bin/env python3
"""Tag, describe, and categorize a cropped photo with Claude vision.

Reads ANTHROPIC_API_KEY from the environment. For each image, calls Claude
(Opus 4.8) with the image and a structured-output schema, writes a sidecar
<image>.json with the result, and prints a one-line summary.

Usage:
    tag.py IMAGE [IMAGE ...]
    tag.py --organize PHOTOS_ROOT IMAGE [IMAGE ...]   # also sort into folders

With --organize, each tagged photo is hard-linked into
PHOTOS_ROOT/<decade>/<category>/ based on the model's era + category guess.
"""
import sys
import os
import io
import json
import base64
import argparse

try:
    import anthropic
except ImportError:
    print("ERROR: pip install anthropic (in the project venv)", file=sys.stderr)
    sys.exit(1)
from pydantic import BaseModel, Field
from typing import List, Optional
from PIL import Image

MODEL = "claude-opus-4-8"
MAX_EDGE = 1568  # Claude vision works well at this; larger just costs tokens.


class PhotoTags(BaseModel):
    caption: str = Field(description="One short sentence describing the photo.")
    description: str = Field(description="2-3 sentence detailed description.")
    tags: List[str] = Field(description="5-12 lowercase keyword tags for search.")
    category: str = Field(description="One of: portrait, group, landscape, event, "
                                      "document, object, animal, building, other.")
    setting: str = Field(description="indoor, outdoor, or unknown.")
    people_count: int = Field(description="Approximate number of people visible (0 if none).")
    era_guess: str = Field(description="Best guess at the decade, e.g. '1970s', "
                                       "'2000s', or 'unknown'. Use photo style, clothing, "
                                       "color/B&W, and print qualities as cues.")
    location_hint: Optional[str] = Field(description="Any place guess, or null.")
    event_guess: Optional[str] = Field(description="Likely occasion (wedding, birthday, "
                                                   "vacation, holiday), or null.")
    quality_issues: List[str] = Field(description="Visible defects: blurry, faded, "
                                                  "scratched, torn, color-shift, glare, "
                                                  "or empty list.")


PROMPT = (
    "This is a scanned photograph from a personal photo collection being digitized "
    "and organized. Analyze it and return structured metadata. Infer the era from "
    "visual cues (black & white vs color, film grain, clothing, vehicles, print "
    "borders). Be specific and useful for later search and organization. If the image "
    "is actually a document or blank, set category accordingly."
)


def encode_image(path):
    """Downscale to MAX_EDGE and return (media_type, base64)."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = min(1.0, MAX_EDGE / max(w, h))
    if scale < 1.0:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return "image/jpeg", base64.standard_b64encode(buf.getvalue()).decode()


def tag_image(client, path):
    media_type, data = encode_image(path)
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": media_type, "data": data}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        output_format=PhotoTags,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {resp.stop_details}")
    return resp.parsed_output


def organize(image_path, tags, root):
    """Hard-link the image into root/<decade>/<category>/."""
    decade = (tags.era_guess or "unknown").strip() or "unknown"
    cat = (tags.category or "other").strip() or "other"
    dest_dir = os.path.join(root, decade, cat)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(image_path))
    if os.path.abspath(dest) != os.path.abspath(image_path) and not os.path.exists(dest):
        try:
            os.link(image_path, dest)
        except OSError:
            import shutil
            shutil.copy2(image_path, dest)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--organize", metavar="PHOTOS_ROOT", default=None)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set in environment. "
              "Set it and re-run; cropping already produced the images.", file=sys.stderr)
        sys.exit(2)

    client = anthropic.Anthropic()
    failures = 0
    for path in args.images:
        try:
            tags = tag_image(client, path)
        except Exception as e:
            print(f"FAIL {path}: {e}", file=sys.stderr)
            failures += 1
            continue
        sidecar = os.path.splitext(path)[0] + ".json"
        with open(sidecar, "w") as f:
            json.dump(tags.model_dump(), f, indent=2)
        dest = organize(path, tags, args.organize) if args.organize else path
        print(f"{os.path.basename(path)}  [{tags.era_guess}/{tags.category}]  "
              f"{tags.caption}  -> {dest}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
