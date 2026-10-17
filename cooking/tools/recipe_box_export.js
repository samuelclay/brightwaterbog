// Run in the DevTools console on https://cooking.nytimes.com/recipe-box
// Loads every page of the recipe box in a hidden frame and downloads
// nyt-recipe-box.txt: one <id>-<slug> per line, in recipe-box order.
(async () => {
  const total = +((document.querySelector('main').innerText.match(/of ([\d,]+) results/) || [])[1] || '0').replace(/,/g, '');
  if (!total) return console.error('Open https://cooking.nytimes.com/recipe-box first (no "of N results" found).');
  const pages = Math.ceil(total / 48);
  const seen = new Map();
  const load = page => new Promise(resolve => {
    const f = document.createElement('iframe');
    f.style.cssText = 'position:fixed;left:-9999px;width:1200px;height:900px';
    f.src = `/recipe-box?page=${page}`;
    let tries = 0;
    const want = page < pages ? 48 : total - 48 * (pages - 1);
    const poll = () => {
      const doc = f.contentDocument;
      const links = doc ? [...doc.querySelectorAll('main a[href*="/recipes/"]')]
        .map(a => new URL(a.href).pathname.match(/^\/recipes\/(\d+-[\w-]+)/)?.[1]).filter(Boolean) : [];
      if (new Set(links).size >= want || ++tries > 60) { f.remove(); resolve([...new Set(links)]); }
      else setTimeout(poll, 500);
    };
    f.onload = () => setTimeout(poll, 800);
    document.body.appendChild(f);
  });
  for (let p = 1; p <= pages; p++) {
    const links = await load(p);
    links.forEach(s => { const id = s.split('-')[0]; if (!seen.has(id)) seen.set(id, s); });
    console.log(`page ${p}/${pages}: ${links.length} links (${seen.size} unique so far)`);
  }
  const text = [...seen.values()].join('\n') + '\n';
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([text], {type: 'text/plain'})),
    download: 'nyt-recipe-box.txt',
  });
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Done: ${seen.size} of ${total} recipes -> nyt-recipe-box.txt`);
})();
