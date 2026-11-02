// Sesiones opacas en RAM. La autoridad permanente solo se usa para el intercambio.
export class SessionClient {
  constructor({ fetcher = globalThis.fetch.bind(globalThis), clock = () => Date.now() } = {}) {
    this.fetcher = fetcher; this.clock = clock; this.token = ''; this.deadline = 0;
    this.generation = 0; this.pending = null; this.identity = '';
  }
  validate(value) {
    if (!value || !/^thn_[A-Za-z0-9_-]{64}$/.test(value.access_token) ||
        !Number.isInteger(value.expires_in) || value.expires_in < 1 || value.expires_in > 900 ||
        !/^[a-f0-9]{32}$/.test(value.session_id) || !Array.isArray(value.scopes)) throw new Error('invalid_session');
    return value;
  }
  async request(operation, token) {
    const response = await this.fetcher(`/api/v1/auth/${operation}/`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: '{}', cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(6000),
    });
    if (!response.ok) { const error = new Error('invalid_session'); error.status = response.status; throw error; }
    return this.validate(await response.json());
  }
  exchange(authority) { return this.request('session', authority); }
  accept(value) {
    this.validate(value);
    if (this.identity === value.session_id) { this.generation++; this.pending = null; }
    else this.close();
    this.identity = value.session_id; this.token = value.access_token;
    this.deadline = this.clock() + value.expires_in * 1000;
  }
  async ensure() {
    if (!this.token) { const error = new Error('unauthorized'); error.status = 401; throw error; }
    if (this.deadline - this.clock() > 120000) return this.token;
    // NOTA DEL OPERADOR: el HUD consulta varias rutas a la vez. Todas comparten
    // una renovacion pendiente para no rotar el mismo token en paralelo.
    if (this.pending) return this.pending;
    const generation = this.generation, previous = this.token;
    const task = this.request('renew', previous).then(value => {
      if (generation !== this.generation) { this.revoke(value.access_token); throw new Error('session_changed'); }
      this.token = value.access_token; this.deadline = this.clock() + value.expires_in * 1000;
      return this.token;
    });
    this.pending = task;
    try { return await task; }
    finally { if (this.pending === task) this.pending = null; }
  }
  revoke(token) {
    if (!token) return;
    // La caducidad del servidor sigue siendo efectiva si la ventana se cierra abruptamente.
    void this.fetcher('/api/v1/auth/revoke/', { method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: '{}', cache: 'no-store', credentials: 'omit', keepalive: true,
      signal: AbortSignal.timeout(6000) }).catch(() => {});
  }
  close() {
    this.generation++; this.revoke(this.token); this.token = ''; this.identity = ''; this.deadline = 0; this.pending = null;
  }
}
