import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeWorkflowFields } from '../web/serialization.mjs';

test('reactive combo output is detached without changing workflow JSON', () => {
    const choices = new Proxy(['generate', 'reuse'], {});
    const data = { links: [[9, 17, 2, 31, 4, choices]],
                   outputs: [{ name: 'stage', type: choices, links: [9] }],
                   properties: { scene: { width: 682, height: 1023 } } };
    const expected = JSON.stringify(data);
    assert.throws(() => structuredClone(data), { name: 'DataCloneError' });
    assert.deepEqual(normalizeWorkflowFields(data), ['links', 'outputs']);
    assert.equal(JSON.stringify(data), expected);
    assert.deepEqual(structuredClone(data), JSON.parse(expected));
});

test('ordinary workflow fields keep values and object identity', () => {
    const data = { widgets_values: ['file.png', 1024], properties: { index: 3 } };
    const original = data.widgets_values;
    assert.deepEqual(normalizeWorkflowFields(data), []);
    assert.equal(data.widgets_values, original);
});
