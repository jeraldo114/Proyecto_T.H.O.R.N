import test from 'node:test';
import assert from 'node:assert/strict';
import { panelPixels, panelPosition, initialPosition } from '../src/panel-layout.js';
import { WorkspaceClient } from '../src/workspace.js';

test('ventanas acotan NaN, extremos y pantallas menores que su tamaño', () => {
  assert.deepEqual(panelPixels({ x: Infinity, y: -2 }, { width: 500, height: 300 }, { width: 700, height: 400 }), { x: 0, y: 0 });
  assert.deepEqual(panelPosition({ x: 900, y: -1 }, { width: 800, height: 600 }, { width: 400, height: 300 }), { x: 1, y: 0 });
  const area = { width: 1200, height: 700 }, size = { width: 600, height: 400 }, source = { x: .33, y: .75 };
  assert.deepEqual(panelPosition(panelPixels(source, area, size), area, size), source);
  assert.notDeepEqual(initialPosition('map', 0), initialPosition('document', 1));
});

test('mover y cerrar se envian en orden y una respuesta vieja no reabre ventanas', async () => {
  let release; const order = [];
  const client = new WorkspaceClient(async (route, options) => {
    order.push(options.body.operation);
    if (order.length === 1) await new Promise(resolve => { release = resolve; });
    return { revision: order.length, panels: options.body.operation === 'close' ? [] : [{ id: 'a' }] };
  });
  const moving = client.change('a', 'move', { x: .2, y: .3 });
  const closing = client.change('a', 'close');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(order, ['move']); release(); await Promise.all([moving, closing]);
  assert.deepEqual(order, ['move', 'close']); assert.deepEqual(client.panels, []);
});

test('una consulta inicial tardia no borra un resultado recien recibido', async () => {
  let release;
  const client = new WorkspaceClient(() => new Promise(resolve => { release = resolve; }));
  const pending = client.refresh();
  const panel = { id: 'a'.repeat(32), kind: 'answer', title: 'Nueva respuesta' };
  client.accept(panel); release({ revision: 0, panels: [] }); await pending;
  assert.deepEqual(client.panels, [panel]);
});
