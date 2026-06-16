# brightwaterbog

Archive of the stained glass sculptures at Bright Water Bog.

Photos are digitized on an Epson Perfection V19II scanner, auto-cropped, and
tagged/organized with Claude vision.

## One-shot workflow

Lay one or more photos on the scanner bed, then:

```bash
./digitize.sh                 # 600 dpi color, scan -> crop -> AI tag -> organize
./digitize.sh --dpi 300       # faster, smaller files
./digitize.sh --no-tag        # scan + crop only (no API call)
```

Output: tagged photos land in `photos/<decade>/<category>/`, each with a sidecar
`.json` of metadata (caption, tags, era guess, people, setting, defects).

## Pieces

| Path | What it does |
|------|--------------|
| `scanner/icascan` | Headless CLI driving the scanner via macOS ImageCaptureCore. `icascan list` / `icascan scan --out F --dpi N --color color\|gray`. Build: `swiftc -O scanner/icascan.swift -o scanner/icascan -framework ImageCaptureCore`. |
| `pipeline/crop.py` | OpenCV: finds each photo on the white bed, deskews, writes separate JPEGs. |
| `pipeline/tag.py` | Claude (Opus 4.8) vision tagging → sidecar JSON + `--organize` folder sort. Needs `ANTHROPIC_API_KEY`. |
| `digitize.sh` | Orchestrates scan → crop → tag with scanner process hygiene. |

## Setup notes

- **Scanner is USB bus-powered** — plugging the cable in turns it on; there's no power button.
- Requires the official **Epson Scan 2** driver installed (provides the macOS ICA driver). SANE/`scanimage` does **not** work with this model.
- Python deps live in `.venv` (opencv, numpy, pillow, anthropic). `ANTHROPIC_API_KEY` must be in the environment for tagging.
- If a scan reports "device busy", a prior `icascan` process is still holding it: `pkill -9 -f "icascan scan"` and retry.

## Tuning the cropper

`crop.py --min-area-frac` sets the smallest blob (as a fraction of the full bed)
counted as a photo. Lower it to catch small prints; raise it to ignore specks.
Documents with whitespace gaps may split into multiple regions — that's expected;
it's tuned for solid photo rectangles.
