import { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Paper,
  Typography,
  Tabs,
  Tab,
  Card,
  CardContent,
  CardActions,
  Button,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Grid,
  Chip,
  IconButton,
  Alert,
  CircularProgress,
} from '@mui/material';
import { Add, Edit, Delete } from '@mui/icons-material';
import {
  getTransformationDefinitions,
  getTransformations,
  createTransformationDefinition,
  updateTransformationDefinition,
  deleteTransformationDefinition,
} from '../services/api';
import type { TransformationDefinition } from '../types';

/**
 * Each tab is sourced from whatever is authoritative for it.
 *
 * Custom definitions live in the backend's CRUD collection (`GET
 * /api/transformations/definitions`) — the collection this page creates, edits and
 * deletes. AWS-managed definitions live in the transform agent's read-only catalog
 * (`GET /transformations`, proxied as `/atx-transform/transformations`), which is the
 * only place they exist: no AWS-managed record is ever written to the CRUD collection,
 * so filtering that collection by `type === 'aws-managed'` was empty by construction.
 *
 * The two loads are independent so an unreachable agent cannot blank the custom tab,
 * and each tab distinguishes "loaded and genuinely empty" from "could not load" —
 * one catch-all covering both is the ambiguous-empty-state failure mode Build
 * Constraint 50 forbids.
 */
