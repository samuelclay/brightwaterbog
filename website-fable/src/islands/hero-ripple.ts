// Animated water-ripple reflection under the hero title.
//
// The SVG in Hero.astro paints a static feTurbulence-displaced reflection;
// animating that filter forces a CPU re-render every frame, so instead this
// island hides the static reflection group and overlays a transparent WebGL
// canvas that redraws the title text mirrored about the same waterline, with
// the displacement done in a fragment shader. The SVG version remains the
// fallback for no-JS, reduced-motion, and no-WebGL visitors.
//
// All geometry is in the SVG's viewBox units (720x170, baseline y=84,
// reflection mirrored via `translate(0 182) scale(1 -1)` => waterline y=91),
// so the canvas lines up with the title at any rendered size.

const TITLE = "Bright Water Bog";
const VB_W = 720; // svg viewBox width
const FONT_SIZE = 84;
const BASELINE_Y = 84;
const CAN_X = -12; // canvas rect in viewBox units — wider and taller than the
const CAN_W = 744; //   svg so displaced pixels aren't clipped at its edges
const CAN_H = 200;
const TEX_W = 760; // texture width in viewBox units (right slack for metrics)
const TEX_H = 170;
const TEX_SCALE = 2;

const FRAG = `
precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_tex;
uniform float u_time;

// Smooth 3D value noise — z is time, so the ripple field undulates in place
// instead of sliding past like waves.
float hash3(vec3 p) {
  return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}
float vnoise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  vec3 u = f * f * (3.0 - 2.0 * f);
  float a = mix(hash3(i),                    hash3(i + vec3(1, 0, 0)), u.x);
  float b = mix(hash3(i + vec3(0, 1, 0)),    hash3(i + vec3(1, 1, 0)), u.x);
  float c = mix(hash3(i + vec3(0, 0, 1)),    hash3(i + vec3(1, 0, 1)), u.x);
  float d = mix(hash3(i + vec3(0, 1, 1)),    hash3(i + vec3(1, 1, 1)), u.x);
  return mix(mix(a, b, u.y), mix(c, d, u.y), u.z);
}
// Two octaves, like the SVG filter's feTurbulence numOctaves="2".
float fbm(vec3 p) {
  return (vnoise(p) + 0.5 * vnoise(p * 2.0)) / 1.5;
}

void main() {
  float vx = ${CAN_X.toFixed(1)} + v_uv.x * ${CAN_W.toFixed(1)};
  float vy = v_uv.y * ${CAN_H.toFixed(1)};

  // Match the static filter: fractal noise at baseFrequency 0.011 x / 0.09 y,
  // displacement scale 22 (=> +-11 viewBox units) on both axes. Time morphs
  // the field (z axis) while the ripples drift down-screen slowly.
  float t = u_time;
  vec3 np = vec3(vx * 0.011, vy * 0.09 - t * 0.06, t * 0.28);
  float dx = 22.0 * (fbm(np) - 0.5);
  float dy = 22.0 * (fbm(np + vec3(37.2, 17.8, 51.3)) - 0.5);

  // Mirror about the waterline: source y = 182 - vy (matches the SVG's
  // translate(0 182) scale(1 -1) on text at baseline 84).
  float sx = vx + dx;
  float sy = 182.0 - vy + dy;
  vec2 tuv = vec2(sx / ${TEX_W.toFixed(1)}, sy / ${TEX_H.toFixed(1)});
  vec4 c = texture2D(u_tex, tuv);
  if (tuv.y < 0.0 || tuv.y > 1.0 || tuv.x < 0.0 || tuv.x > 1.0) c = vec4(0.0);

  gl_FragColor = c * 0.38;
}
`;

const VERT = `
attribute vec2 a_pos;
varying vec2 v_uv;
void main() {
  v_uv = vec2(a_pos.x * 0.5 + 0.5, 0.5 - a_pos.y * 0.5);
  gl_Position = vec4(a_pos, 0.0, 1.0);
}
`;

