import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, Button, TextField, Typography, Paper, CircularProgress, Alert } from '@mui/material';
import { useAuth } from '../contexts/AuthContext';
import { getAuthConfig, detectAuthMode, setStoredToken } from '../services/authService';

export function LoginPage() {
  const navigate = useNavigate();
  const { login, isAuthenticated } = useAuth();
  const [mode, setMode] = useState<'disabled' | 'local' | 'cognito' | null>(null);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isAuthenticated) {
      navigate('/');
      return;
    }

    async function detectMode() {
      try {
        const config = await getAuthConfig();
        const detected = detectAuthMode(config);
        setMode(detected);

        if (detected === 'disabled') {
          // Auto-login with demo token
          setStoredToken('demo-token');
          navigate('/');
        } else if (detected === 'cognito') {
          // Redirect to Cognito hosted UI
          const domain = config.cognito_domain;
          const clientId = config.cognito_client_id;
          const redirectUri = config.redirect_uri ?? `${window.location.origin}/callback`;
          window.location.href = `${domain}/login?client_id=${clientId}&response_type=token&scope=openid+profile+email&redirect_uri=${encodeURIComponent(redirectUri)}`;
        }
      } catch {
        // Default to local mode on config failure
        setMode('local');
      } finally {
        setLoading(false);
      }
    }

    void detectMode();
  }, [isAuthenticated, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await login(username, password);
      navigate('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    }
  };

  if (loading || mode === 'disabled' || mode === 'cognito') {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        bgcolor: 'background.default',
      }}
    >
      <Paper sx={{ p: 4, maxWidth: 400, width: '100%' }}>
        <Typography variant="h5" sx={{ mb: 3, textAlign: 'center' }}>
          Code Transformation Engine
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3, textAlign: 'center' }}>
          Sign in to continue
        </Typography>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        <form onSubmit={handleSubmit}>
          <TextField
            fullWidth
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            sx={{ mb: 2 }}
            autoComplete="username"
          />
          <TextField
            fullWidth
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            sx={{ mb: 3 }}
            autoComplete="current-password"
          />
          <Button
            fullWidth
            type="submit"
            variant="contained"
            size="large"
          >
            Sign In
          </Button>
        </form>
      </Paper>
    </Box>
  );
}
