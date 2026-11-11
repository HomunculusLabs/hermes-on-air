// Preview geometry for the quick-start causal screen, read from the real layout (not DOMRect.parentElement):
// each .onair-preview-screen must sit inside its card, and a shown caption inside its screen, never over
// the card's PRIVATE tag.
async function previewGeometry(page) {
  return page.evaluate(() => {
    const r = (b) => b && [b.left, b.top, b.right, b.bottom].map((v) => Math.round(v * 10) / 10);
    const inside = (a, b) => a.left >= b.left - 0.5 && a.right <= b.right + 0.5 && a.top >= b.top - 0.5 && a.bottom <= b.bottom + 0.5;
    const overlap = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const screens = [];
    for (const screen of document.querySelectorAll('.onair-qs-causal .onair-preview-screen')) {
      const card = screen.parentElement.closest('li');  // the element's parent card
      const tag = card && card.querySelector('.onair-tag');
      const cap = screen.querySelector('.onair-preview-caption');
      const s = screen.getBoundingClientRect(), c = card.getBoundingClientRect();
      const k = cap && cap.getBoundingClientRect(), t = tag && tag.getBoundingClientRect();
      screens.push({screen: r(s), card: r(c), caption: r(k), tag: r(t), tagText: tag ? tag.textContent : '',
        screenInCard: inside(s, c), captionInScreen: k ? inside(k, s) : null,
        captionOverTag: k && t ? overlap(k, t) : null, caption_text: cap ? cap.textContent.slice(0, 120) : ''});
    }
    return {width: innerWidth, screens};
  });
}

function geometryProblems(g, {caption = false} = {}) {
  const out = [];
  if (!g.screens.length) out.push('no preview screen at ' + g.width);
  for (const s of g.screens) {
    if (!s.screenInCard) out.push('screen outside its card at ' + g.width + ': ' + JSON.stringify(s));
    if (caption && s.captionInScreen !== true) out.push('caption outside its screen at ' + g.width + ': ' + JSON.stringify(s));
    if (s.captionOverTag) out.push('caption covers the PRIVATE tag at ' + g.width + ': ' + JSON.stringify(s));
  }
  return out;
}

module.exports = {previewGeometry, geometryProblems};
