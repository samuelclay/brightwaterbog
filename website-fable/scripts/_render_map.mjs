import fs from "node:fs"; import path from "node:path"; import { fileURLToPath } from "node:url"; import sharp from "sharp";
const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const t = JSON.parse(fs.readFileSync(path.join(SITE,"src/data/trail.json"),"utf8"));
const C={amber:"#f2b45a",cobalt:"#6a93ff",teal:"#35c4a6",garnet:"#e6586f",violet:"#a98bec",rose:"#f191b2",gold:"#f0cf5b"};
const dots=t.nodes.map((p,i)=>`<circle cx="${p.x}" cy="${p.y}" r="2.9" fill="#16211c" stroke="${C[p.glass]||'#888'}" stroke-width="1.1"/><text x="${p.x}" y="${p.y+1}" font-size="2.6" fill="#ece4d4" text-anchor="middle" font-family="monospace">${i+1}</text>`).join("");
// dark-theme casing: dark shadow(6) + came base(2.6) + glass progress(3.2, ~60%)
const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="720" height="720"><rect width="100" height="100" fill="#16211c"/>
<path d="${t.loopD}" fill="none" stroke="#000" stroke-opacity="0.45" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
<path d="${t.loopD}" fill="none" stroke="#3b473f" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
<path d="${t.pathD}" fill="none" stroke="#f2b45a" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round" pathLength="1" stroke-dasharray="0.6 1"/>
${dots}</svg>`;
await sharp(Buffer.from(svg)).png().toFile(process.argv[2]);
console.log("ok; loop closes:", t.loopD.slice(-40));
