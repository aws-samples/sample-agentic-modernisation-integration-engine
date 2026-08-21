import { useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import { Box, Typography } from '@mui/material';
import { CloudUpload as CloudUploadIcon } from '@mui/icons-material';

interface FileUploadProps {
  onFileSelect: (file: File) => void;
}

export function FileUpload({ onFileSelect }: FileUploadProps) {
  const onDrop = useCallback(
    (acceptedFiles: File[]) => {
      if (acceptedFiles.length > 0) {
        onFileSelect(acceptedFiles[0]);
      }
    },
    [onFileSelect]
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'application/zip': ['.zip'] },
    multiple: false,
  });

  return (
    <Box
      {...getRootProps()}
      sx={{
        border: '2px dashed',
        borderColor: isDragActive ? 'secondary.main' : 'divider',
        borderRadius: 2,
        p: 4,
        textAlign: 'center',
        cursor: 'pointer',
        bgcolor: isDragActive ? 'rgba(255,153,0,0.04)' : 'transparent',
        transition: 'all 0.2s',
        '&:hover': {
          borderColor: 'secondary.main',
          bgcolor: 'rgba(255,153,0,0.04)',
        },
      }}
    >
      <input {...getInputProps()} />
      <CloudUploadIcon sx={{ fontSize: 48, color: 'text.secondary', mb: 1 }} />
      <Typography variant="body1" gutterBottom>
        {isDragActive ? 'Drop the ZIP file here' : 'Drag & drop a ZIP file here, or click to select'}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Accepts .zip files only
      </Typography>
    </Box>
  );
}
