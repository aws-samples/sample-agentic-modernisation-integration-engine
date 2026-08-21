import { useState, type FormEvent } from 'react';
import { Box, TextField, Button } from '@mui/material';
import { GitHub as GitHubIcon } from '@mui/icons-material';

interface GitHubInputProps {
  onSubmit: (repoUrl: string, branch: string, patToken?: string) => void;
  loading?: boolean;
}

export function GitHubInput({ onSubmit, loading = false }: GitHubInputProps) {
  const [repoUrl, setRepoUrl] = useState('');
  const [branch, setBranch] = useState('main');
  const [patToken, setPatToken] = useState('');

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!repoUrl.trim()) return;
    onSubmit(repoUrl.trim(), branch.trim() || 'main', patToken.trim() || undefined);
  };

  return (
    <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <TextField
        label="Repository URL"
        placeholder="https://github.com/owner/repo"
        value={repoUrl}
        onChange={(e) => setRepoUrl(e.target.value)}
        required
        fullWidth
        InputProps={{
          startAdornment: <GitHubIcon sx={{ mr: 1, color: 'text.secondary' }} />,
        }}
      />
      <TextField
        label="Branch"
        placeholder="main"
        value={branch}
        onChange={(e) => setBranch(e.target.value)}
        fullWidth
      />
      <TextField
        label="PAT Token (optional)"
        placeholder="ghp_..."
        value={patToken}
        onChange={(e) => setPatToken(e.target.value)}
        type="password"
        fullWidth
        helperText="Required for private repositories"
      />
      <Button
        type="submit"
        variant="contained"
        color="secondary"
        disabled={!repoUrl.trim() || loading}
        sx={{ alignSelf: 'flex-start' }}
      >
        {loading ? 'Analyzing...' : 'Analyze Repository'}
      </Button>
    </Box>
  );
}