function drawTextTexture(): HTMLCanvasElement | null {
  const tex = document.createElement("canvas");
  tex.width = TEX_W * TEX_SCALE;
  tex.height = TEX_H * TEX_SCALE;
  const ctx = tex.getContext("2d");
  if (!ctx) return null;
  ctx.scale(TEX_SCALE, TEX_SCALE);
  // Same face/weight as .hero__glass-text; the opsz/SOFT/WONK variation axes
  // aren't reachable from canvas, invisible at 38% opacity under displacement.
  ctx.font = `620 ${FONT_SIZE}px "Fraunces Variable", Georgia, serif`;
  const m = ctx.measureText(TITLE);
  const grad = ctx.createLinearGradient(
    0,
    BASELINE_Y - m.actualBoundingBoxAscent,
    0,
    BASELINE_Y + m.actualBoundingBoxDescent,
  );
  grad.addColorStop(0, "#ffe3ae");
  grad.addColorStop(0.55, "#f2b45a");
  grad.addColorStop(1, "#c97a3d");
  // stroke first = SVG's paint-order: stroke
  ctx.lineWidth = 2.6;
  ctx.lineJoin = "round";
  ctx.strokeStyle = "#4a3414";
  ctx.strokeText(TITLE, 4, BASELINE_Y);
  ctx.fillStyle = grad;
  ctx.fillText(TITLE, 4, BASELINE_Y);
  return tex;
}

function compile(gl: WebGLRenderingContext, type: number, src: string) {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) return null;
  return sh;
}

function init() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const svg = document.querySelector<SVGSVGElement>(".hero__title-glass");
  const staticReflection = svg?.querySelector<SVGGElement>(".hero__reflection");
  const title = svg?.closest<HTMLElement>(".hero__title");
  if (!svg || !staticReflection || !title) return;

  const canvas = document.createElement("canvas");
  canvas.className = "hero__ripple";
  canvas.setAttribute("aria-hidden", "true");
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
    depth: false,
    stencil: false,
  });
  if (!gl) return;

  const vs = compile(gl, gl.VERTEX_SHADER, VERT);
  const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG);
  const prog = gl.createProgram();
  if (!vs || !fs || !prog) return;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "a_pos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const uTime = gl.getUniformLocation(prog, "u_time");

  const uploadText = () => {
    const source = drawTextTexture();
    if (!source) return false;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    return true;
  };
  if (!uploadText()) return;

  title.appendChild(canvas); // .hero__ripple CSS keeps it under the svg text
  staticReflection.style.display = "none";

  const resize = () => {
    const s = svg.getBoundingClientRect().width / VB_W;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.style.left = `${CAN_X * s}px`;
    canvas.style.width = `${CAN_W * s}px`;
    canvas.style.height = `${CAN_H * s}px`;
    canvas.width = Math.max(1, Math.round(CAN_W * s * dpr));
    canvas.height = Math.max(1, Math.round(CAN_H * s * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
  };
  resize();
  new ResizeObserver(resize).observe(svg);

  const t0 = performance.now();
  let raf = 0;
  let visible = true;
  const frame = (now: number) => {
    raf = 0;
    if (!visible) return;
    gl.uniform1f(uTime, (now - t0) / 1000);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    raf = requestAnimationFrame(frame);
  };
  const start = () => {
    if (!raf && visible) raf = requestAnimationFrame(frame);
  };

  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    start();
  }).observe(canvas);

  // Fraunces may still be loading on first paint — redraw the texture with the
  // real face once it lands (fonts.load resolves immediately if cached).
  document.fonts
    .load(`620 ${FONT_SIZE}px "Fraunces Variable"`, TITLE)
    .then(() => uploadText())
    .catch(() => {});

  canvas.addEventListener("webglcontextlost", () => {
    if (raf) cancelAnimationFrame(raf);
    visible = false;
    canvas.remove();
    staticReflection.style.display = "";
  });

  start();
}

init();
