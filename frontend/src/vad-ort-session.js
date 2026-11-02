/** ONNX proxy transfiere buffers; Silero reutiliza estado y sample-rate. */
export function preserveTensorInputs(session, Tensor) {
  return {
    run(feeds) {
      const copies = Object.fromEntries(Object.entries(feeds).map(([name, tensor]) =>
        [name, new Tensor(tensor.type, tensor.data.slice(), [...tensor.dims])]));
      return session.run(copies);
    },
    release() { return session.release(); },
  };
}
