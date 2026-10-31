/* Dependency-free lifecycle/propagation tests. Run: node scripts/test_login_scene.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { test } = require('node:test');

function fixture({ reduced = false, width = 1672, gpu = false, shaderFailure = false } = {}) {
  class Element extends EventTarget {
    constructor() { super(); this.style = {}; this.dataset = {}; this.classList = { toggle() {} }; }
    setAttribute(name, value) { this[name] = value; }
    getBoundingClientRect() { return { left: 1020, top: 180, width: 850, height: 684, right: 1870 }; }
  }
  const operations = {};
  const context = new Proxy({}, { get: (_, key) => {
    operations[key] = (operations[key] || 0) + 1;
    if (key === 'createRadialGradient') return () => ({ addColorStop() {} });
    if (key === 'getImageData') return () => ({ data: new Uint8ClampedArray(180 * 145 * 4).fill(255) });
    return () => {};
  }, set: () => true });
  const gpuOperations = {};
  const gpuContext = new Proxy({}, { get: (_, key) => {
    if (key === key.toUpperCase()) return 1;
    return (...args) => {
      gpuOperations[key] = (gpuOperations[key] || 0) + 1;
      if (key === 'getShaderParameter') return !shaderFailure;
      if (key === 'getProgramParameter') return true;
      if (key.startsWith('create') || key === 'getUniformLocation') return {};
      if (key === 'getAttribLocation') return args[1] === 'position' ? 0 : 1;
    };
  } });
  const createCanvas = (force2d = false) => {
    const result = new Element();
    result.getContext = type => type === 'webgl' ? (gpu && !force2d ? gpuContext : null) : context;
    result.cloneNode = () => createCanvas(true);
    result.replaceWith = replacement => { result.replacement = replacement; };
    return result;
  };
  const canvas = createCanvas(), staticCanvas = createCanvas(), waveCanvas = createCanvas();
  const image = new Element(); image.complete = true; image.naturalWidth = 1392;
  const brain = new Element();
  const modules = Array.from({ length: 4 }, () => new Element());
  const document = new EventTarget(); document.hidden = false; document.documentElement = new Element();
  document.body = new Element(); document.body.classList = { contains: () => false, toggle() {} };
  document.getElementById = id => ({ 'neural-canvas': canvas, 'neural-static': staticCanvas, 'neural-wave': waveCanvas })[id];
  document.querySelector = selector => ({ '.brain-structure': image, '.brain-region': brain })[selector];
  document.querySelectorAll = () => modules;
  document.createElement = () => createCanvas();
  const motion = new EventTarget(); motion.matches = reduced;
  const window = new EventTarget(), frames = new Map(); let id = 0;
  window.requestIdleCallback = cb => { cb(); return 1; };
  const sandbox = { document, window, innerWidth: width, innerHeight: 941, devicePixelRatio: 2,
    matchMedia: () => motion, AbortController, console, performance: { now: () => 0 },
    setTimeout, clearTimeout,
    requestAnimationFrame: callback => { frames.set(++id, callback); return id; },
    cancelAnimationFrame: frame => frames.delete(frame),
    ResizeObserver: class { observe() {} disconnect() { this.disconnected = true; } },
  };
  const source = fs.readFileSync(path.join(__dirname, 'static/login/neural-scene.js'), 'utf8')
    .replace('mount();', 'mount(); globalThis.testScene = scene;');
  vm.runInNewContext(source, sandbox);
  if (!sandbox.testScene && frames.size === 1) {
    const [firstId, firstCallback] = frames.entries().next().value;
    frames.delete(firstId);
    firstCallback(0);
  }
  return { scene: sandbox.testScene, canvas, document, window, motion, frames, operations, gpuOperations };
}

test('a single frame loop pauses when hidden, resumes, and disposes on navigation', () => {
  const f = fixture();
  assert.equal(f.frames.size, 1);
  f.document.hidden = true; f.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(f.frames.size, 0); assert.equal(f.canvas.dataset.sceneState, 'hidden');
  f.document.hidden = false; f.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(f.frames.size, 1);
  f.scene.sync(); f.scene.sync(); assert.equal(f.frames.size, 1);
  f.window.dispatchEvent(new Event('pagehide'));
  assert.equal(f.frames.size, 0); assert.equal(f.canvas.dataset.sceneState, 'disposed');
  assert.ok(f.scene.resizeObserver.disconnected);
  assert.ok(f.scene.abort.signal.aborted);
});

test('system reduced motion stops recurring frames without requiring a visible pause control', () => {
  const f = fixture({ reduced: true });
  assert.equal(f.frames.size, 0); assert.equal(f.canvas.dataset.sceneState, 'paused');
  f.motion.matches = false; f.motion.dispatchEvent(new Event('change'));
  assert.equal(f.frames.size, 1);
  f.motion.matches = true; f.motion.dispatchEvent(new Event('change')); assert.equal(f.frames.size, 0);
  f.motion.matches = false; f.motion.dispatchEvent(new Event('change')); assert.equal(f.frames.size, 1);
});

test('signals travel over graph edges, arrive at neighbors, and remain bounded', () => {
  const { scene } = fixture();
  assert.equal(scene.nodes.length, 185);
  scene.activate(0);
  assert.ok(scene.pulses.length > 0);
  const pulse = scene.pulses[0];
  assert.ok(scene.nodes[0].neighbors.includes(pulse.b));
  assert.equal(scene.nodes[pulse.b].energy, 0);
  scene.time = 1; scene.update(.01);
  assert.equal(scene.nodes[pulse.b].energy, 1);
  for (let i = 0; i < scene.nodes.length; i++) scene.activate(i);
  assert.ok(scene.pulses.length <= 36);
  scene.signals = []; scene.sendModule(0, true);
  scene.time = 5; scene.update(.01);
  assert.equal(scene.nodes[scene.anchors[0]].energy, 1);
});

test('mobile reduces graph density and omits module transmissions', () => {
  const { scene, canvas } = fixture({ width: 390 });
  assert.equal(scene.nodes.length, 55);
  assert.equal(scene.lowQuality, true);
  assert.equal(canvas.width, 487);
  scene.sendModule(0, true); assert.equal(scene.signals.length, 0);
});

test('high-refresh callbacks render every frame instead of alternating skipped frames', () => {
  for (const hz of [60, 75, 90, 120, 144]) {
    const f = fixture(); let renders = 0;
    f.scene.draw = () => { renders++; };
    for (let i = 1; i <= hz; i++) {
      const [id, callback] = f.frames.entries().next().value;
      f.frames.delete(id); callback(i * 1000 / hz);
    }
    assert.equal(renders, hz, `${hz} Hz must not skip frames`);
    assert.equal(f.frames.size, 1);
    assert.ok(Math.abs(f.scene.time - 1) < .012);
  }
});

test('glow and static network are cached; repeated resize observations do not rebuild', () => {
  const f = fixture();
  const gradientCount = f.operations.createRadialGradient;
  const graph = f.scene.nodes, wave = f.scene.waveRows;
  let staticDraws = 0; f.scene.drawStatic = () => staticDraws++;
  for (let i = 1; i <= 20; i++) { f.scene.time = i / 60; f.scene.update(1 / 60); f.scene.draw(i / 60); f.scene.resize(); }
  assert.equal(f.operations.createRadialGradient, gradientCount);
  assert.equal(f.scene.nodes, graph); assert.equal(f.scene.waveRows, wave);
  assert.equal(staticDraws, 0);
});

test('optimized wave preserves the original sinusoidal surface', () => {
  const { scene } = fixture();
  for (const time of [0, 1.3, 9.2]) for (const [index, row] of scene.waveRows.entries()) {
    const z = index / (scene.waveRows.length - 1), phase = time * .48;
    for (const point of row.points) {
      const u = (point.x - scene.width / 2) / (scene.width * (1.05 + z * .65)) + .5;
      const expected = (Math.sin(u * 13 + z * 5 - phase) * 22 + Math.sin(u * 22 - z * 6 + phase * .7) * 22 * .4) * (.25 + z * .75);
      const actual = point.a * Math.cos(phase) - point.b * Math.sin(phase) + point.c * Math.cos(phase * .7) + point.d * Math.sin(phase * .7);
      assert.ok(Math.abs(actual - expected) < 1e-9);
    }
  }
});

test('GPU wave uses one draw call per frame and uploads geometry only on resize/quality changes', () => {
  const f = fixture({ gpu: true });
  const uploaded = f.gpuOperations.bufferData, draws = f.gpuOperations.drawArrays;
  for (let i = 0; i < 60; i++) f.scene.drawWave(i / 60);
  assert.equal(f.gpuOperations.drawArrays - draws, 60);
  assert.equal(f.gpuOperations.bufferData, uploaded);
  assert.equal(f.canvas.dataset.waveRenderer, 'webgl');
  f.scene.destroy();
  assert.equal(f.gpuOperations.deleteBuffer, 1); assert.equal(f.gpuOperations.deleteProgram, 1);
});

test('GPU context loss can recover without breaking the graph or form', () => {
  const f = fixture({ gpu: true });
  const canvas = f.scene.waveRenderer.canvas, draws = f.gpuOperations.drawArrays;
  canvas.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
  f.scene.drawWave(2); assert.equal(f.gpuOperations.drawArrays, draws);
  canvas.dispatchEvent(new Event('webglcontextrestored'));
  f.scene.drawWave(3); assert.ok(f.gpuOperations.drawArrays > draws);
});

test('a driver shader failure falls back to Canvas 2D instead of disabling animations', () => {
  const f = fixture({ gpu: true, shaderFailure: true });
  assert.equal(f.canvas.dataset.waveRenderer, 'canvas2d');
  assert.equal(f.canvas.dataset.sceneState, 'running');
  assert.equal(f.frames.size, 1);
  f.scene.draw(1);
});
