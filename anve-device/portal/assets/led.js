"use strict";
// Plays a status-light pattern on an element. Patterns come from the box
// (anvebox_agent/lights.py), so the screen and the real light share one table.
// With reduced motion the light holds still at its brightest step instead.
window.AnveLed = (() => {
  const OFF = 0.14; // an "off" light stays faintly visible, so you can see where it is
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const level = (b) => OFF + (1 - OFF) * b;

  function play(el, pattern) {
    if (!el || !pattern) return;
    const key = JSON.stringify(pattern.steps) + pattern.smooth;
    if (el.dataset.playing === key) return;
    el.dataset.playing = key;
    el.getAnimations().forEach((a) => a.cancel());
    const steps = pattern.steps;
    const total = steps.reduce((sum, step) => sum + step[1], 0);
    const brightest = Math.max(...steps.map((step) => step[0]));
    el.style.opacity = String(level(brightest));
    if (reduce.matches || steps.length < 2 || typeof el.animate !== "function") return;
    const frames = [];
    let t = 0;
    if (pattern.smooth) {
      frames.push({ opacity: level(steps[steps.length - 1][0]), offset: 0 });
      for (const [b, ms] of steps) {
        t += ms;
        frames.push({ opacity: level(b), offset: t / total });
      }
      el.animate(frames, { duration: total, iterations: Infinity, easing: "ease-in-out" });
    } else {
      for (const [b, ms] of steps) {
        frames.push({ opacity: level(b), offset: t / total });
        t += ms;
        frames.push({ opacity: level(b), offset: t / total });
      }
      el.animate(frames, { duration: total, iterations: Infinity });
    }
  }

  return { play };
})();
