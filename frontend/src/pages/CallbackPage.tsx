import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, CircularProgress, Typography } from '@mui/material';
import { setStoredToken } from '../services/authService';

/**
 * Cognito OAuth callback page.
 * Extracts the access token from the URL hash fragment and stores it.
 */
export function CallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    const hash = window.location.hash.substring(1);
    const params = new URLSearchParams(hash);
    const accessToken = params.get('access_token') ?? params.get('id_token');

    if (accessToken) {
      setStoredToken(accessToken);
      navigate('/');
    } else {
      // No token found — redirect to login
      navigate('/login');
    }
  }, [navigate]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
      <CircularProgress />
      <Typography sx={{ mt: 2 }} color="text.secondary">
        Processing authentication...
      </Typography>
    </Box>
  );
}
