/* All requests use Flask's signed, HTTP-only session cookie. */
window.NearShareAPI = (() => {
  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
    const type = response.headers.get('content-type') || '';
    const data = type.includes('application/json') ? await response.json() : null;
    if (!response.ok) {
      const error = new Error(data?.error || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return data;
  }
  const json = value => JSON.stringify(value);
  return {
    get: path => request(path),
    post: (path, body) => request(path, { method: 'POST', ...(body === undefined ? {} : { body: json(body) }) }),
    patch: (path, body) => request(path, { method: 'PATCH', ...(body === undefined ? {} : { body: json(body) }) }),
    put: (path, body) => request(path, { method: 'PUT', body: json(body) }),
    delete: path => request(path, { method: 'DELETE' }),
    upload: (path, file) => { const body = new FormData(); body.append('image', file); return request(path, { method: 'POST', body }); }
  };
})();
