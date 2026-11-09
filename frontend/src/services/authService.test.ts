import { afterEach, describe, expect, it, vi } from 'vitest';
import { login } from './authService';

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body })
  );
}

describe('login', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('reads the backend access_token field', async () => {
    mockFetch(200, { access_token: 'jwt-123', token_type: 'bearer' });
    await expect(login('amie', 'pw')).resolves.toEqual({ token: 'jwt-123' });
  });

  it('reports wrong credentials clearly', async () => {
    mockFetch(401, { detail: 'Invalid username or password' });
    await expect(login('amie', 'bad')).rejects.toThrow('Invalid username or password');
  });

  it('fails when the response has no token', async () => {
    mockFetch(200, {});
    await expect(login('amie', 'pw')).rejects.toThrow('no token');
  });
});
