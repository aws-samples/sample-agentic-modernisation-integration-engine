import type { AuthConfig } from '../types';

const TOKEN_KEY = 'auth_token';

/**
 * SSRF guard: reject absolute URLs in auth redirects.
 * Only relative paths are safe to follow.
 */
function assertRelativeUrl(url: string): void {
  if (/^https?:\/\//i.test(url) || url.startsWith('//')) {
    throw new Error('Absolute URLs in auth redirects are not allowed (SSRF protection)');
  }
}

export async function getAuthConfig(): Promise<AuthConfig> {
  const response = await fetch('/api/auth/config');
  if (!response.ok) {
    throw new Error(`Failed to fetch auth config: ${response.status}`);
  }
  return response.json() as Promise<AuthConfig>;
}

export async function login(username: string, password: string): Promise<{ token: string }> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw new Error(`Login failed: ${response.status}`);
  }
  const data = await response.json();

  // SSRF guard: if response includes a redirect, validate it
  if (data.redirect && typeof data.redirect === 'string') {
    assertRelativeUrl(data.redirect);
  }

  return data as { token: string };
}

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * Detect auth mode from config response.
 * Returns 'disabled' | 'local' | 'cognito'
 */
export function detectAuthMode(config: AuthConfig): 'disabled' | 'local' | 'cognito' {
  if (config.mode === 'disabled') return 'disabled';
  if (config.mode === 'cognito') return 'cognito';
  return 'local';
}
