import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import {
  getAuthConfig,
  login as loginApi,
  getStoredToken,
  setStoredToken,
  clearToken,
  detectAuthMode,
} from '../services/authService';

interface AuthUser {
  username: string;
}

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: AuthUser | null;
  token: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);

  const checkAuth = useCallback(async () => {
    setIsLoading(true);
    try {
      const config = await getAuthConfig();
      const mode = detectAuthMode(config);

      if (mode === 'disabled') {
        // Auto-authenticate with demo token
        const demoToken = 'demo-token';
        setStoredToken(demoToken);
        setToken(demoToken);
        setUser({ username: 'demo' });
        setIsAuthenticated(true);
      } else {
        // Validate stored token
        const storedToken = getStoredToken();
        if (storedToken) {
          setToken(storedToken);
          setUser({ username: 'user' });
          setIsAuthenticated(true);
        } else {
          setIsAuthenticated(false);
          setUser(null);
          setToken(null);
        }
      }
    } catch {
      // If auth config fails, fall back to checking stored token
      const storedToken = getStoredToken();
      if (storedToken) {
        setToken(storedToken);
        setUser({ username: 'user' });
        setIsAuthenticated(true);
      } else {
        setIsAuthenticated(false);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loginFn = useCallback(async (username: string, password: string) => {
    const result = await loginApi(username, password);
    setStoredToken(result.token);
    setToken(result.token);
    setUser({ username });
    setIsAuthenticated(true);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setToken(null);
    setUser(null);
    setIsAuthenticated(false);
  }, []);

  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  const value: AuthContextValue = {
    isAuthenticated,
    isLoading,
    user,
    token,
    login: loginFn,
    logout,
    checkAuth,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
