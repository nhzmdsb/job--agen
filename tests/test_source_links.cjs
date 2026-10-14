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
  const node = (tag, text) => ({tag, text, children: [], append(child) {this.children.push(child);}, replaceChildren() {this.children = [];}, removeAttribute(name) {delete this[name];}});
  const $ = id => {if (!elements.has(id)) elements.set(id, node('div', '')); return elements.get(id);};
  vm.runInNewContext(renderer + '\nfillSide();', {current: record, editing: false, artifacts: [], $, node, dataStage: () => 'pool', stages: {pool: '机会池'}, colors: {green: '绿色'}, fillReading: () => {}});
  const links = $('source-links').children;
  links.action = $('apply-link'); links.company = $('company-link'); links.status = $('apply-status');
  return links;
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
for (const [field, label] of [['application_url','前往投递 ↗'],['recruitment_url','打开招聘页面 ↗'],['detail_url','查看岗位详情 ↗'],['url','查看来源 ↗'],['boss_url','查看 BOSS 来源 ↗']]) {
  const data = {[field]: repeated, company_url: 'https://example.invalid/company'};
  const links = linksFor({data, view: {title: 'Fixture', color: 'green'}});
  assert.equal(links.action.href, repeated);
  assert.equal(links.action.textContent, label);
  assert.equal(links.company.href, data.company_url);
  assert.equal(links.status.textContent, field === 'application_url' ? '' : '投递入口待补齐。');
}
console.log(`Verified detail links for ${records.length} cards, action labels, duplicate suppression and unsafe URLs.`);
