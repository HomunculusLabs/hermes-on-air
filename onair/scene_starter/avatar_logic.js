// Local, deterministic cues; no text is displayed or sent anywhere.
export function moodForLine(text) {
  if (typeof text !== 'string') return 'neutral';
  if (/\b(wow|whoa|what)\b/i.test(text)) return 'surprised';
  if (/\?/.test(text)) return 'thinking';
  if (/\b(sorry|apolog\w*|sad|grief|unfortunately)\b/i.test(text)) return 'sad';
  if (/!|\b(haha|hehe|laugh\w*|wonderful|yay)\b/i.test(text)) return 'happy';
  return 'neutral';
}

// Prefer a bound VRM preset, then known optional custom channels.
const moodFallbacks = {
  happy: ['Expr.brow_warm', 'Expr.mouth_soft_smile'],
  sad: ['Expr.brow_concerned', 'Expr.mouth_pout'],
  surprised: ['Expr.brow_surprised', 'Expr.mouth_small_o'],
  relaxed: ['Expr.brow_warm', 'Expr.mouth_soft_smile']
};
export function moodChannels(mood, bound) {
  if (!Object.prototype.hasOwnProperty.call(moodFallbacks, mood)) return [];
  return bound(mood) ? [mood] : moodFallbacks[mood].filter(bound);
}

// World-space relaxed upper-arm direction, independent of the authored rest pose.
export function armTarget(side) { return [side < 0 ? -.27 : .27, -.963, 0]; }
export function framingScale(height, framing) {
  if (!(height > .01) || !Number.isFinite(height)) return 1;
  return (framing === 'bust' ? 4.2 : framing === 'full' ? 2.6 : 3.4) / height;
}
