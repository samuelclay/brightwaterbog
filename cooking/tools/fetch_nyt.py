#!/usr/bin/env python3
"""Fetch NYT Cooking recipe pages listed in data/nyt/recipes.txt (one
"<id>-<slug>" per line, as saved from the recipe box) and store each page's
schema.org Recipe JSON-LD as data/nyt/raw/<id>.json. NYT's own recipe object
(`scoopRecipe` inside the page's __NEXT_DATA__ — it has the tips, display
time, yield, typed tags and ingredient-group headings the JSON-LD lacks) is
attached under `_scoop`, and the raw HTML is kept in data/nyt/raw/html/.

Already-saved ids are skipped, so re-running only fetches new saves (and
retries earlier failures). Sequential with a short sleep — be polite.
`--rescoop` re-extracts `_scoop` from the saved HTML without refetching.
"""
import glob, gzip, json, os, re, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "data", "nyt", "recipes.txt")
RAW = os.path.join(ROOT, "data", "nyt", "raw")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=40) as r:
        body = r.read()
    if body[:2] == b"\x1f\x8b":  # the CDN sometimes gzips regardless of Accept-Encoding
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def recipe_ld(html):
    for b in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            d = json.loads(b)
        except json.JSONDecodeError:
            continue
        for it in (d if isinstance(d, list) else [d]):
            t = it.get("@type")
            if t == "Recipe" or (isinstance(t, list) and "Recipe" in t):
                return it
    return None


def scoop_recipe(html):
    i = html.find('id="__NEXT_DATA__"')
    if i < 0:
        return None
    j = html.find(">", i) + 1
    k = html.find("</script>", j)
    try:
        nd = json.loads(html[j:k])
    except json.JSONDecodeError:
        return None
    return (nd.get("props") or {}).get("pageProps", {}).get("scoopRecipe")


def rescoop():
    for f in sorted(glob.glob(os.path.join(RAW, "*.json"))):
        rid = os.path.basename(f)[:-5]
        hf = os.path.join(RAW, "html", f"{rid}.html")
        if not os.path.exists(hf):
            print("no html for", rid)
            continue
        ld = json.load(open(f))
        ld["_scoop"] = scoop_recipe(open(hf).read())
        json.dump(ld, open(f, "w"), ensure_ascii=False, indent=1)
        print("rescooped", rid, "ok" if ld["_scoop"] else "MISSING")


def main():
    if "--rescoop" in sys.argv:
        return rescoop()
    slugs = [s.strip() for s in open(LIST) if s.strip()]
    os.makedirs(os.path.join(RAW, "html"), exist_ok=True)
    fails_f = os.path.join(RAW, "_fails.txt")
    fails = []
    for i, slug in enumerate(slugs, 1):
        rid = slug.split("-", 1)[0]
        f = os.path.join(RAW, f"{rid}.json")
        if os.path.exists(f):
            continue
        url = f"https://cooking.nytimes.com/recipes/{slug}"
        try:
            html = fetch(url)
            ld = recipe_ld(html)
            if not ld:
                raise RuntimeError("no Recipe ld+json in page")
            ld["_slug"] = slug
            ld["_scoop"] = scoop_recipe(html)
            open(os.path.join(RAW, "html", f"{rid}.html"), "w").write(html)
            json.dump(ld, open(f, "w"), ensure_ascii=False, indent=1)
            print(f"{i:3d}/{len(slugs)} ok   {rid} {ld.get('name')}", flush=True)
        except Exception as e:
            print(f"{i:3d}/{len(slugs)} FAIL {rid} {e}", flush=True)
            fails.append(slug)
        time.sleep(0.7)
    if fails:
        open(fails_f, "w").write("\n".join(fails) + "\n")
        print(f"{len(fails)} failed -> {fails_f} (re-run to retry)")
    elif os.path.exists(fails_f):
        os.remove(fails_f)


if __name__ == "__main__":
    main()
