"""Exercise the real scene loading path before the character finishes loading."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("transparent", [False, True])
def test_loading_canvas_is_ready_before_append(tmp_path, transparent):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable")
    source = (Path(__file__).parents[2] / "onair/scene_starter/scene.js").read_text()
    source = source.replace("'./vendor/three/build/three.module.js'", "'./three.mjs'")
    for name in ("room", "lighting", "character", "events", "quality"):
        source = source.replace(f"'./{name}.js'", f"'./{name}.mjs'")
    (tmp_path / "scene.mjs").write_text(source)
    (tmp_path / "three.mjs").write_text('''
export class Color { constructor(value) { this.value = value; } }
export class Scene { }
export class PerspectiveCamera { constructor() { this.position = {fromArray() {}}; } lookAt() {} }
export class WebGLRenderer {
  constructor() { this.domElement = {style: {}}; this.shadowMap = {}; globalThis.renderer = this; }
  setPixelRatio(v) { this.ratio = v; }
  setSize(w,h) { this.width = w; this.height = h; }
  setClearColor(color, alpha) { this.color = color; this.alpha = alpha; }
  clear() { this.cleared = true; }
}
export const PCFShadowMap = 1, SRGBColorSpace = 2, ACESFilmicToneMapping = 3;
''')
    (tmp_path / "room.mjs").write_text("export const themes = {test: {palette: {wall: '#abcdef'}}};\n"
                                        "export const resolveTheme = () => 'test';\n"
                                        "export const buildRoom = () => ({});\n")
    (tmp_path / "lighting.mjs").write_text("export const buildLighting = () => ({});\n")
    (tmp_path / "character.mjs").write_text("export const createCharacter = () => new Promise(() => {});\n")
    (tmp_path / "events.mjs").write_text("export const connectEvents = () => {};\n")
    (tmp_path / "quality.mjs").write_text("export const createQualityController = () => ({});\n")
    (tmp_path / "probe.mjs").write_text('''
import assert from 'node:assert/strict';
const transparent = process.argv[2] === 'true';
globalThis.location = {search: transparent ? '?bg=transparent' : ''};
globalThis.innerWidth = 800; globalThis.innerHeight = 450; globalThis.devicePixelRatio = 1;
globalThis.fetch = async () => ({json: async () => ({theme: 'test', palette: {wall: '#123456'}})});
globalThis.document = {body: {dataset: {}, appendChild(canvas) {
  const r = globalThis.renderer;
  assert.equal(r.width, 800); assert.equal(r.height, 450);
  assert.equal(r.cleared, true);
  assert.equal(r.alpha, transparent ? 0 : 1);
  assert.equal(r.color, '#123456');
  assert.equal(canvas.style.backgroundColor, transparent ? 'transparent' : '#123456');
  globalThis.appended = true;
}}};
import('./scene.mjs');
await new Promise(resolve => setTimeout(resolve, 50));
assert.equal(globalThis.appended, true);
''')
    result = subprocess.run([node, str(tmp_path / "probe.mjs"), str(transparent).lower()],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
