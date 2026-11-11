import * as THREE from './vendor/three/build/three.module.js';
import { createAvatar } from './avatar.js';
import { mat, sphere, textureMaterial } from './geometry.js';

export async function createCharacter(scene, config) {
  document.body.dataset.avatar = 'starter';
  document.body.dataset.avatarPose = 'starter';
  const presence = await fetch('./avatar-status.json').then(r => r.json());
  if (presence.avatar) {
    try {
      const avatar = await createAvatar(scene, config);
      document.body.dataset.avatar = 'vrm';
      window.__sceneChar = avatar;
      return avatar;
    } catch {
      document.body.dataset.avatar = 'fallback-load-error';
    }
  }
  // Private verification handle; the scene itself never writes show state.
  const starter = await createStarter(scene, config);
  window.__sceneChar = starter;
  return starter;
}

async function createStarter(scene, config) {
  const fur = mat('#ead2b0'), blush = mat('#dca99a'), ink = mat('#493a37');
  const lipColor = mat('#d5b09c');
  const accent = mat(config.accent), collar = mat('#f1e5d0');
  const character = new THREE.Group();
  character.position.set(0, .1, 0);
  character.scale.setScalar(config.characterScale || 1);
  scene.add(character);

  // A soft torso, separate ribbing and a short neck keep the silhouette legible.
  const knit = textureMaterial(config.accent, 'knit');
  const torso = sphere(character, knit, .62, [0, 1.22, 0], [1.05, 1.03, .68]);
  sphere(character, collar, .27, [0, 1.665, .15], [1.13, .40, .75]);
  sphere(character, fur, .19, [0, 1.73, .05], [1, 1.2, .8]);
  for (let i = -5; i <= 5; i++) {
    const rib = sphere(character, mat('#ddd0bc'), .012, [i * .046, 1.64, .292 - Math.abs(i) * .008], [.35, 2, .25]);
    rib.castShadow = false;
  }
  for (const sign of [-1, 1]) {
    const shoulder = sphere(character, knit, .21, [sign * .37, 1.34, .16], [1.05, 1.15, .85]);
    shoulder.rotation.z = sign * .5;
    const arm = sphere(character, knit, .19, [sign * .45, 1.11, .33], [.9, 1.65, .85]);
    arm.rotation.z = sign * .7;
    const cuff = sphere(character, collar, .13, [sign * .515, .925, .45], [1.05, .55, .8]);
    cuff.rotation.z = sign * .2;
    sphere(character, fur, .16, [sign * .54, .855, .48], [1.08, .65, .9]);
    sphere(character, fur, .075, [sign * .47, .855, .56], [.8, .65, .8]);
    for (let i = 0; i < 3; i++) {
      const toe = sphere(character, lipColor, .018, [sign * (.49 + i * .042), .91, .585 - Math.abs(i - 1) * .009], [.65, .3, .25]);
      toe.castShadow = false;
    }
  }
  const face = new THREE.Group();
  character.add(face);
  for (const sign of [-1, 1]) {
    const ear = sphere(face, fur, .19, [sign * .43, 2.44, .0], [.72, 1.35, .75]);
    ear.rotation.z = -sign * .35;
    const innerEar = sphere(face, blush, .10, [sign * .43, 2.47, .12], [.55, 1.05, .22]);
    innerEar.rotation.z = -sign * .35;
    innerEar.castShadow = false;
  }
  const head = sphere(face, fur, .52, [0, 2.15, .02], [1.06, 1, .94]);
  head.castShadow = false;
  head.receiveShadow = false;
  const eyes = [], glints = [], brows = [], cheeks = [];
  for (const sign of [-1, 1]) {
    const eye = sphere(face, ink, .056, [sign * .19, 2.18, .469], [.8, 1.2, .3]);
    eye.castShadow = false;
    eyes.push(eye);
    const glint = sphere(face, collar, .012, [sign * .19 - .012, 2.204, .486], [1, 1, .35]);
    glint.castShadow = false;
    glints.push(glint);
    const brow = sphere(face, ink, .055, [sign * .19, 2.30, .445], [1.2, .19, .18]);
    brow.castShadow = false;
    brows.push(brow);
    const cheek = sphere(face, blush, .07, [sign * .32, 2.06, .423], [1, .48, .12]);
    cheek.castShadow = false;
    cheeks.push(cheek);
  }
  sphere(face, ink, .035, [0, 2.08, .51], [1, .7, .3]).castShadow = false;
  const chin = sphere(face, fur, .11, [0, 1.91, .478], [1.15, .55, .12]);
  chin.castShadow = false;
  // A shallow layered cavity: soft lip rim, dark interior and a lower tongue.
  const lip = sphere(face, lipColor, .10, [0, 1.955, .525], [1.06, .45, .06]);
  lip.castShadow = false;
  const mouth = sphere(face, ink, .085, [0, 1.955, .533], [1, .14, .05]);
  mouth.castShadow = false;
  const tongue = sphere(face, mat('#be807b'), .048, [0, 1.93, .539], [1, .3, .05]);
  tongue.castShadow = false;
  const smileCurve = new THREE.QuadraticBezierCurve3(
    new THREE.Vector3(-.09, 2.0, .505), new THREE.Vector3(0, 1.95, .51),
    new THREE.Vector3(.09, 2.0, .505));
  const smile = new THREE.Mesh(new THREE.TubeGeometry(smileCurve, 16, .008, 6, false), ink);
  face.add(smile);
  // Rotate the whole face about its base, not individual features.
  for (const part of face.children) part.position.y -= 2.15;
  face.position.y = 2.15;
  let speaking = false, openness = 0;
  let voice = null, voiceStart = 0, voiceWindow = 0, voiceDuration = 0, lastMouthReport = 0;
  let mood = 'neutral', moodUntil = 0, lookX = 0, lookY = 0, lookUntil = 0;
  let reaction = '', reactionUntil = 0;
  const mouthLabels = Array.from({length: 101}, (_, i) => (i / 100).toFixed(2));
  return {
    status() {
      return {mouthScale: mouth.scale.y, aa: null};
    },
    setVoice(levels, start, windowMs, durationMs) {
      voice = levels; voiceStart = start; voiceWindow = windowMs; voiceDuration = durationMs;
    },
    setSpeaking(value) {
      speaking = value;
      smile.visible = !value;
      mouth.visible = value;
      lip.visible = value;
      tongue.visible = value;
      if (!value) {
        openness = 0; voice = null;
        document.body.dataset.mouth = '0';
        mouth.scale.y = .14;
        lip.scale.y = .2;
        tongue.visible = false;
      }
    },
    setMood(name) {
      mood = ['neutral', 'happy', 'surprised', 'sad', 'relaxed', 'thinking'].includes(name) ? name : 'neutral';
      moodUntil = performance.now() + 6000;
    },
    glance(xNorm, yNorm, ms) {
      lookX = Math.max(-1, Math.min(1, Number(xNorm) || 0));
      lookY = Math.max(-1, Math.min(1, Number(yNorm) || 0));
      lookUntil = performance.now() + Math.max(0, Math.min(5000, Number(ms) || 0));
    },
    react(kind) { reaction = kind; reactionUntil = performance.now() + 1000; },
    setPresence(state) { if (state !== 'live') this.setSpeaking(false); },
    update(t, delta) {
      const blink = Math.max(0, 1 - Math.abs((t % 4.5) - 4.3) / .12);
      const now = performance.now();
      if (now > moodUntil) mood = 'neutral';
      const glance = (Math.sin(t * .47) > .83 ? .009 : 0) + (now < lookUntil ? lookX * .025 : 0);
      const breath = Math.sin(t * 1.4);
      torso.scale.y = 1.03 + breath * .009;
      face.rotation.z = .012 + Math.sin(t * .65) * .018 + (now < reactionUntil && reaction === 'greet' ? .10 : 0);
      face.rotation.x = (speaking ? Math.sin(t * 7.1) * .009 * openness : 0) + (now < reactionUntil && reaction === 'nod' ? .07 : 0) - (now < lookUntil ? lookY * .025 : 0);
      eyes[0].scale.y = eyes[1].scale.y = Math.max(.08, 1.2 * (1 - blink));
      for (let i = 0; i < 2; i++) {
        eyes[i].position.x = (i ? .19 : -.19) + glance;
        glints[i].position.x = eyes[i].position.x - .012;
        glints[i].visible = blink < .5;
        brows[i].position.y = .15 + (speaking ? openness * .018 : 0) + (mood === 'surprised' || (now < reactionUntil && reaction === 'alert') ? .035 : mood === 'sad' ? -.018 : 0);
        brows[i].rotation.z = (i ? -1 : 1) * (mood === 'sad' ? .20 : mood === 'happy' ? -.12 : 0);
        cheeks[i].scale.y = .48 - openness * .07;
      }
      const elapsed = now - voiceStart;
      if (voice && speaking) {
        const index = Math.floor(elapsed / voiceWindow);
        openness = elapsed >= 0 && elapsed < voiceDuration && index < voice.length ? voice[index] : 0;
      } else {
        // Text-only captions retain the original procedural syllabic cadence.
        const syllable = .5 + .5 * Math.sin(t * 8.1 + .8 * Math.sin(t * 2.7));
        const stress = .68 + .32 * Math.sin(t * 3.3 + 1.1);
        const target = speaking ? Math.max(.02, Math.min(1, syllable * stress * 1.45)) : 0;
        openness += (target - openness) * (1 - Math.exp(-delta * 20));
      }
      if (now - lastMouthReport >= 100) {
        document.body.dataset.mouth = mouthLabels[Math.round(openness * 100)]; lastMouthReport = now;
      }
      const round = .5 + .5 * Math.sin(t * 4.3 + .7);
      const closed = !speaking || openness < .10;
      smile.visible = closed;
      mouth.visible = lip.visible = !closed;
      mouth.scale.y = .14 + openness * 1.05;
      mouth.scale.x = 1.24 - round * openness * .4;
      lip.scale.y = .2 + openness * 1.05;
      lip.scale.x = 1.24 - round * openness * .4;
      mouth.position.y = -.195 - openness * .014;
      lip.position.y = mouth.position.y;
      tongue.position.y = mouth.position.y - openness * .042;
      chin.position.y = -.24 - openness * .022;
      tongue.scale.x = .7 + openness * .3;
      tongue.visible = speaking && openness > .25;
    }
  };
}
