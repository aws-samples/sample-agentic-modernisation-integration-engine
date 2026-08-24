import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Drawer,
  Box,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Collapse,
  Typography,
  Badge,
  Divider,
  IconButton,
  Avatar,
} from '@mui/material';
import {
  Dashboard as DashboardIcon,
  Code as CodeIcon,
  History as HistoryIcon,
  Transform as TransformIcon,
  Assessment as AssessmentIcon,
  Build as BuildIcon,
  ExpandLess,
  ExpandMore,
  Logout as LogoutIcon,
  Person as PersonIcon,
} from '@mui/icons-material';
import { useAuth } from '../contexts/AuthContext';

interface NavigationProps {
  selectedNavItem: string;
  analysisCount: number;
  onNavItemSelect: (item: string) => void;
  onLogout?: () => void;
}

const DRAWER_WIDTH = 280;

export function Navigation({ selectedNavItem, analysisCount, onNavItemSelect, onLogout }: NavigationProps) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [codeAnalyseOpen, setCodeAnalyseOpen] = useState(true);
  const [awsTransformOpen, setAwsTransformOpen] = useState(true);

  const handleNavClick = (item: string, path: string) => {
    onNavItemSelect(item);
    navigate(path);
  };

  const handleLogout = () => {
    if (onLogout) {
      onLogout();
    } else {
      logout();
    }
  };

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: DRAWER_WIDTH,
        flexShrink: 0,
        '& .MuiDrawer-paper': {
          width: DRAWER_WIDTH,
          boxSizing: 'border-box',
          bgcolor: 'primary.main',
          color: 'white',
        },
      }}
    >
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <CodeIcon sx={{ color: 'secondary.main' }} />
        <Typography variant="h6" noWrap sx={{ color: 'white', fontWeight: 700 }}>
          Code Analyse & Transform
        </Typography>
      </Box>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.12)' }} />

      <List component="nav" sx={{ flex: 1, px: 1 }}>
        {/* Main Section */}
        <ListItemButton
          selected={selectedNavItem === 'dashboard'}
          onClick={() => handleNavClick('dashboard', '/')}
          sx={{
            borderRadius: 1,
            mb: 0.5,
            '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
            '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
          }}
        >
          <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
            <DashboardIcon />
          </ListItemIcon>
          <ListItemText primary="Dashboard" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
        </ListItemButton>

        {/* Code Analyse Section */}
        <ListItemButton
          onClick={() => setCodeAnalyseOpen(!codeAnalyseOpen)}
          sx={{
            borderRadius: 1,
            mb: 0.5,
            '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
          }}
        >
          <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
            <Badge badgeContent={analysisCount} color="secondary" max={99}>
              <AssessmentIcon />
            </Badge>
          </ListItemIcon>
          <ListItemText primary="Code Analyse" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
          {codeAnalyseOpen ? <ExpandLess /> : <ExpandMore />}
        </ListItemButton>

        <Collapse in={codeAnalyseOpen} timeout="auto" unmountOnExit>
          <List component="div" disablePadding>
            <ListItemButton
              selected={selectedNavItem === 'analysis'}
              onClick={() => handleNavClick('analysis', '/analysis')}
              sx={{
                pl: 4,
                borderRadius: 1,
                mb: 0.5,
                '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
                '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
                <CodeIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Code Analyse" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
            </ListItemButton>

            <ListItemButton
              selected={selectedNavItem === 'previous'}
              onClick={() => handleNavClick('previous', '/previous')}
              sx={{
                pl: 4,
                borderRadius: 1,
                mb: 0.5,
                '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
                '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
                <HistoryIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Previous Analyses" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
            </ListItemButton>
          </List>
        </Collapse>

        {/* AWS Transform Section */}
        <ListItemButton
          onClick={() => setAwsTransformOpen(!awsTransformOpen)}
          sx={{
            borderRadius: 1,
            mb: 0.5,
            '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
          }}
        >
          <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
            <TransformIcon />
          </ListItemIcon>
          <ListItemText primary="AWS Transform" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
          {awsTransformOpen ? <ExpandLess /> : <ExpandMore />}
        </ListItemButton>

        <Collapse in={awsTransformOpen} timeout="auto" unmountOnExit>
          <List component="div" disablePadding>
            <ListItemButton
              selected={selectedNavItem === 'transformations'}
              onClick={() => handleNavClick('transformations', '/transformations')}
              sx={{
                pl: 4,
                borderRadius: 1,
                mb: 0.5,
                '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
                '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
                <BuildIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="Transforms" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
            </ListItemButton>

            <ListItemButton
              selected={selectedNavItem === 'atx-analysis'}
              onClick={() => handleNavClick('atx-analysis', '/atx-analysis')}
              sx={{
                pl: 4,
                borderRadius: 1,
                mb: 0.5,
                '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
                '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
                <AssessmentIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="ATX Analyse" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
            </ListItemButton>

            <ListItemButton
              selected={selectedNavItem === 'atx-transform'}
              onClick={() => handleNavClick('atx-transform', '/atx-transform')}
              sx={{
                pl: 4,
                borderRadius: 1,
                mb: 0.5,
                '&.Mui-selected': { bgcolor: 'rgba(255,153,0,0.15)' },
                '&:hover': { bgcolor: 'rgba(255,255,255,0.08)' },
              }}
            >
              <ListItemIcon sx={{ color: 'inherit', minWidth: 40 }}>
                <TransformIcon fontSize="small" />
              </ListItemIcon>
              <ListItemText primary="ATX Transform" sx={{ '& .MuiListItemText-primary': { color: 'white' } }} />
            </ListItemButton>
          </List>
        </Collapse>
      </List>

      <Divider sx={{ borderColor: 'rgba(255,255,255,0.12)' }} />

      {/* User info and logout */}
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', gap: 1 }}>
        <Avatar sx={{ width: 32, height: 32, bgcolor: 'secondary.main' }}>
          <PersonIcon fontSize="small" />
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" sx={{ color: 'white', fontWeight: 500 }} noWrap>
            {user?.username ?? 'User'}
          </Typography>
        </Box>
        <IconButton size="small" onClick={handleLogout} sx={{ color: 'rgba(255,255,255,0.7)' }}>
          <LogoutIcon fontSize="small" />
        </IconButton>
      </Box>
    </Drawer>
  );
}
