// A sustained slow window triggers one downgrade; high and low never oscillate.
export function createQualityController(setting, apply) {
  const requested = ['auto', 'high', 'low'].includes(setting) ? setting : 'auto';
  let mode = requested === 'low' ? 'low' : 'high';
  let start = null, previous = null, count = 0, slow = 0;
  apply(mode);
  return {
    get mode() { return mode; },
    sample(now, visible) {
      if (requested !== 'auto' || mode === 'low') return;
      if (!visible || !Number.isFinite(now)) {
        start = previous = null; count = slow = 0;
        return;
      }
      if (previous === null) { start = previous = now; return; }
      const dt = now - previous;
      previous = now;
      if (dt <= 0) return;
      count++;
      if (dt > 40) slow++;
      if (now - start >= 2000 && count >= 2) {
        if (slow / count >= .75) {
          mode = 'low'; apply(mode);
        }
        start = now; count = slow = 0;
      }
    }
  };
}
