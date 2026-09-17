(function (global) {
  'use strict';
  // Selected once, before app setup, task restoration or any request.
  const demo = new URLSearchParams(global.location.search).get('demo') === '1';
  const base = /^(http:|https:)$/.test(global.location.protocol) ? global.location.origin : 'http://127.0.0.1:8000';
  global.researchDataProvider = demo ? new global.DemoDataProvider() : new global.LiveDataProvider(base);
})(window);
