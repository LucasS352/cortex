/* Login-only graphics. One clock owns the graph, connections, modules and wave. */
(() => {
  'use strict';
  const CONFIG = Object.freeze({
    maxDpr: 1.25, maxCanvasPixels: 1800000, desktopNodes: 185, mobileNodes: 55, maxPulses: 36,
    interactionRadius: 125, impulseSeconds: .65, decay: 1.15,
    spontaneousInterval: 1.25, waveAmplitude: 22, waveSpeed: .48,
    floatAmplitude: 4, glow: .8, waveHeight: 190,
  });
  class DigitalWave {
    constructor(canvas, signal) {
      this.canvas = canvas;
      this.gl = canvas.getContext('webgl', {
        alpha: true, antialias: false, depth: false, stencil: false,
        premultipliedAlpha: true, preserveDrawingBuffer: false,
      });
      if (this.gl) {
        try { this.initializeGL(); }
        catch {
          // A driver can expose WebGL but reject shader compilation. Recover
          // with a fresh 2D canvas; contexts cannot change type on the same node.
          if (this.buffer) this.gl.deleteBuffer(this.buffer);
          if (this.program) this.gl.deleteProgram(this.program);
          this.gl = null;
          const replacement = canvas.cloneNode(false); canvas.replaceWith(replacement);
          this.canvas = replacement; this.ctx = replacement.getContext('2d');
          return;
        }
        canvas.addEventListener('webglcontextlost', event => {
          event.preventDefault(); this.lost = true;
        }, { signal });
        canvas.addEventListener('webglcontextrestored', () => {
          this.lost = false; this.initializeGL(); this.setRows(this.rows); this.draw(this.time || 0);
        }, { signal });
      } else this.ctx = canvas.getContext('2d');
    }
    initializeGL() {
      const gl = this.gl;
      const shader = (type, source) => {
        const result = gl.createShader(type); gl.shaderSource(result, source); gl.compileShader(result);
        if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(result));
        return result;
      };
      const vertex = shader(gl.VERTEX_SHADER, `
        attribute vec4 position; attribute vec4 wave;
        uniform vec2 viewport; uniform vec4 phase; uniform float dpr;
        varying float radius; varying float diameter; varying float opacity;
        void main() {
          float y = position.y + dot(wave, phase);
          gl_Position = vec4(position.x / viewport.x * 2.0 - 1.0, 1.0 - y / viewport.y * 2.0, 0.0, 1.0);
          radius = position.z * dpr; diameter = (position.z * 2.0 + 2.0) * dpr;
          opacity = position.w; gl_PointSize = diameter;
        }`);
      const fragment = shader(gl.FRAGMENT_SHADER, `
        precision mediump float;
        varying float radius; varying float diameter; varying float opacity;
        void main() {
          float distance = length((gl_PointCoord - vec2(0.5)) * diameter);
          float alpha = clamp(radius + 0.5 - distance, 0.0, 1.0) * opacity;
          gl_FragColor = vec4(vec3(21.0,157.0,244.0) / 255.0 * alpha, alpha);
        }`);
      this.program = gl.createProgram();
      gl.attachShader(this.program, vertex); gl.attachShader(this.program, fragment); gl.linkProgram(this.program);
      gl.deleteShader(vertex); gl.deleteShader(fragment);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(this.program));
      gl.useProgram(this.program);
      this.buffer = gl.createBuffer();
      this.position = gl.getAttribLocation(this.program, 'position');
      this.wave = gl.getAttribLocation(this.program, 'wave');
      this.viewport = gl.getUniformLocation(this.program, 'viewport');
      this.phase = gl.getUniformLocation(this.program, 'phase');
      this.dprUniform = gl.getUniformLocation(this.program, 'dpr');
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      gl.clearColor(0, 0, 0, 0);
    }
    resize(width, dpr) {
      this.width = width; this.dpr = dpr;
      this.canvas.width = Math.round(width * dpr);
      this.canvas.height = Math.round(CONFIG.waveHeight * dpr);
      if (this.ctx) this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    setRows(rows) {
      this.rows = rows;
      if (!this.gl || this.lost) return;
      const vertices = [];
      for (const row of rows) for (const point of row.points) {
        vertices.push(point.x, row.y, row.radius, row.alpha, point.a, -point.b, point.c, point.d);
      }
      this.count = vertices.length / 8;
      this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffer);
      this.gl.bufferData(this.gl.ARRAY_BUFFER, new Float32Array(vertices), this.gl.STATIC_DRAW);
    }
    draw(time) {
      this.time = time;
      const phase = time * CONFIG.waveSpeed;
      const sin = Math.sin(phase), cos = Math.cos(phase), sin2 = Math.sin(phase * .7), cos2 = Math.cos(phase * .7);
      if (this.gl) {
        if (this.lost) return;
        const gl = this.gl;
        gl.viewport(0, 0, this.canvas.width, this.canvas.height); gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(this.program); gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
        gl.enableVertexAttribArray(this.position); gl.vertexAttribPointer(this.position, 4, gl.FLOAT, false, 32, 0);
        gl.enableVertexAttribArray(this.wave); gl.vertexAttribPointer(this.wave, 4, gl.FLOAT, false, 32, 16);
        gl.uniform2f(this.viewport, this.width, CONFIG.waveHeight); gl.uniform1f(this.dprUniform, this.dpr);
        gl.uniform4f(this.phase, cos, sin, cos2, sin2);
        gl.drawArrays(gl.POINTS, 0, this.count);
        return;
      }
      // Canvas fallback for browsers without WebGL; same surface and waveform.
      const ctx = this.ctx;
      if (!ctx) return;
      ctx.clearRect(0, 0, this.width, CONFIG.waveHeight);
      for (const row of this.rows) {
        ctx.fillStyle = row.color; ctx.beginPath();
        for (const point of row.points) {
          const y = row.y + point.a * cos - point.b * sin + point.c * cos2 + point.d * sin2;
          ctx.moveTo(point.x + row.radius, y); ctx.arc(point.x, y, row.radius, 0, Math.PI * 2);
        }
        ctx.fill();
      }
    }
    destroy() {
      if (this.gl && !this.lost) { this.gl.deleteBuffer(this.buffer); this.gl.deleteProgram(this.program); }
      this.rows = [];
    }
  }
  class NeuralScene {
    init() {
      this.canvas = document.getElementById('neural-canvas');
      this.ctx = this.canvas.getContext('2d', { alpha: true });
      if (!this.ctx) return;
      this.staticCanvas = document.getElementById('neural-static');
      this.staticCtx = this.staticCanvas.getContext('2d');
      this.waveCanvas = document.getElementById('neural-wave');
      if (!this.staticCtx) return;
      this.abort = new AbortController();
      this.waveRenderer = new DigitalWave(this.waveCanvas, this.abort.signal);
      this.canvas.dataset.waveRenderer = this.waveRenderer.gl ? 'webgl' : 'canvas2d';
      this.image = document.querySelector('.brain-structure');
      this.brain = document.querySelector('.brain-region');
      this.modules = [...document.querySelectorAll('.neural-module')];
      this.motion = matchMedia('(prefers-reduced-motion: reduce)');
      this.frame = 0; this.time = 0; this.last = 0;
      this.nextActivity = .5; this.nextModule = 1.8; this.lastPointer = -1;
      this.pointer = { x: -1000, y: -1000 }; this.pulses = []; this.signals = [];
      this.nodes = []; this.edges = []; this.lowQuality = innerWidth < 700;
      this.intervals = []; this.costs = []; this.seed = 71;
      this.createGlowSprite();
      const options = { signal: this.abort.signal };
      window.addEventListener('pointermove', e => {
        this.pointer.x = e.clientX; this.pointer.y = e.clientY;
      }, { ...options, passive: true });
      document.addEventListener('pointerleave', () => { this.pointer.x = -1000; }, options);
      document.addEventListener('visibilitychange', () => this.sync(), options);
      this.motion.addEventListener('change', () => this.sync(), options);
      this.modules.forEach((module, index) => {
        module.addEventListener('pointerenter', () => {
          if (!this.still()) this.sendModule(index, true);
        }, options);
      });
      this.image.addEventListener('load', () => this.resize(true), options);
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(document.documentElement);
      window.addEventListener('resize', () => this.resize(), options);
      this.resize(); this.sync();
    }
    random() { this.seed = (this.seed * 1664525 + 1013904223) >>> 0; return this.seed / 4294967296; }
    still() { return this.motion.matches; }
    resize(force = false) {
      // ResizeObserver and window.resize may report the same layout repeatedly.
      if (!force && this.width === innerWidth && this.height === innerHeight && this.deviceDpr === devicePixelRatio) return;
      this.width = innerWidth; this.height = innerHeight;
      this.mobile = this.width < 981;
      this.deviceDpr = devicePixelRatio;
      const dpr = Math.min(devicePixelRatio || 1, CONFIG.maxDpr,
        Math.sqrt(CONFIG.maxCanvasPixels / (this.width * this.height)));
      for (const [canvas, ctx] of [[this.canvas, this.ctx], [this.staticCanvas, this.staticCtx]]) {
        canvas.width = Math.floor(this.width * dpr);
        canvas.height = Math.floor(this.height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      const waveDpr = Math.min(devicePixelRatio || 1, CONFIG.maxDpr);
      this.waveRenderer.resize(this.width, waveDpr);
      this.rect = this.brain.getBoundingClientRect();
      this.moduleLayout = this.modules.map((el, i) => {
        el.style.transform = '';
        const r = el.getBoundingClientRect();
        return { x: r.right, y: r.top + r.height / 2, angle: [5, 3, 0, -2][i], dy: 0, until: 0, active: false, transform: '' };
      });
      this.buildGraph(); this.pulses = []; this.signals = [];
      this.prepareWave();
      this.drawStatic();
      this.draw(0); this.sync();
    }
    buildGraph() {
      // Sample the actual structural image: nodes sit on folds, never on a sphere.
      this.nodes = []; this.edges = []; this.seed = 71;
      if (!this.image.complete || !this.image.naturalWidth) return;
      const sample = document.createElement('canvas'); sample.width = 180; sample.height = 145;
      const ctx = sample.getContext('2d', { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(this.image, 0, 0, 180, 145);
      let pixels;
      try { pixels = ctx.getImageData(0, 0, 180, 145).data; } catch { return; }
      const count = this.mobile ? CONFIG.mobileNodes : CONFIG.desktopNodes;
      for (let attempt = 0; attempt < 9000 && this.nodes.length < count; attempt++) {
        const u = this.random(), v = this.random();
        const p = (Math.floor(v * 145) * 180 + Math.floor(u * 180)) * 4;
        if (pixels[p + 3] < 160 || pixels[p + 1] < 78 || pixels[p + 2] < 130) continue;
        if (this.nodes.some(n => Math.hypot(n.u - u, n.v - v) < .034)) continue;
        this.nodes.push({ u, v, x: this.rect.left + u * this.rect.width,
          y: this.rect.top + v * this.rect.height, energy: 0, phase: this.random() * 6.28, neighbors: [] });
      }
      const seen = new Set();
      this.nodes.forEach((node, a) => {
        const nearest = this.nodes.map((other, b) => ({ b, d: Math.hypot(node.u - other.u, node.v - other.v) }))
          .filter(item => item.d > 0 && item.d < .17).sort((x, y) => x.d - y.d).slice(0, 3);
        nearest.forEach(({ b }) => {
          const key = [Math.min(a, b), Math.max(a, b)].join('-');
          if (seen.has(key)) return;
          seen.add(key); this.edges.push([a, b]);
          node.neighbors.push(b); this.nodes[b].neighbors.push(a);
        });
      });
      this.anchors = [0.23, .39, .58, .76].map(v => {
        let best = 0, distance = Infinity;
        this.nodes.forEach((n, i) => { const d = Math.hypot(n.u - .25, n.v - v); if (d < distance) { best = i; distance = d; } });
        return best;
      });
    }
    activate(index, depth = 0, visited = []) {
      const node = this.nodes[index];
      if (!node) return;
      node.energy = 1;
      if (depth >= 3 || this.pulses.length >= CONFIG.maxPulses) return;
      const trail = [...visited, index];
      node.neighbors.filter(n => !trail.includes(n)).slice(0, depth ? 1 : 2).forEach(target => {
        if (this.pulses.length < CONFIG.maxPulses && !this.pulses.some(p => p.a === index && p.b === target)) {
          this.pulses.push({ a: index, b: target, start: this.time, duration: CONFIG.impulseSeconds * (.8 + this.random() * .5), depth: depth + 1, visited: trail });
        }
      });
    }
    sendModule(index, reverse) {
      if (this.mobile || !this.nodes.length || this.signals.some(s => s.index === index)) return;
      this.signals.push({ index, reverse, start: this.time, duration: 2 + this.random() * .7 });
      if (reverse) this.moduleLayout[index].until = this.time + .7;
      else this.activate(this.anchors[index]);
    }
    sync() {
      cancelAnimationFrame(this.frame); this.frame = 0; this.last = 0;
      const stopped = this.still();
      this.intervals = []; this.costs = [];
      this.canvas.dataset.sceneState = document.hidden ? 'hidden' : stopped ? 'paused' : 'running';
      if (stopped) { this.pulses = []; this.signals = []; this.nodes.forEach(n => n.energy = 0); this.draw(0); }
      else if (!document.hidden) this.frame = requestAnimationFrame(stamp => this.tick(stamp));
    }
    tick(stamp) {
      const elapsed = this.last ? stamp - this.last : 16.67;
      // Render on every display callback. A fixed 16 ms gate skips frames on
      // 75/90/144 Hz monitors and creates uneven motion even with a fast renderer.
      const start = performance.now();
      const dt = Math.min(elapsed / 1000, .1); this.last = stamp; this.time += dt;
      this.update(dt); this.draw(this.time);
      this.costs.push(performance.now() - start);
      this.intervals.push(elapsed);
      if (this.costs.length === 180) {
        const average = this.costs.reduce((a, b) => a + b, 0) / this.costs.length;
        const cadence = this.intervals.reduce((a, b) => a + b, 0) / this.intervals.length;
        const sorted = [...this.intervals].sort((a, b) => a - b);
        this.canvas.dataset.frameMs = average.toFixed(2);
        this.canvas.dataset.fps = (1000 / cadence).toFixed(1);
        this.canvas.dataset.frameP95 = sorted[Math.floor(sorted.length * .95)].toFixed(2);
        if (!this.lowQuality && (average > 6 || sorted[171] > Math.max(25, sorted[18] * 1.8))) {
          this.lowQuality = true; this.prepareWave();
        }
        this.canvas.dataset.quality = this.lowQuality ? 'economy' : 'full';
        this.costs = []; this.intervals = [];
      }
      this.frame = requestAnimationFrame(s => this.tick(s));
    }
    update(dt) {
      this.nodes.forEach(node => { node.energy = Math.max(0, node.energy - dt * CONFIG.decay); });
      const arrived = this.pulses.filter(p => this.time - p.start >= p.duration);
      this.pulses = this.pulses.filter(p => this.time - p.start < p.duration);
      arrived.forEach(p => this.activate(p.b, p.depth, p.visited));
      this.signals = this.signals.filter(signal => {
        if (this.time - signal.start < signal.duration) return true;
        if (signal.reverse) this.activate(this.anchors[signal.index]);
        else this.moduleLayout[signal.index].until = this.time + .8;
        return false;
      });
      if (this.time > this.nextActivity && this.nodes.length) {
        this.activate(Math.floor(this.random() * this.nodes.length));
        this.nextActivity = this.time + CONFIG.spontaneousInterval * (.7 + this.random());
      }
      if (this.time > this.nextModule) {
        this.sendModule(Math.floor(this.random() * 4), this.random() > .5);
        this.nextModule = this.time + 2.6 + this.random() * 2.2;
      }
      if (!this.mobile && this.time - this.lastPointer > .38) {
        let nearest = -1, distance = CONFIG.interactionRadius;
        this.nodes.forEach((node, i) => {
          const d = Math.hypot(node.x - this.pointer.x, node.y - this.pointer.y);
          if (d < distance) { distance = d; nearest = i; }
        });
        if (nearest >= 0) { this.activate(nearest); this.lastPointer = this.time; }
      }
    }
    createGlowSprite() {
      // Rasterize the expensive radial gradient once, then reuse a tiny texture.
      this.glowSprite = document.createElement('canvas');
      this.glowSprite.width = this.glowSprite.height = 64;
      const ctx = this.glowSprite.getContext('2d');
      const gradient = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
      gradient.addColorStop(0, 'rgba(53,201,255,.6)');
      gradient.addColorStop(1, 'rgba(22,139,255,0)');
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, 64, 64);
    }
    dot(x, y, radius, alpha, halo = false, ctx = this.ctx) {
      if (x < -30 || x > this.width + 30 || y < -30 || y > this.height + 30) return;
      if (halo) {
        const size = radius * 14;
        ctx.globalAlpha = alpha;
        ctx.drawImage(this.glowSprite, x - size / 2, y - size / 2, size, size);
        ctx.globalAlpha = 1;
      }
      ctx.fillStyle = `rgba(108,216,255,${alpha})`; ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();
    }
    draw(time) {
      const ctx = this.ctx; ctx.clearRect(0, 0, this.width, this.height);
      this.drawAmbient(time); this.drawWave(time);
      if (!this.mobile) this.drawConnections(time);
      ctx.lineWidth = .6;
      this.edges.forEach(([a, b]) => {
        const n = this.nodes[a], m = this.nodes[b];
        const energy = Math.max(n.energy, m.energy);
        if (energy <= .01) return; // Inactive connections live in the static layer.
        ctx.strokeStyle = `rgba(45,155,255,${energy * .3})`;
        ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.lineTo(m.x, m.y); ctx.stroke();
      });
      this.nodes.forEach(node => {
        const base = .2 + .08 * Math.sin(time * .65 + node.phase);
        this.dot(node.x, node.y, 1 + node.energy * 1.5, (base + node.energy * .7) * CONFIG.glow, node.energy > .35);
      });
      this.pulses.forEach(pulse => {
        const a = this.nodes[pulse.a], b = this.nodes[pulse.b];
        const t = Math.min(1, (this.time - pulse.start) / pulse.duration);
        this.dot(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 1.7, .85, true);
      });
    }
    drawAmbient(time) {
      for (let i = 0; i < (this.mobile ? 12 : 36); i++) {
        const x = ((i * 137.508) % 1000) / 1000 * this.width + Math.sin(time * .08 + i) * 5;
        const y = ((i * 231.37) % 1000) / 1000 * this.height + Math.cos(time * .06 + i) * 5;
        this.dot(x, y, i % 5 === 0 ? 2.4 : .8, .08 + (i % 4) * .03, i % 5 === 0);
      }
    }
    prepareWave() {
      const columns = this.lowQuality ? 85 : 115, rows = this.lowQuality ? 22 : 28;
      const amplitude = CONFIG.waveAmplitude * Math.min(1, this.width / 1200);
      this.waveRows = [];
      for (let row = 0; row < rows; row++) {
        const z = row / (rows - 1), perspective = .25 + z * .75;
        const points = [];
        for (let col = 0; col < columns; col++) {
          const u = col / (columns - 1);
          const x = (u - .5) * this.width * (1.05 + z * .65) + this.width / 2;
          if (x < -2 || x > this.width + 2) continue;
          // sin(a ± b) lets each frame use four trig calls, instead of thousands.
          points.push({ x, a: Math.sin(u * 13 + z * 5) * amplitude * perspective,
            b: Math.cos(u * 13 + z * 5) * amplitude * perspective,
            c: Math.sin(u * 22 - z * 6) * amplitude * .4 * perspective,
            d: Math.cos(u * 22 - z * 6) * amplitude * .4 * perspective });
        }
        this.waveRows.push({ y: CONFIG.waveHeight - 145 + z * 180, radius: .55 + z * .75,
          alpha: .12 + z * .48, color: `rgba(21,157,244,${.12 + z * .48})`, points });
      }
      this.waveRenderer.setRows(this.waveRows);
    }
    drawWave(time) {
      this.waveRenderer.draw(time);
    }
    drawStatic() {
      const ctx = this.staticCtx;
      ctx.clearRect(0, 0, this.width, this.height);
      // New blurred background network: baked once at resize, never animated.
      let seed = 1029;
      const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
      const points = Array.from({ length: this.mobile ? 28 : 66 }, () => ({ x: random() * this.width, y: random() * this.height }));
      ctx.save(); ctx.filter = 'blur(2px)'; ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(37,137,220,.18)'; ctx.beginPath();
      points.forEach((point, index) => {
        points.map((other, i) => ({ other, i, distance: Math.hypot(point.x - other.x, point.y - other.y) }))
          .filter(item => item.i > index && item.distance < this.width * .22)
          .sort((a, b) => a.distance - b.distance).slice(0, 3).forEach(({ other }) => {
            ctx.moveTo(point.x, point.y); ctx.lineTo(other.x, other.y);
          });
      });
      ctx.stroke();
      points.forEach((point, index) => this.dot(point.x, point.y, index % 4 ? 1.4 : 3, .22, true, ctx));
      ctx.restore();
      ctx.strokeStyle = 'rgba(45,155,255,.09)'; ctx.lineWidth = .6; ctx.beginPath();
      this.edges.forEach(([a, b]) => {
        ctx.moveTo(this.nodes[a].x, this.nodes[a].y); ctx.lineTo(this.nodes[b].x, this.nodes[b].y);
      });
      ctx.stroke();
    }
    curve(index, time) {
      const module = this.moduleLayout[index];
      const node = this.nodes[this.anchors?.[index]];
      const dy = time ? Math.sin(time * (Math.PI * 2 / (6 + index)) + index * 1.7) * CONFIG.floatAmplitude : 0;
      module.dy = dy;
      const a = { x: module.x, y: module.y + dy };
      const b = node || { x: this.width * .78, y: this.height * .5 };
      return [a, { x: a.x + this.width * .13, y: a.y + [90, 65, -60, -100][index] },
        { x: b.x - this.width * .16, y: b.y + [40, -70, 80, 50][index] }, b];
    }
    bezier(points, t) {
      const s = 1 - t;
      return { x: s ** 3 * points[0].x + 3 * s * s * t * points[1].x + 3 * s * t * t * points[2].x + t ** 3 * points[3].x,
        y: s ** 3 * points[0].y + 3 * s * s * t * points[1].y + 3 * s * t * t * points[2].y + t ** 3 * points[3].y };
    }
    drawConnections(time) {
      const ctx = this.ctx;
      this.modules.forEach((el, index) => {
        const points = this.curve(index, time), layout = this.moduleLayout[index];
        const transform = `translateY(${layout.dy.toFixed(2)}px) rotate(${layout.angle}deg)`;
        if (layout.transform !== transform) { el.style.transform = transform; layout.transform = transform; }
        const active = !!time && layout.until > time;
        if (layout.active !== active) { el.classList.toggle('active', active); layout.active = active; }
        ctx.lineWidth = .85; ctx.strokeStyle = 'rgba(40,151,235,.36)';
        ctx.beginPath(); ctx.moveTo(points[0].x, points[0].y);
        ctx.bezierCurveTo(points[1].x, points[1].y, points[2].x, points[2].y, points[3].x, points[3].y); ctx.stroke();
        [0, .14, .85].forEach(t => { const p = this.bezier(points, t); this.dot(p.x, p.y, t ? 1.9 : 2.3, .7, true); });
        this.signals.filter(s => s.index === index).forEach(signal => {
          let t = Math.min(1, (this.time - signal.start) / signal.duration);
          if (!signal.reverse) t = 1 - t;
          const point = this.bezier(points, t); this.dot(point.x, point.y, 2.6, .95, true);
        });
      });
    }
    destroy() {
      cancelAnimationFrame(this.frame); this.abort?.abort(); this.resizeObserver?.disconnect();
      this.waveRenderer?.destroy();
      this.pulses = []; this.signals = []; this.nodes = []; this.edges = [];
      this.canvas.dataset.sceneState = 'disposed';
    }
  }
  let scene;
  function mount() {
    if (scene) return;
    try { scene = new NeuralScene(); scene.init(); }
    catch (error) { scene?.destroy(); console.warn('Cortex: exibindo cenário estático.', error); }
  }
  mount();
  window.addEventListener('pagehide', () => { scene?.destroy(); scene = null; });
  window.addEventListener('pageshow', mount);
})();
