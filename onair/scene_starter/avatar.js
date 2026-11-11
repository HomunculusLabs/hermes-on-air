import * as THREE from './vendor/three/build/three.module.js';
import { GLTFLoader } from './vendor/three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from './vendor/three-vrm.module.js';
import { armTarget, moodChannels } from './avatar_logic.js';

const MOUTHS = ['aa', 'ih', 'ou', 'ee', 'oh'];
const MOODS = ['happy', 'sad', 'surprised', 'relaxed'];
const AXIS_Z = new THREE.Vector3(0, 0, 1);
const labels = Array.from({length: 101}, (_, i) => (i / 100).toFixed(2));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

// The model and all of its optional features are independently optional.
export async function createAvatar(scene, config) {
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser));
  const vrm = (await loader.loadAsync('./avatar.vrm')).userData.vrm;
  if (!vrm || !vrm.scene) throw new Error('Not a VRM');
  VRMUtils.rotateVRM0(vrm);
  const model = vrm.scene, humanoid = vrm.humanoid, expressions = vrm.expressionManager;
  const bone = name => humanoid?.getNormalizedBoneNode(name) || null;
  const head = bone('head'), spine = bone('spine'), chest = bone('chest') || bone('upperChest');
  const hips = bone('hips');
  const leftArm = bone('leftUpperArm'), rightArm = bone('rightUpperArm');
  const leftElbow = bone('leftLowerArm'), rightElbow = bone('rightLowerArm');
  const q = new THREE.Quaternion(), inverse = new THREE.Quaternion();
  const v = new THREE.Vector3(), w = new THREE.Vector3(), target = new THREE.Vector3();
  model.updateMatrixWorld(true);
  const pose = config.avatarPose === 'tpose' ? 'tpose' : 'relaxed';
  function lowerArm(arm, elbow) {
    if (!arm || !elbow || !arm.parent) return false;
    arm.getWorldPosition(v); elbow.getWorldPosition(w); w.sub(v);
    if (w.lengthSq() < 1e-8) return false;
    const desired = armTarget(w.x < 0 ? -1 : 1);
    // A touch of asymmetry keeps the relaxed stance from reading as a rigid mirror pose.
    desired[0] *= w.x < 0 ? 1 : .82;
    target.fromArray(desired);
    arm.parent.getWorldQuaternion(inverse).invert();
    w.applyQuaternion(inverse).normalize(); target.applyQuaternion(inverse).normalize();
    q.setFromUnitVectors(w, target); arm.quaternion.premultiply(q);
    model.updateMatrixWorld(true);
    return true;
  }
  let posed = false;
  if (pose === 'relaxed') {
    posed = lowerArm(leftArm, leftElbow) || posed;
    posed = lowerArm(rightArm, rightElbow) || posed;
    // Normalised bone frames provide a consistent handed local Z axis.
    for (const [side, sign] of [['left', 1], ['right', -1]]) {
      const elbow = bone(side + 'LowerArm'), wrist = bone(side + 'Hand');
      if (elbow) elbow.quaternion.multiply(q.setFromAxisAngle(AXIS_Z, sign * .12));
      if (wrist) wrist.quaternion.multiply(q.setFromAxisAngle(AXIS_Z, -sign * .06));
      for (const digit of ['Index', 'Middle', 'Ring', 'Little']) {
        for (const segment of ['Proximal', 'Intermediate', 'Distal']) {
          const joint = bone(side + digit + segment);
          if (joint) joint.quaternion.multiply(q.setFromAxisAngle(AXIS_Z, sign * .11));
        }
      }
    }
  }
  // Resolve normalized pose into the rendered skinned meshes before measuring.
  // Precise bounds include accessories above the head, not just bone positions
  // or stale per-mesh bounding boxes. This is done once at load, never per frame.
  vrm.update(0);
  model.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(model, true);
  const height = box.max.y - box.min.y;
  const center = box.getCenter(new THREE.Vector3());
  const framing = ['bust', 'waist', 'full', 'manual'].includes(config.avatarFraming) ? config.avatarFraming : 'bust';
  if (framing === 'manual') {
    model.position.fromArray(config.avatarPosition || [0, .1, 0]);
    model.scale.setScalar(config.avatarScale || config.characterScale || 1);
  } else {
    // Nominal starter camera: 32-degree fov at config.camera looking at [0, 1.65, 0].
    const cam = Array.isArray(config.camera) ? config.camera : [0, 2.2, 7.7];
    const bandTop = 1.65 + Math.max(2, cam[2] || 7.7) * Math.tan(THREE.MathUtils.degToRad(16));
    const fraction = framing === 'bust' ? .84 : framing === 'full' ? .40 : .62;
    const scale = clamp(bandTop * fraction / (height || 1.4), .1, 12);
    model.scale.multiplyScalar(scale);
    // Keep the standing performer clear of foreground furniture.
    model.position.set(-center.x * scale, -box.min.y * scale, .05);
  }
  // A soft contact pool grounds the avatar without a hard wall shadow.
  let pool = null;
  const opaque = new URLSearchParams(location.search).get('bg') !== 'transparent';
  if (opaque) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    const ctx = canvas.getContext('2d');
    const fade = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    fade.addColorStop(0, 'rgba(48,31,23,.42)');
    fade.addColorStop(.55, 'rgba(48,31,23,.22)');
    fade.addColorStop(1, 'rgba(48,31,23,0)');
    ctx.fillStyle = fade; ctx.fillRect(0, 0, 128, 128);
    const map = new THREE.CanvasTexture(canvas);
    pool = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({map, transparent: true, depthWrite: false}));
    pool.rotation.x = -Math.PI / 2;
    pool.scale.setScalar(Math.max(.9, (box.max.x - box.min.x) * model.scale.x * 1.45));
    pool.position.set(model.position.x, .012, model.position.z + .06);
    pool.renderOrder = -1;
    scene.add(pool);
  }
  model.traverse(part => {
    // MToon stays unlit by shadow maps; the contact pool provides soft grounding instead.
    if (part.isMesh) { part.castShadow = false; part.receiveShadow = false; }
  });
  scene.add(model);
  const gaze = new THREE.Object3D(); scene.add(gaze);
  if (vrm.lookAt) vrm.lookAt.target = gaze;
  const baseHead = head?.quaternion.clone(), baseSpine = spine?.quaternion.clone(), baseChest = chest?.quaternion.clone(), baseHips = hips?.quaternion.clone();
  const headPos = new THREE.Vector3();
  let speaking = false, voice = null, voiceStart = 0, voiceWindow = 0, voiceDuration = 0;
  let openness = 0, performanceLevel = 0, lastReport = 0, blinkStart = 0, nextBlink = 2.4, doubleBlink = false;
  let seed = 31, mood = 'neutral', moodUntil = 0, currentMood = 0, moodName = 'neutral';
  let glanceX = 0, glanceY = 0, glanceUntil = 0, glanceBlend = 0;
  let reaction = '', reactionUntil = 0, presence = 'live';
  const has = name => !!expressions?.getExpression(name);
  const boundNames = new Set(Object.keys(expressions?.expressionMap || {}).filter(name =>
    (expressions.getExpression(name)?.binds?.length || 0) > 0));
  const bound = name => boundNames.has(name);
  const primary = ['aa', 'Expr.mouth_open_smile', 'oh', 'ee', 'ih', 'ou', 'Expr.mouth_small_o'].find(bound);
  const rounded = ['oh', 'ou', 'Expr.mouth_small_o', 'ee', 'ih'].find(name => name !== primary && bound(name));
  const routes = Object.fromEntries(MOODS.map(name => [name, moodChannels(name, bound)]));
  const moodNames = [...new Set(MOODS.flatMap(name => routes[name]))];
  const moodMouths = moodNames.filter(name => name.startsWith('Expr.mouth_'));
  const allMouths = [...new Set([...MOUTHS, ...moodMouths, primary, rounded].filter(Boolean))];
  const set = (name, value) => { if (has(name)) expressions.setValue(name, value); };
  const clearMouth = () => { for (const name of allMouths) set(name, 0); };
  const random = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  function update(t, delta) {
    try {
      delta = clamp(Number.isFinite(delta) ? delta : 0, 0, .05);
      const now = performance.now();
      const elapsed = now - voiceStart;
      let input = 0;
      if (speaking && voice) {
        const index = Math.floor(elapsed / voiceWindow);
        input = elapsed >= 0 && elapsed < voiceDuration && index >= 0 && index < voice.length ? clamp(voice[index] || 0, 0, 1) : 0;
      } else if (speaking) {
        const syllable = .5 + .5 * Math.sin(t * 8.1 + .8 * Math.sin(t * 2.7));
        input = clamp(syllable * (.68 + .32 * Math.sin(t * 3.3 + 1.1)) * 1.45, .02, 1);
      }
      openness = input;
      // The decoded envelope is already smoothed; a second low-pass hides syllables.
      if (voice) performanceLevel = input;
      else performanceLevel += (input - performanceLevel) * (1 - Math.exp(-delta * 18));
      if (now >= nextBlink * 1000 && !blinkStart) {
        blinkStart = now; doubleBlink = random() < .17;
      }
      const bt = blinkStart ? (now - blinkStart) / 1000 : -1;
      let blink = bt >= 0 ? Math.max(0, 1 - Math.abs(bt - .105) / .105) : 0;
      if (doubleBlink && bt > .22) blink = Math.max(blink, Math.max(0, 1 - Math.abs(bt - .325) / .105));
      if (bt > (doubleBlink ? .45 : .23)) { blinkStart = 0; nextBlink = now / 1000 + 2.2 + random() * 3.6; }
      set('blink', blink);
      if (!has('blink')) { set('blinkLeft', blink); set('blinkRight', blink); }
      if (moodUntil && now > moodUntil) mood = 'neutral';
      const desiredMood = mood === 'thinking' ? 'relaxed' : mood;
      if (desiredMood !== moodName && currentMood < .01) moodName = desiredMood;
      const moodTarget = desiredMood === moodName && desiredMood !== 'neutral' ? .65 : 0;
      currentMood += (moodTarget - currentMood) * (1 - Math.exp(-delta * 5));
      const activeMood = routes[moodName] || [];
      for (const name of moodNames) {
        const amount = name.startsWith('Expr.mouth_') ? (speaking ? .3 * (1 - performanceLevel) * (voice ? performanceLevel : 1) : 0) : 1;
        set(name, activeMood.includes(name) ? currentMood * amount : 0);
      }
      const moodExpression = expressions?.getExpression(moodName);
      const mouthFactor = bound(moodName) && moodExpression?.overrideMouth === 'block' ? 1 - currentMood : 1;
      if (speaking) {
        // A bounded blend uses only shapes that actually deform the loaded model.
        const round = .5 + .5 * Math.sin(t * 3.2 + input * 1.1);
        const total = performanceLevel * .96 * mouthFactor;
        const mix = rounded ? .06 + .14 * round : 0;
        if (primary) set(primary, total * (1 - mix));
        if (rounded) set(rounded, total * mix);
      } else clearMouth();
      const sway = Math.sin(t * .63) * .018 + Math.sin(t * .19) * .012;
      const shift = Math.sin(t * .21) * .016 + .012;
      if (hips && baseHips) {
        hips.quaternion.copy(baseHips);
        hips.rotation.z += shift; hips.rotation.y += .05 + Math.sin(t * .13) * .02;
      }
      if (spine && baseSpine) { spine.quaternion.copy(baseSpine); spine.rotation.z += sway - shift * .6; spine.rotation.x += Math.sin(t * 1.5) * .009; spine.rotation.y -= .06; }
      if (chest && baseChest) { chest.quaternion.copy(baseChest); chest.rotation.x += Math.sin(t * 1.5 + .3) * .012; }
      if (head && baseHead) {
        head.quaternion.copy(baseHead);
        head.rotation.z += -sway * .55 + Math.sin(t * .8) * .013;
        head.rotation.x += Math.sin(t * .57) * .015 + Math.sin(t * 6) * performanceLevel * .018;
        head.rotation.y += glanceX * glanceBlend * .45;
        head.rotation.x -= glanceY * glanceBlend * .28;
        if (reactionUntil > now) {
          const remaining = (reactionUntil - now) / 1000;
          const curve = Math.sin(Math.min(1, remaining) * Math.PI);
          head.rotation.x += (reaction === 'nod' ? .16 : reaction === 'alert' ? -.20 : .04) * (reaction === 'nod' ? Math.sin(remaining * 9) : curve);
          if (reaction === 'greet') head.rotation.z += .22 * curve;
        }
      }
      glanceBlend += ((now < glanceUntil ? 1 : 0) - glanceBlend) * (1 - Math.exp(-delta * 5));
      if (head) head.getWorldPosition(headPos); else headPos.set(0, 2.15, 0);
      gaze.position.set(headPos.x + Math.sin(t * .73) * .05 + glanceX * glanceBlend * .65,
        headPos.y + Math.sin(t * .51) * .035 + glanceY * glanceBlend * .45, headPos.z + 5);
      vrm.update(delta);
      if (now - lastReport >= 100) {
        document.body.dataset.mouth = labels[Math.round(openness * 100)];
        document.body.dataset.avatarBlink = labels[Math.round(clamp(blink, 0, 1) * 100)];
        document.body.dataset.avatarAa = labels[Math.round(clamp(expressions?.getValue('aa') || 0, 0, 1) * 100)];
        document.body.dataset.avatarMouths = allMouths.map(name => (expressions?.getValue(name) || 0).toFixed(3)).join(',');
        document.body.dataset.avatarMood = moodName + ':' + currentMood.toFixed(2);
        document.body.dataset.avatarMoodChannels = activeMood.join(',');
        document.body.dataset.avatarGlance = glanceX + ':' + glanceY + ':' + glanceBlend.toFixed(2);
        if (head) {
          head.getWorldQuaternion(q);
          document.body.dataset.avatarHeadYaw = Math.round(THREE.MathUtils.radToDeg(2 * Math.asin(Math.max(-1, Math.min(1, q.y)))) * 10) / 10;
        }
        lastReport = now;
      }
    } catch { /* A malformed optional rig must not break the scene's frame loop. */ }
  }
  model.updateMatrixWorld(true);
  function armY(arm, elbow) {
    if (!arm || !elbow) return 'missing';
    arm.getWorldPosition(v); elbow.getWorldPosition(w); return w.sub(v).normalize().y.toFixed(3);
  }
  document.body.dataset.avatarPose = pose === 'tpose' ? 'tpose' : posed ? 'relaxed' : 'unavailable';
  document.body.dataset.avatarLeftArmY = armY(leftArm, leftElbow);
  document.body.dataset.avatarRightArmY = armY(rightArm, rightElbow);
  document.body.dataset.avatarVersion = String(vrm.meta?.metaVersion || 'unknown');
  document.body.dataset.avatarHeight = height.toFixed(3);
  document.body.dataset.avatarExprs = Object.keys(expressions?.expressionMap || {}).join(',');
  return {
    frameCamera(camera, shot) {
      if (framing === 'manual' || !(height > .01)) return;
      const scaledHeight = height * model.scale.y;
      const visible = scaledHeight * (framing === 'full' ? 1.16 : framing === 'waist' ? .78 : .64)
        * (shot === 'close' ? .82 : shot === 'medium' ? .91 : 1);
      const top = model.position.y + box.max.y * model.scale.y;
      const centerX = model.position.x + center.x * model.scale.x;
      const targetY = top - visible * .37;
      const distance = visible / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
      camera.position.set(centerX, targetY + .12, model.position.z + distance);
      camera.lookAt(centerX, targetY, model.position.z);
    },
    status() {
      return {mouthScale: speaking ? .14 + openness * 1.3 : .14,
        aa: expressions?.getValue('aa') ?? null,
        mouthValues: Object.fromEntries(allMouths.map(name => [name, expressions?.getValue(name) || 0]))};
    },
    setVoice(levels, start, windowMs, durationMs) { voice = levels; voiceStart = start; voiceWindow = windowMs; voiceDuration = durationMs; },
    setSpeaking(value) {
      speaking = !!value;
      if (!speaking) { voice = null; openness = performanceLevel = 0; clearMouth(); document.body.dataset.mouth = '0'; document.body.dataset.avatarAa = '0'; document.body.dataset.avatarMouths = allMouths.map(() => '0').join(','); }
    },
    setMood(name) { mood = ['neutral', 'happy', 'surprised', 'sad', 'relaxed', 'thinking'].includes(name) ? name : 'neutral'; moodUntil = mood === 'neutral' ? 0 : performance.now() + 6000; },
    glance(xNorm, yNorm, ms) { glanceX = clamp(Number(xNorm) || 0, -1, 1); glanceY = clamp(Number(yNorm) || 0, -1, 1); glanceUntil = performance.now() + clamp(Number(ms) || 0, 0, 5000); },
    react(kind) { reaction = ['alert', 'greet', 'nod'].includes(kind) ? kind : ''; reactionUntil = performance.now() + (reaction ? 1000 : 0); },
    setPresence(state) { presence = ['live', 'offair', 'privacy'].includes(state) ? state : 'offair'; if (presence !== 'live') this.setSpeaking(false); },
    update
  };
}
