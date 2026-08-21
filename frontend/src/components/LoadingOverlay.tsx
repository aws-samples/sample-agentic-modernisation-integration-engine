import { Backdrop, CircularProgress } from '@mui/material';

interface LoadingOverlayProps {
  open: boolean;
}

export function LoadingOverlay({ open }: LoadingOverlayProps) {
  return (
    <Backdrop
      open={open}
      sx={{
        color: '#fff',
        zIndex: (theme) => theme.zIndex.drawer + 1,
      }}
    >
      <CircularProgress color="inherit" />
    </Backdrop>
  );
}
