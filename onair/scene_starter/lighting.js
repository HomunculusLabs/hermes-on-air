import * as THREE from './vendor/three/build/three.module.js';

export function buildLighting(scene, colors = {}) {
  const ambient = new THREE.HemisphereLight(colors.ambient || '#fff2df', '#a8a89b', 1.65);
  scene.add(ambient);
  const key = new THREE.DirectionalLight(colors.key || '#ffe5c8', 1.45);
  key.position.set(-3, 6, 5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -4.5;
  key.shadow.camera.right = 4.5;
  key.shadow.camera.top = 4.5;
  key.shadow.camera.bottom = -4.5;
  key.shadow.camera.near = 0.5;
  key.shadow.camera.far = 16;
  key.shadow.bias = -0.00015;
  key.shadow.normalBias = 0.025;
  key.shadow.radius = 3;
  scene.add(key);
  const fill = new THREE.DirectionalLight(colors.fill || '#fff0dd', 0.8);
  fill.position.set(4, 3, 3);
  scene.add(fill);
  const rim = new THREE.DirectionalLight(colors.rim || '#e4edf1', .45);
  rim.position.set(2, 4, -4);
  scene.add(rim);
  let start = 0, target = 0, since = performance.now();
  return {
    setQuality(value) { fill.visible = rim.visible = value !== 'low'; },
    setPresence(value) {
      const now = performance.now();
      start = start + (target - start) * Math.min(1, (now - since) / 600);
      target = value === 'live' ? 1 : 0;
      since = now;
    },
    update(now) {
      const level = start + (target - start) * Math.min(1, (now - since) / 600);
      ambient.intensity = .5 + 1.15 * level;
      key.intensity = .18 + 1.27 * level;
      fill.intensity = .2 + .6 * level;
      rim.intensity = .15 + .3 * level;
    }
  };
}