type LoadState = 'loading' | 'loaded' | 'error';

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function TransformationManagement() {
  const [activeTab, setActiveTab] = useState(0);

  const [customDefinitions, setCustomDefinitions] = useState<TransformationDefinition[]>([]);
  const [customState, setCustomState] = useState<LoadState>('loading');
  const [customError, setCustomError] = useState<string | null>(null);

  const [awsManaged, setAwsManaged] = useState<TransformationDefinition[]>([]);
  const [awsState, setAwsState] = useState<LoadState>('loading');
  const [awsError, setAwsError] = useState<string | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingDef, setEditingDef] = useState<TransformationDefinition | null>(null);
  const [formName, setFormName] = useState('');
  const [formDescription, setFormDescription] = useState('');
  const [formType, setFormType] = useState('');
  const [formPath, setFormPath] = useState('');
  const [error, setError] = useState<string | null>(null);

  const loadCustomDefinitions = useCallback(async () => {
    setCustomState('loading');
    setCustomError(null);
    try {
      const defs = await getTransformationDefinitions();
      const normalized = Array.isArray(defs) ? defs : [];
      setCustomDefinitions(normalized.filter((d) => d.type !== 'aws-managed'));
      setCustomState('loaded');
    } catch (err: unknown) {
      setCustomDefinitions([]);
      setCustomError(describeError(err));
      setCustomState('error');
    }
  }, []);

  const loadAwsManaged = useCallback(async () => {
    setAwsState('loading');
    setAwsError(null);
    try {
      const catalog = await getTransformations();
      const normalized = Array.isArray(catalog) ? catalog : [];
      setAwsManaged(normalized.filter((d) => d.type === 'aws-managed'));
      setAwsState('loaded');
    } catch (err: unknown) {
      setAwsManaged([]);
      setAwsError(describeError(err));
      setAwsState('error');
    }
  }, []);

  // Load both collections on mount, independently: one failing must not blank the other.
  useEffect(() => {
    void loadCustomDefinitions();
    void loadAwsManaged();
  }, [loadCustomDefinitions, loadAwsManaged]);

  const handleCreate = () => {
    setEditingDef(null);
    setFormName('');
    setFormDescription('');
    setFormType('custom');
    setFormPath('');
    setDialogOpen(true);
  };

  const handleEdit = (def: TransformationDefinition) => {
    setEditingDef(def);
    setFormName(def.name);
    setFormDescription(def.description);
    setFormType(def.type);
    setFormPath(def.definition_path);
    setDialogOpen(true);
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteTransformationDefinition(id);
      setCustomDefinitions((prev) => prev.filter((d) => d.id !== id));
    } catch {
      setError('Failed to delete transformation definition');
    }
  };

  const handleSave = async () => {
    setError(null);
    try {
      if (editingDef) {
        const updated = await updateTransformationDefinition(editingDef.id, {
          name: formName,
          description: formDescription,
          type: formType,
          definition_path: formPath,
        });
        setCustomDefinitions((prev) => prev.map((d) => (d.id === editingDef.id ? updated : d)));
      } else {
        const created = await createTransformationDefinition({
          name: formName,
          description: formDescription,
          type: formType,
          definition_path: formPath,
          published: false,
        });
        setCustomDefinitions((prev) => [...prev, created]);
      }
      setDialogOpen(false);
    } catch {
      setError('Failed to save transformation definition');
    }
  };

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Transformation Management
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <Paper>
        <Tabs value={activeTab} onChange={(_, v: number) => setActiveTab(v)} sx={{ borderBottom: '1px solid', borderColor: 'divider' }}>
          <Tab label="Custom Transformations" />
          <Tab label="AWS Managed" />
        </Tabs>

        <Box sx={{ p: 2 }}>
          {activeTab === 0 && (
            <>
              <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 2 }}>
                <Button variant="contained" startIcon={<Add />} onClick={handleCreate}>
                  New Transformation
                </Button>
              </Box>

              {customState === 'error' && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  Could not load custom transformations: {customError}. The definitions
                  themselves are unaffected.
                </Alert>
              )}

              {customState === 'loading' ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} aria-label="Loading custom transformations" />
                </Box>
              ) : (
                <Grid container spacing={2}>
                  {customDefinitions.map((def) => (
                    <Grid item xs={12} sm={6} md={4} key={def.id}>
                      <Card variant="outlined">
                        <CardContent>
                          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                            <Typography variant="subtitle1" fontWeight={600}>
                              {def.name}
                            </Typography>
                            <Chip
                              label={def.published ? 'Published' : 'Draft'}
                              size="small"
                              color={def.published ? 'success' : 'default'}
                            />
                          </Box>
                          <Typography variant="body2" color="text.secondary" sx={{ mt: 1, minHeight: 40 }}>
                            {def.description}
                          </Typography>
                          <Typography variant="caption" color="text.secondary">
                            Type: {def.type}
                          </Typography>
                        </CardContent>
                        <CardActions>
                          <IconButton size="small" onClick={() => handleEdit(def)} aria-label={`Edit ${def.name}`}>
                            <Edit fontSize="small" />
                          </IconButton>
                          <IconButton
                            size="small"
                            color="error"
                            onClick={() => handleDelete(def.id)}
                            aria-label={`Delete ${def.name}`}
                          >
                            <Delete fontSize="small" />
                          </IconButton>
                        </CardActions>
                      </Card>
                    </Grid>
                  ))}
                  {customState === 'loaded' && customDefinitions.length === 0 && (
                    <Grid item xs={12}>
                      <Typography color="text.secondary" textAlign="center" sx={{ py: 4 }}>
                        No custom transformations defined. Click &quot;New Transformation&quot; to create one.
                      </Typography>
                    </Grid>
                  )}
                </Grid>
              )}
            </>
          )}

          {activeTab === 1 && (
            <>
              {awsState === 'error' && (
                <Alert severity="error" sx={{ mb: 2 }}>
                  Could not load AWS managed transformations: {awsError}. The transform
                  agent may be unreachable — this is not the same as there being none.
                </Alert>
              )}

              {awsState === 'loading' ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
                  <CircularProgress size={24} aria-label="Loading AWS managed transformations" />
                </Box>
              ) : (
                <Grid container spacing={2}>
                  {awsManaged.map((t) => {
                    // The CLI identifier is what a user needs to drive the
                    // transformation; without a resolved one the record cannot be run.
                    const definitionName = t.atx_definition_name ?? null;
                    return (
                      <Grid item xs={12} sm={6} md={4} key={t.id ?? t.name}>
                        {/* Read-only by design: the agent's catalog is not editable here. */}
                        <Card variant="outlined">
                          <CardContent>
                            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 1 }}>
                              <Typography variant="subtitle1" fontWeight={600}>
                                {t.name}
                              </Typography>
                              <Chip label="AWS Managed" size="small" color="primary" />
                            </Box>
                            <Typography variant="body2" color="text.secondary" sx={{ mt: 1, minHeight: 40 }}>
                              {t.description}
                            </Typography>
                            {t.source && t.target && (
                              <Typography variant="caption" color="text.secondary" display="block">
                                {`${t.source} → ${t.target}`}
                              </Typography>
                            )}
                            {definitionName ? (
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                display="block"
                                sx={{ fontFamily: 'monospace', mt: 0.5, wordBreak: 'break-all' }}
                              >
                                {definitionName}
                              </Typography>
                            ) : (
                              <Chip
                                label="Not executable — no ATX identifier"
                                size="small"
                                color="warning"
                                sx={{ mt: 0.5 }}
                              />
                            )}
                          </CardContent>
                        </Card>
                      </Grid>
                    );
                  })}
                  {awsState === 'loaded' && awsManaged.length === 0 && (
                    <Grid item xs={12}>
                      <Typography color="text.secondary" textAlign="center" sx={{ py: 4 }}>
                        No AWS managed transformations available.
                      </Typography>
                    </Grid>
                  )}
                </Grid>
              )}
            </>
          )}
        </Box>
      </Paper>

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editingDef ? 'Edit Transformation' : 'New Transformation'}</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            <TextField
              label="Name"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              fullWidth
              size="small"
            />
            <TextField
              label="Description"
              value={formDescription}
              onChange={(e) => setFormDescription(e.target.value)}
              fullWidth
              multiline
              rows={3}
              size="small"
            />
            <TextField
              label="Type"
              value={formType}
              onChange={(e) => setFormType(e.target.value)}
              fullWidth
              size="small"
              placeholder="custom"
            />
            <TextField
              label="Definition Path"
              value={formPath}
              onChange={(e) => setFormPath(e.target.value)}
              fullWidth
              size="small"
              placeholder="/path/to/definition"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSave} disabled={!formName.trim()}>
            {editingDef ? 'Update' : 'Create'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
