const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Execute the actual detail-panel renderer against a minimal DOM fixture.
const app = fs.readFileSync(path.join(__dirname, '../job_agent/static/app.js'), 'utf8');
const renderer = app.slice(app.indexOf('function fillSide()'), app.indexOf('const dimensionLabels='));
assert.ok(renderer.includes('source-links'));
function linksFor(record) {
  const elements = new Map();
  const node = (tag, text) => ({tag, text, children: [], append(child) {this.children.push(child);}, replaceChildren() {this.children = [];}});
  const $ = id => {if (!elements.has(id)) elements.set(id, node('div', '')); return elements.get(id);};
  vm.runInNewContext(renderer + '\nfillSide();', {current: record, artifacts: [], $, node, dataStage: () => 'pool', fillReading: () => {}});
  return $('source-links').children;
}
const records = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')) : Array.from({length: 9}, (_, i) => ({
  data: {company: 'Fictional Co', [i ? 'detail_url' : 'url']: `https://example.invalid/wechat/vuectmjobs/#/detail?id=fixture&JobID=${i + 1}`},
  view: {title: `Fictional role ${i + 1}`},
}));
for (const record of records) {
  const links = linksFor(record);
  assert.equal(links[0].href, record.data.detail_url || record.data.url);
  assert.ok(links[0].href.includes('#/detail?'));
  assert.ok(links[0].href.includes('JobID='));
  assert.equal(links[0].target, '_blank');
  assert.equal(links[0].rel, 'noopener noreferrer');
}
const repeated = 'https://example.invalid/#/detail?JobID=one';
assert.equal(linksFor({data: {detail_url: repeated, url: repeated, boss_url: 'javascript:bad'}, view: {title: 'Fixture'}}).length, 1);
assert.deepEqual(linksFor({data: {detail_url: repeated, url: 'https://example.invalid/source'}, view: {title: 'Fixture'}}).map(a => a.href), [repeated, 'https://example.invalid/source']);
console.log(`Verified detail links for ${records.length} cards, link priority, duplicate suppression and unsafe URLs.`);
