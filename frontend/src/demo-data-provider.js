(function (global) {
  'use strict';
  class DemoDataProvider {
    constructor() { this.mode = 'demo'; this.data = null; }
    async load() {
      if (this.data) return this.data;
      const url = new URL('./demo/demo_run.json', global.location.href);
      const response = await fetch(url, { credentials: 'omit', redirect: 'error' });
      if (!response.ok) throw new Error(`静态 Demo 数据加载失败 (${response.status})`);
      const data = await response.json();
      if (data.schemaVersion !== 'rivio.demo.v1' || data.provenance?.taskId !== 'task_user_5117fc817c3a'
        || !data.finalSnapshot?.workspace?.report || !Array.isArray(data.replay?.events)) {
        throw new Error('Demo 数据格式或唯一 Run 标识不正确');
      }
      this.data = data; this.replay = new global.DemoReplay(data);
      return data;
    }
    request() { return Promise.reject(new Error('Demo 模式禁止 API 请求，不会回退到 Live。')); }
    subscribePipeline() { throw new Error('Demo 模式禁止 EventSource。'); }
    dispose() { this.replay?.stop(); }
  }
  global.DemoDataProvider = DemoDataProvider;
})(window);
