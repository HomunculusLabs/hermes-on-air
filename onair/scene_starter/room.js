import * as THREE from './vendor/three/build/three.module.js';
import { mat, box, sphere, cylinder, textureMaterial } from './geometry.js';

// Paint the view locally: no images or network requests are needed at runtime.
function skyMaterial(night = false) {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  const sky = ctx.createLinearGradient(0, 0, 0, 256);
  sky.addColorStop(0, night ? '#101d39' : '#92b7c2');
  sky.addColorStop(0.7, night ? '#344765' : '#d9ddd0');
  sky.addColorStop(1, night ? '#aa8580' : '#f5d6ab');
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, 256, 256);
  ctx.fillStyle = 'rgba(255, 250, 229, .65)';
  ctx.beginPath();
  ctx.arc(184, 65, 23, 0, Math.PI * 2);
  ctx.fill();
  if (night) {
    ctx.fillStyle = 'rgba(255,245,220,.8)';
    for (let i = 0; i < 22; i++) {
      const x = (i * 83 + 13) % 256, y = (i * 47 + 17) % 160;
      ctx.fillRect(x, y, i % 5 === 0 ? 2 : 1, i % 5 === 0 ? 2 : 1);
    }
  }
  ctx.fillStyle = night ? '#283947' : '#9cad9e';
  ctx.beginPath();
  ctx.moveTo(0, 225);
  ctx.quadraticCurveTo(65, 160, 135, 225);
  ctx.quadraticCurveTo(200, 170, 256, 212);
  ctx.lineTo(256, 256);
  ctx.lineTo(0, 256);
  ctx.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return new THREE.MeshBasicMaterial({ map: texture });
}

