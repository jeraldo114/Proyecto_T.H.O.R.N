const { registerOcrWorker } = require('./ocr-worker-control.js');
module.exports = function spawnWorker(options) {
  return registerOcrWorker(new Worker(options.workerPath));
};
