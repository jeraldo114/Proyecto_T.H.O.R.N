import { FilesetResolver, HandLandmarker } from '@mediapipe/tasks-vision';

let detector;
self.onmessage = async event => {
  const value = event.data;
  if (value.type === 'init') {
    try {
      const files = await FilesetResolver.forVisionTasks('/hud/assets', true);
      detector = await HandLandmarker.createFromOptions(files, {
        baseOptions: { modelAssetPath: '/hud/assets/hand_landmarker.task', delegate: 'CPU' },
        runningMode: 'VIDEO', numHands: 1, canvas: new OffscreenCanvas(640, 480),
        minHandDetectionConfidence: 0.65, minHandPresenceConfidence: 0.65, minTrackingConfidence: 0.65,
      });
      self.postMessage({ type: 'ready' });
    } catch { self.postMessage({ type: 'error', code: 'vision_model_unavailable' }); }
  } else if (value.type === 'frame') {
    try {
      const result = detector.detectForVideo(value.image, value.timestamp);
      self.postMessage({ type: 'result', landmarks: result.landmarks[0] || [], timestamp: value.timestamp });
    } catch { self.postMessage({ type: 'error', code: 'vision_inference_failed' }); }
    finally { value.image?.close(); }
  }
};
