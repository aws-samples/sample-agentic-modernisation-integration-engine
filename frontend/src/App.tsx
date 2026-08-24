import { useState, useCallback } from 'react';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Box, Typography } from '@mui/material';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { Navigation } from './components/Navigation';
import { LoginPage } from './pages/LoginPage';
import { CallbackPage } from './pages/CallbackPage';
import { Dashboard } from './pages/Dashboard';
import { BedrockAnalysisPage } from './pages/BedrockAnalysisPage';
import { AnalysisResultsDisplay } from './pages/AnalysisResultsDisplay';
import { PreviousAnalysesPage } from './pages/PreviousAnalysesPage';
import { AtxAnalysisPage } from './pages/AtxAnalysisPage';
import { AtxJavaTransformPage } from './pages/AtxJavaTransformPage';
import { AtxTransformPage } from './pages/AtxTransformPage';
import { TransformationManagement } from './pages/TransformationManagement';
import type { AnalysisResult } from './types';

const theme = createTheme({
  palette: {
    primary: {
      main: '#232F3E',
    },
    secondary: {
      main: '#FF9900',
    },
    background: {
      default: '#F8F9FA',
    },
  },
  typography: {
    h1: { fontWeight: 700, fontSize: '2.5rem' },
    h2: { fontWeight: 600, fontSize: '2rem' },
    h3: { fontWeight: 600, fontSize: '1.75rem' },
    h4: { fontWeight: 600, fontSize: '1.5rem' },
    h5: { fontWeight: 500, fontSize: '1.25rem' },
    h6: { fontWeight: 500, fontSize: '1rem' },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: {
    borderRadius: 8,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          padding: '8px 16px',
        },
        contained: {
          boxShadow: 'none',
          '&:hover': {
            boxShadow: '0 2px 4px rgba(0,0,0,0.1)',
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 12,
          boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 6,
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          borderRight: 'none',
          boxShadow: '2px 0 8px rgba(0,0,0,0.05)',
        },
      },
    },
  },
});

function AuthGate({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Typography>Loading...</Typography>
      </Box>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return <>{children}</>;
}

function AppContent() {
  const [selectedNavItem, setSelectedNavItem] = useState('dashboard');
  const [currentAnalysisId, setCurrentAnalysisId] = useState<string | null>(null);
  const [analysisResults, setAnalysisResults] = useState<AnalysisResult | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [analysisCount, setAnalysisCount] = useState(0);

  const handleAnalysisStart = useCallback((analysisId: string) => {
    setCurrentAnalysisId(analysisId);
    setIsLoading(true);
  }, []);

  const handleAnalysisComplete = useCallback((result: AnalysisResult) => {
    setAnalysisResults(result);
    setIsLoading(false);
    setAnalysisCount((prev) => prev + 1);
  }, []);

  const handleAnalysisSelect = useCallback((analysisId: string) => {
    setCurrentAnalysisId(analysisId);
  }, []);

  const handleFileSelect = useCallback((file: string) => {
    setSelectedFile(file);
  }, []);

  const handleNavItemSelect = useCallback((item: string) => {
    setSelectedNavItem(item);
  }, []);

  // Suppress unused variable warnings for state used by child components in later tasks
  void currentAnalysisId;
  void analysisResults;
  void selectedFile;
  void isLoading;
  void handleAnalysisStart;
  void handleAnalysisComplete;
  void handleAnalysisSelect;
  void handleFileSelect;

  return (
    <Routes>
      {/* Auth routes — no Navigation */}
      <Route path="/login" element={<LoginPage />} />
      <Route path="/callback" element={<CallbackPage />} />

      {/* All other routes — AuthGate + Navigation */}
      <Route
        path="/*"
        element={
          <AuthGate>
            <Box sx={{ display: 'flex' }}>
              <Navigation
                selectedNavItem={selectedNavItem}
                analysisCount={analysisCount}
                onNavItemSelect={handleNavItemSelect}
              />
              <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/analysis" element={<BedrockAnalysisPage onAnalysisStart={handleAnalysisStart} />} />
                  <Route path="/results/:id" element={<AnalysisResultsDisplay />} />
                  <Route path="/previous" element={<PreviousAnalysesPage />} />
                  <Route path="/atx-analysis" element={<AtxAnalysisPage />} />
                  <Route path="/atx-transform" element={<AtxJavaTransformPage />} />
                  <Route path="/transform-results/:id" element={<AtxTransformPage />} />
                  <Route path="/transformations" element={<TransformationManagement />} />
                </Routes>
              </Box>
            </Box>
          </AuthGate>
        }
      />
    </Routes>
  );
}

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <AuthProvider>
          <AppContent />
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
