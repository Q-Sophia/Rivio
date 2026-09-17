(function (global) {
  'use strict';
  class LiveDataProvider {
    constructor(base) { this.base = base; this.mode = 'live'; }
    async request(path, options = {}) {
      const response = await fetch(`${this.base}${path}`, options);
      if (!response.ok) {
        const text = await response.text();
        let detail = text;
        try { detail = JSON.parse(text).detail || text; } catch (_) { /* Original text. */ }
        throw new Error(`${response.status} ${response.statusText}: ${detail}`);
      }
      return response.json();
    }
    subscribePipeline(path) { return new EventSource(`${this.base}${path}`); }
  }
  global.LiveDataProvider = LiveDataProvider;
})(window);