function buildFurnished(scene, config, transparent) {
  const wall = mat(config.wall), floor = textureMaterial(config.floor, 'wood'), wood = textureMaterial('#936f55', 'wood');
  const dark = mat('#58483f'), cream = mat('#eee3cb'), accent = mat(config.accent);
  const green = mat('#738c73'), leafLight = mat('#a3b391');
  const room = new THREE.Group();
  scene.add(room);
  room.visible = !transparent;

  box(room, floor, [16, .16, 12], [0, -.12, 0]);
  box(room, wall, [16, 7, .2], [0, 3.4, -2]);
  box(room, cream, [16, .18, .12], [0, .09, -1.85]);
  // Plank seams give the floor depth without a large repeating image.
  const seams = mat('#776b60');
  for (let x = -7; x <= 7; x += .65) {
    // Separate strips stop at the rug instead of relying on coplanar occlusion.
    box(room, seams, [.012, .006, 4.9], [x, -.037, -3.05]).castShadow = false;
    box(room, seams, [.012, .006, 3.6], [x, -.037, 3.70]).castShadow = false;
  }
  box(room, mat('#a79b88'), [4.8, .015, 2.5], [0, -.025, .65]).receiveShadow = false;
  box(room, cream, [4.6, .01, 2.3], [0, -.013, .65]).receiveShadow = false;
  box(room, textureMaterial('#c7baa2', 'weave'), [4.48, .01, 2.18], [0, -.006, .65]).receiveShadow = false;

  // A shallow window recess with a daylight view, sill and simple trim.
  box(room, wood, [2.85, 2.3, .08], [-2.9, 2.9, -1.84]).castShadow = false;
  const view = box(room, skyMaterial(config.theme === 'night-studio'), [2.57, 2.02, .04], [-2.9, 2.9, -1.76]);
  view.castShadow = false;
  view.receiveShadow = false;
  box(room, cream, [.08, 2.02, .08], [-2.9, 2.9, -1.69]).receiveShadow = false;
  box(room, cream, [2.57, .08, .08], [-2.9, 2.9, -1.69]).receiveShadow = false;
  box(room, wood, [3.0, .12, .38], [-2.9, 1.75, -1.62]).castShadow = false;

  // Shelf, books and a simple plant.
  if (config.theme === 'night-studio') {
    const strip = new THREE.MeshBasicMaterial({color: '#8b91dd'});
    box(room, strip, [2.45, .035, .025], [3, 2.60, -1.82]).castShadow = false;
    const glow = new THREE.PointLight('#8b91dd', 1.1, 3, 2);
    glow.position.set(3, 2.55, -1.55); room.add(glow);
  }
  box(room, wood, [2.5, .14, .48], [3, 2.72, -1.58]).castShadow = false;
  for (const x of [2.15, 3.85]) box(room, dark, [.08, .28, .22], [x, 2.54, -1.7]).castShadow = false;
  for (let i = 0; i < 6; i++) {
    const h = .4 + (i % 3) * .09;
    const x = 2.12 + i * .2;
    box(room, [accent, cream, green, dark][i % 4], [.16, h, .27], [x, 2.79 + h / 2, -1.48]).castShadow = false;
    box(room, cream, [.11, .018, .01], [x, 2.86, -1.335]);
  }
  cylinder(room, cream, .20, .15, .32, [3.85, 2.95, -1.48]);
  for (let i = 0; i < 7; i++) {
    const leaf = sphere(room, i % 2 ? green : leafLight, .15,
      [3.85 + Math.sin(i * 2.4) * .14, 3.21 + (i % 3) * .08, -1.48 + Math.cos(i * 2.4) * .08], [.55, 1.55, .55]);
    leaf.rotation.z = Math.sin(i * 2.4) * .5;
    leaf.castShadow = false;
  }
  // Framed abstract landscape, made from layered shapes.
  box(room, dark, [1.05, .8, .08], [3.12, 1.8, -1.84]);
  box(room, cream, [.93, .68, .04], [3.12, 1.8, -1.77]);
  box(room, mat('#aac1bc'), [.78, .53, .02], [3.12, 1.8, -1.73]);
  sphere(room, mat('#e7c992'), .12, [3.32, 1.91, -1.70], [1, 1, .1]);
  box(room, green, [.78, .18, .02], [3.12, 1.63, -1.70]);

  // Decorative wall pieces do not cast distracting marks onto the wall.
  room.traverse(part => { if (part.isMesh) part.castShadow = false; });

  // Keep furniture separate so a standing VRM has an unobstructed area.
  const deskStart = room.children.length;
  const desk = new THREE.Group();

  // Soft contact pools use a single shared procedural map, hidden with the room.
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext('2d');
  const fade = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  fade.addColorStop(0, 'rgba(48,31,23,.28)');
  fade.addColorStop(1, 'rgba(48,31,23,0)');
  ctx.fillStyle = fade; ctx.fillRect(0, 0, 128, 128);
  const contactMap = new THREE.CanvasTexture(canvas);
  const contactMaterial = new THREE.MeshBasicMaterial({map: contactMap, transparent: true, depthWrite: false});
  function contact(x, z, width, depth) {
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, depth), contactMaterial);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set(x, .852, z);
    room.add(mesh);
  }
  contact(-.54, .48, .55, .4); contact(.54, .48, .55, .4);
  contact(.87, .78, .75, .5); contact(1.55, .72, .52, .44);

  // Desk and deliberately readable, familiar objects.
  box(room, wood, [4.6, .16, 1.25], [0, .77, .65]);
  box(room, dark, [4.55, .08, .08], [0, .68, 1.25]);
  box(room, dark, [.16, .7, .8], [-2.05, .34, .65]);
  box(room, dark, [.16, .7, .8], [2.05, .34, .65]);
  cylinder(room, dark, .25, .25, .06, [-1.65, .88, .65]);
  cylinder(room, dark, .035, .035, .72, [-1.65, 1.25, .65]);
  cylinder(room, green, .15, .34, .28, [-1.65, 1.64, .65]);
  const lampInterior = new THREE.MeshStandardMaterial({ color: '#fff0cf', emissive: '#ffd79b', emissiveIntensity: .75, roughness: .75 });
  cylinder(room, lampInterior, .30, .30, .02, [-1.65, 1.495, .65]).castShadow = false;
  const lampGlow = new THREE.PointLight('#ffcf91', config.theme === 'night-studio' ? 2.6 : 1.2, 2.8, 2);
  lampGlow.position.set(-1.65, 1.45, .65);
  room.add(lampGlow);
  const ceramic = new THREE.MeshStandardMaterial({color: '#eee3cb', roughness: .32, metalness: 0});
  cylinder(room, ceramic, .19, .16, .3, [1.55, 1.0, .72]);
  cylinder(room, dark, .145, .145, .012, [1.55, 1.155, .72]);
  const handle = new THREE.Mesh(new THREE.TorusGeometry(.10, .025, 12, 32), ceramic);
  handle.position.set(1.73, 1.02, .72);
  room.add(handle);
  // A notebook and pencil make the desk feel inhabited without printed marks.
  const notebook = box(room, cream, [.55, .035, .36], [.87, .875, .78]);
  notebook.rotation.y = -.12;
  const cover = box(room, green, [.58, .018, .38], [.87, .849, .78]);
  cover.rotation.y = -.12;
  const pencil = cylinder(room, accent, .013, .013, .48, [.89, .907, .8]);
  pencil.rotation.z = Math.PI / 2;
  pencil.rotation.y = -.18;
  for (const child of room.children.slice(deskStart)) desk.add(child);
  room.add(desk);
  room.userData.setPerformer = standing => { desk.visible = !standing; };
  room.userData.deskEnabled = () => desk.visible;

  // Unlettered tally box: only the face glows, and only when the show is live.
  box(room, dark, [.40, .22, .12], [1.85, 2.16, -1.70]).castShadow = false;
  const tallyMaterial = new THREE.MeshStandardMaterial({color: '#492222', emissive: '#f03224', emissiveIntensity: 0, roughness: .75});
  box(room, tallyMaterial, [.33, .15, .03], [1.85, 2.16, -1.62]).castShadow = false;
  room.userData.setTally = (live, pulse = 0) => {
    tallyMaterial.emissiveIntensity = live ? 1.8 + pulse : 0;
  };

  // Steam is a pair of translucent, prebuilt curves; animate without allocations.
  const steamMaterial = new THREE.MeshBasicMaterial({color: '#fff5e8', transparent: true, opacity: .25, depthWrite: false});
  const steam = [];
  for (let i = 0; i < 2; i++) {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(0, 0, 0), new THREE.Vector3(.025, .09, 0),
      new THREE.Vector3(-.018, .18, 0), new THREE.Vector3(.02, .28, 0)]);
    const wisp = new THREE.Mesh(new THREE.TubeGeometry(curve, 20, .006, 5, false), steamMaterial);
    wisp.position.set(1.51 + i * .08, 1.17, .72);
    desk.add(wisp);
    steam.push(wisp);
  }
  room.userData.animate = t => {
    steam[0].position.x = 1.51 + Math.sin(t * 1.1) * .012;
    steam[1].position.x = 1.59 + Math.sin(t * 1.1 + 1.5) * .012;
  };
  return room;
}

