import fs from "node:fs"; import path from "node:path"; import { fileURLToPath } from "node:url"; import sharp from "sharp";
const SITE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const t = JSON.parse(fs.readFileSync(path.join(SITE,"src/data/trail.json"),"utf8"));
const C={amber:"#f2b45a",cobalt:"#6a93ff",teal:"#35c4a6",garnet:"#e6586f",violet:"#a98bec",rose:"#f191b2",gold:"#f0cf5b"};
const [,,vw,vh]=t.viewBox.split(" ").map(Number);
const nodes=t.nodes.map(p=>`<circle cx="${p.x}" cy="${p.y}" r="2.3" fill="${C[p.glass]||"#888"}" stroke="#0e1512" stroke-width="0.5"/><text x="${p.x}" y="${p.y-3.2}" font-size="2.6" fill="#ece4d4" text-anchor="middle" font-family="monospace">${p.order}</text>`).join("");
const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${vw} ${vh}" width="820" height="${Math.round(820*vh/vw)}"><rect width="${vw}" height="${vh}" fill="#16211c"/><path d="${t.pathD}" fill="none" stroke="#6a93ff" stroke-width="1" stroke-linecap="round" opacity="0.85"/>${nodes}</svg>`;
await sharp(Buffer.from(svg)).png().toFile(process.argv[2]);
console.log("viewBox",t.viewBox,"nodes",t.nodes.length);
