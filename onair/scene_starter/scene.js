import * as THREE from './vendor/three/build/three.module.js';
import { buildRoom, resolveTheme, themes } from './room.js';
import { buildLighting } from './lighting.js';
import { createCharacter } from './character.js';
import { connectEvents } from './events.js';
import { createQualityController } from './quality.js';

const saved = await fetch('./scene.json').then(r => r.json());
const params = new URLSearchParams(location.search);
const themeName = resolveTheme(params.get('theme') || saved.theme);
const theme = themes[themeName];
// Legacy top-level colours remain supported for the original room.
const config = {...saved, ...theme.palette, ...(themeName === 'cozy-day' ? {wall: saved.wall || theme.palette.wall, floor: saved.floor || theme.palette.floor, accent: saved.accent || theme.palette.accent} : {}), ...(saved.palette || {}), theme: themeName};
const transparent = params.get('bg') === 'transparent';
const requestedShot = params.get('shot') || config.shot;
const shot = ['wide', 'medium', 'close'].includes(requestedShot) ? requestedShot : 'wide';
document.body.dataset.theme = themeName; document.body.dataset.shot = shot;
document.body.dataset.background = transparent ? 'transparent' : 'room';
const scene = new THREE.Scene();
if (!transparent) scene.background = new THREE.Color(config.wall);
const camera = new THREE.PerspectiveCamera(32, innerWidth / innerHeight, 0.1, 100);
camera.position.fromArray(config.camera || [0, 2.2, 7.7]);
if (shot !== 'wide') {
  const factor = shot === 'medium' ? .72 : .50;
  camera.position.x *= factor; camera.position.y = 2.05 + (camera.position.y - 2.05) * factor; camera.position.z *= factor;
}
camera.lookAt(0, shot === 'wide' ? 1.65 : 2.0, 0);
const requestedQuality = params.get('quality') || config.quality || 'auto';
let currentQuality = requestedQuality === 'low' ? 'low' : 'high';
const pixelRatio = () => currentQuality === 'low' ? .5 : Math.min(Math.max(devicePixelRatio, innerWidth <= 640 ? 2 : 1), 2);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(pixelRatio()); renderer.setSize(innerWidth, innerHeight);
renderer.shadowMap.enabled = currentQuality !== 'low'; renderer.shadowMap.type = THREE.PCFShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.0;
// Paint a safe loading surface before the async character load or the first frame.
// Keep this on the canvas, so the privacy curtain's body background still wins.
renderer.domElement.style.backgroundColor = transparent ? 'transparent' : config.wall;
renderer.setClearColor(config.wall, transparent ? 0 : 1);
renderer.clear();
document.body.appendChild(renderer.domElement);
const lighting = buildLighting(scene, theme.lights); const room = buildRoom(scene, config, transparent);
const character = await createCharacter(scene, config);
if (typeof room.userData.setPerformer === 'function') room.userData.setPerformer(document.body.dataset.avatar === 'vrm');
if (typeof character.frameCamera === 'function') character.frameCamera(camera, shot);
// A fixed pool: no new geometry, materials or particle objects during reactions.
const count = 36, positions = new Float32Array(count * 3);
const particlesGeometry = new THREE.BufferGeometry();
particlesGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3).setUsage(THREE.DynamicDrawUsage));
const particleColor = themeName === 'clean-stage' ? '#267b80' : '#ffe3a9';
// One reusable radial alpha sprite keeps point corners transparent at every scale.
const spriteCanvas = document.createElement('canvas');
spriteCanvas.width = spriteCanvas.height = 64;
const spriteContext = spriteCanvas.getContext('2d');
const spriteGradient = spriteContext.createRadialGradient(32, 32, 5, 32, 32, 32);
spriteGradient.addColorStop(0, 'rgba(255,255,255,1)');
spriteGradient.addColorStop(.65, 'rgba(255,255,255,.85)');
spriteGradient.addColorStop(1, 'rgba(255,255,255,0)');
spriteContext.fillStyle = spriteGradient; spriteContext.fillRect(0, 0, 64, 64);
const sprite = new THREE.CanvasTexture(spriteCanvas);
const particles = new THREE.Points(particlesGeometry, new THREE.PointsMaterial({color: particleColor, map: sprite, size: .055, transparent: true, opacity: .8, depthWrite: false}));
particles.visible = false; room.add(particles);
const quality = createQualityController(requestedQuality, value => {
  currentQuality = value;
  document.body.dataset.quality = value;
  renderer.setPixelRatio(pixelRatio()); renderer.setSize(innerWidth, innerHeight);
  renderer.shadowMap.enabled = value !== 'low';
  lighting.setQuality(value);
  if (value === 'low') particles.visible = false;
});
// A hidden page may stop receiving animation callbacks entirely.
document.addEventListener('visibilitychange', () => quality.sample(performance.now(), false));
let alertStart = -Infinity, stageLive = false;
document.body.dataset.alert = 'false';
const stage = {
  setPresence(value) {
    stageLive = value === 'live';
    lighting.setPresence(value);
    if (typeof room.userData.setTally === 'function') room.userData.setTally(stageLive);
    if (!stageLive) { particles.visible = false; alertStart = -Infinity; document.body.dataset.alert = 'false'; }
  },
  alert() { alertStart = performance.now(); particles.visible = quality.mode !== 'low'; document.body.dataset.alert = 'true'; }
};
// Read-only diagnostics for private browser verification; never writes show state.
renderer.domElement.onAirStatus = () => ({...character.status(),
  deskEnabled: typeof room.userData.deskEnabled === 'function' ? room.userData.deskEnabled() : null,
  pixelRatio: renderer.getPixelRatio(), shadowsEnabled: renderer.shadowMap.enabled});
connectEvents(character, config, stage);
const timer = new THREE.Timer();
function frame() {
  requestAnimationFrame(frame);
  timer.update();
  const delta = Math.min(timer.getDelta(), .1), now = performance.now();
  quality.sample(now, !document.hidden);
  lighting.update(now);
  const age = (now - alertStart) / 1000;
  if (age < 2.5 && stageLive) {
    if (particles.visible) {
      for (let i = 0; i < count; i++) {
        const angle = i * 2.39996, speed = .35 + (i % 7) * .055;
        positions[i * 3] = Math.cos(angle) * speed * age;
        positions[i * 3 + 1] = 2.1 + (1.1 + (i % 5) * .08) * age - .6 * age * age;
        positions[i * 3 + 2] = -.3 + Math.sin(angle) * speed * age;
      }
      particlesGeometry.attributes.position.needsUpdate = true;
      particles.material.opacity = Math.min(.8, (2.5 - age) * .8);
    }
    if (typeof room.userData.setTally === 'function') room.userData.setTally(true, .8 * (1 + Math.sin(age * 10)));
  } else if (Number.isFinite(alertStart)) {
    particles.visible = false; alertStart = -Infinity; document.body.dataset.alert = 'false';
    if (typeof room.userData.setTally === 'function') room.userData.setTally(stageLive);
  }
  character.update(timer.getElapsed(), delta);
  if (room.visible && typeof room.userData.animate === 'function') room.userData.animate(timer.getElapsed());
  renderer.render(scene, camera);
}
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setPixelRatio(pixelRatio());
  renderer.setSize(innerWidth, innerHeight);
});
frame();
