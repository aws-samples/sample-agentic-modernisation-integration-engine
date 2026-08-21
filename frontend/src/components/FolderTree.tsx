import { useState, useMemo, useCallback } from 'react';
import {
  Box,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Collapse,
  TextField,
  InputAdornment,
  Typography,
} from '@mui/material';
import FolderIcon from '@mui/icons-material/Folder';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';
import SearchIcon from '@mui/icons-material/Search';
import type { FolderNode } from '../types';

interface FolderTreeProps {
  data: FolderNode;
  onFileSelect?: (path: string) => void;
  showFilter?: boolean;
}

interface TreeNodeProps {
  node: FolderNode;
  depth: number;
  path: string;
  filter: string;
  onFileSelect?: (path: string) => void;
  expandedNodes: Set<string>;
  toggleExpand: (path: string) => void;
}

function matchesFilter(node: FolderNode, filter: string): boolean {
  if (!filter) return true;
  const lower = filter.toLowerCase();
  if (node.name.toLowerCase().includes(lower)) return true;
  if (node.children) {
    return node.children.some((child) => matchesFilter(child, lower));
  }
  return false;
}

function TreeNode({
  node,
  depth,
  path,
  filter,
  onFileSelect,
  expandedNodes,
  toggleExpand,
}: TreeNodeProps) {
  const fullPath = path ? `${path}/${node.name}` : node.name;
  const isDirectory = node.type === 'directory';
  const isExpanded = expandedNodes.has(fullPath);

  if (filter && !matchesFilter(node, filter)) {
    return null;
  }

  const handleClick = () => {
    if (isDirectory) {
      toggleExpand(fullPath);
    } else if (onFileSelect) {
      onFileSelect(fullPath);
    }
  };

  return (
    <>
      <ListItemButton
        onClick={handleClick}
        sx={{ pl: 2 + depth * 2 }}
        dense
      >
        <ListItemIcon sx={{ minWidth: 32 }}>
          {isDirectory ? (
            <FolderIcon sx={{ color: '#FF9900', fontSize: 20 }} />
          ) : (
            <InsertDriveFileIcon sx={{ color: '#6C757D', fontSize: 20 }} />
          )}
        </ListItemIcon>
        <ListItemText
          primary={node.name}
          primaryTypographyProps={{ fontSize: '0.875rem' }}
        />
        {isDirectory && node.children && node.children.length > 0 && (
          isExpanded ? <ExpandLess /> : <ExpandMore />
        )}
      </ListItemButton>
      {isDirectory && node.children && (
        <Collapse in={isExpanded} timeout="auto" unmountOnExit>
          <List disablePadding>
            {node.children.map((child, index) => (
              <TreeNode
                key={`${fullPath}/${child.name}-${index}`}
                node={child}
                depth={depth + 1}
                path={fullPath}
                filter={filter}
                onFileSelect={onFileSelect}
                expandedNodes={expandedNodes}
                toggleExpand={toggleExpand}
              />
            ))}
          </List>
        </Collapse>
      )}
    </>
  );
}

export function FolderTree({ data, onFileSelect, showFilter = true }: FolderTreeProps) {
  const [filter, setFilter] = useState('');
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(
    () => new Set([data?.name ?? ''])
  );

  const toggleExpand = useCallback((path: string) => {
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const filteredView = useMemo(() => {
    if (!data || !data.name) return null;
    return (
      <TreeNode
        node={data}
        depth={0}
        path=""
        filter={filter}
        onFileSelect={onFileSelect}
        expandedNodes={expandedNodes}
        toggleExpand={toggleExpand}
      />
    );
  }, [data, filter, onFileSelect, expandedNodes, toggleExpand]);

  if (!data || !data.name) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No folder structure available.
      </Typography>
    );
  }

  return (
    <Box sx={{ maxHeight: 500, overflow: 'auto' }}>
      {showFilter && (
        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            fullWidth
            placeholder="Filter files..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
            }}
          />
        </Box>
      )}
      <List dense disablePadding>
        {filteredView}
      </List>
    </Box>
  );
}
