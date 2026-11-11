import * as THREE from './vendor/three/build/three.module.js';

// Small reusable primitives for owner-built additions to the scene.
export const mat = color => new THREE.MeshStandardMaterial({ color, roughness: 0.9 });

// Small, deterministic canvas maps are generated once when the room is built.
export function textureMaterial(color, kind) {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, 256, 256);
  if (kind === 'wood') {
    for (let y = 0; y < 256; y += 3) {
      ctx.strokeStyle = `rgba(68,42,28,${.025 + (y % 17) / 700})`;
      ctx.lineWidth = y % 15 === 0 ? 1.3 : .5;
      ctx.beginPath();
      for (let x = 0; x <= 256; x += 8) {
        const yy = y + Math.sin(x * .025 + y * .13) * 1.7;
        if (x === 0) ctx.moveTo(x, yy); else ctx.lineTo(x, yy);
      }
      ctx.stroke();
    }
  } else {
    ctx.lineWidth = 1;
    for (let y = 0; y < 256; y += 4) {
      for (let x = 0; x < 256; x += 5) {
        ctx.strokeStyle = (x + y) % 3 ? 'rgba(255,245,225,.05)' : 'rgba(66,42,32,.04)';
        ctx.beginPath();
        ctx.moveTo(x + ((y / 4) % 2) * 2, y);
        ctx.lineTo(x + 2 + ((y / 4) % 2) * 2, y + 3);
        ctx.stroke();
      }
    }
  }
  const map = new THREE.CanvasTexture(canvas);
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.repeat.set(2, 2);
  return new THREE.MeshStandardMaterial({ map, roughness: .92 });
}

export function box(parent, material, size, position) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

export function sphere(parent, material, radius, position, scale = [1, 1, 1]) {
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 32, 24), material);
  mesh.position.set(...position);
  mesh.scale.set(...scale);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

export function cylinder(parent, material, top, bottom, height, position) {
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(top, bottom, height, 32), material);
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}