function buildStage(scene, config, transparent) {
  const room = new THREE.Group(); scene.add(room); room.visible = !transparent;
  // An extruded quarter-circle joins floor and wall without a hard corner.
  const shape = new THREE.Shape();
  shape.moveTo(-6, 0); shape.lineTo(1, 0);
  shape.quadraticCurveTo(2, 0, 2, 1);
  shape.lineTo(2, 7); shape.lineTo(2.05, 7);
  shape.lineTo(2.05, -.08); shape.lineTo(-6, -.08); shape.closePath();
  const sweep = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, {depth: 16, bevelEnabled: false, curveSegments: 32}), mat(config.wall));
  sweep.rotation.y = Math.PI / 2; sweep.position.x = -8; sweep.receiveShadow = true; room.add(sweep);
  const plinth = mat(config.floor), green = mat('#79958b');
  cylinder(room, plinth, .42, .48, .8, [-2.65, .4, -1]);
  cylinder(room, mat('#e9e6d8'), .25, .20, .36, [-2.65, .98, -1]);
  for (let i = 0; i < 7; i++) {
    const leaf = sphere(room, green, .18, [-2.65 + Math.sin(i * 2.4) * .18, 1.32 + (i % 3) * .1, -1 + Math.cos(i * 2.4) * .12], [.5, 1.5, .5]);
    leaf.rotation.z = Math.sin(i * 2.4) * .55;
  }
  // A low podium grounds the seated mascot without filling the clean backdrop.
  const podium = mat('#b4c3bd'), top = mat('#e2e8df');
  const desk = new THREE.Group(); room.add(desk);
  box(desk, podium, [2.8, .76, .75], [0, .38, .68]);
  box(desk, top, [2.9, .10, .9], [0, .81, .68]);
  const dark = mat('#514e50');
  const tally = new THREE.Group(); room.add(tally);
  box(tally, dark, [.27, .14, .06], [1.0, .57, 1.08]).castShadow = false;
  const face = new THREE.MeshStandardMaterial({color: '#492222', emissive: '#f03224', emissiveIntensity: 0, roughness: .75});
  box(tally, face, [.21, .09, .02], [1.0, .57, 1.12]).castShadow = false;
  room.userData.setPerformer = standing => {
    desk.visible = !standing;
    tally.position.set(0, standing ? 1.5 : 0, standing ? -3.05 : 0);
  };
  room.userData.deskEnabled = () => desk.visible;
  room.userData.setTally = (live, pulse = 0) => { face.emissiveIntensity = live ? 1.8 + pulse : 0; };
  room.userData.animate = () => {};
  return room;
}

// Each theme supplies palette defaults, rig colours and a room builder.
export const themes = {
  'cozy-day': {palette: {wall: '#c5c9bb', floor: '#85796d', accent: '#bc795e'},
    lights: {ambient: '#fff2df', key: '#ffe5c8', fill: '#fff0dd', rim: '#e4edf1'}, build: buildFurnished},
  'night-studio': {palette: {wall: '#394557', floor: '#514c54', accent: '#c28172'},
    lights: {ambient: '#d6dcf2', key: '#ffe5c8', fill: '#d8e2ff', rim: '#aabde8'}, build: buildFurnished},
  'clean-stage': {palette: {wall: '#e1e5df', floor: '#b3c2b9', accent: '#b6796c'},
    lights: {ambient: '#ffffff', key: '#fff7ed', fill: '#f2f7ff', rim: '#e1f1eb'}, build: buildStage}
};
export function resolveTheme(name) {
  if (Object.prototype.hasOwnProperty.call(themes, name)) return name;
  if (name) console.warn(`Unknown scene theme: ${name}; using cozy-day`);
  return 'cozy-day';
}
// Owner extension point: a custom room may replace this function wholesale.
export function buildRoom(scene, config, transparent) {
  const name = resolveTheme(config.theme);
  return themes[name].build(scene, config, transparent);
}
