BRIGHTWATER BOG PHOTO DROP ZONES
================================

Drop new photos/videos into the folder for their piece, then tell Claude:
"import the drop zones". Claude runs tools/import_drop_zones.py, which
renames, reads EXIF dates/GPS, boomerang-encodes clips, appends manifest
rows and construction keys, then rebuilds the catalog and (when you say
so) deploys.

    .venv/bin/python tools/import_drop_zones.py plan    # writes _plan.json
    .venv/bin/python tools/import_drop_zones.py apply   # carries it out

The plan lists every file with its duplicate status and a proposed
action/folder/era; edit it (or have Claude edit it) before apply. Entries
marked "hold" stay put until decided. Both _plan.json and _imported/ are
git-ignored.

WHAT GOES WHERE
- One folder per trail stop / indoor piece. Originals straight off the
  iPhone (HEIC, JPG, MOV) are all fine — no need to rename or convert.
- Videos welcome: raw .MOV is preferred (Claude boomerang-encodes from it).
- Workshop / build shots: make a subfolder named "construction" inside the
  piece's folder (or just say so) and they'll show under its Construction tab.

NAME GOTCHAS (folder names, not what you'd guess)
- sculpture_11_welcome_sconce = the red crystal sconce set into the standing
  stone by the road
- sculpture_15_mailbox = Samuel's in-progress copper/chevron mailbox for that
  same stone
- saguaro_cactus = the branching amber floor lamp
- dinosaur = the four-point rainbow web canopy in the rafters (not a dinosaur)

SPECIAL FOLDERS
- _unsorted            can't decide? drop here, Claude sorts by content + GPS
- _new_piece           a piece with no stop yet — Claude creates the page too
- _scans_of_old_prints flatbed scans of old prints ("Then" era)
- _drawings_and_cad    plans/sketches/CAD exports (SVG/DXF/PDF ok — Claude
                       converts to PNG; the site can't serve vectors directly)

Folders with no modern photos yet (gourd, jo_bird, mystery_miscellaneous,
sea_lamp, unfinished_shed_dancers): dropping the first photos here makes
Claude create the website folder and wire it to the page.

These folders live in git, but anything you drop in them is git-ignored —
nothing here can end up committed by accident. After import, Claude
hash-checks against the photo tree (so nothing imports twice) and moves the
originals into _imported/ (also ignored), leaving the zones empty again.
