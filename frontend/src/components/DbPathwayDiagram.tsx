import { Box, Card, Chip, Typography } from '@mui/material';
import {
  ArrowForward as ArrowIcon,
  CloudUpload as DmsIcon,
  FactCheck as ManualIcon,
  HowToReg as ApproveIcon,
  Replay as LoopIcon,
  Search as DiscoverIcon,
  SwapHoriz as ConvertIcon,
  VerifiedUser as VerifyIcon,
} from '@mui/icons-material';
import type { ReactElement } from 'react';

interface Stage {
  key: string;
  title: string;
  icon: ReactElement;
  actor: 'Deterministic' | 'AI agent' | 'Automated checks' | 'Human' | 'Generated artifacts';
  points: string[];
  where: string;
}

const STAGES: Stage[] = [
  {
    key: 'discover',
    title: 'Discover',
    icon: <DiscoverIcon />,
    actor: 'Deterministic',
    points: ['T-SQL objects, columns, MySQL types', 'Incompatibility findings + effort', 'C# embedded SQL, connection strings'],
    where: 'Discovery report',
  },
  {
    key: 'convert',
    title: 'Convert',
    icon: <ConvertIcon />,
    actor: 'AI agent',
    points: ['Bedrock Maker writes MySQL 8.0', 'Grounded in findings and target DDL', 'Up to 3 attempts per object'],
    where: 'Migration → Conversion',
  },
  {
    key: 'verify',
    title: 'Auto-verify',
    icon: <VerifyIcon />,
    actor: 'Automated checks',
    points: ['1. Compiles in MySQL sandbox', '2. Model scores equivalence', '3. Same inputs on SQL Server vs MySQL'],
    where: 'Migration → Conversion',
  },
  {
    key: 'approve',
    title: 'Approve',
    icon: <ApproveIcon />,
    actor: 'Human',
    points: ['Side-by-side review with evidence', 'Approve, edit (re-compiled) or reject', 'Only approved code is exported'],
    where: 'Migration → Review',
  },
  {
    key: 'manual',
    title: 'Manual verify',
    icon: <ManualIcon />,
    actor: 'Human',
    points: ['Row counts + checksums per table', 'Procedure result-set comparison', 'Run on demand against real data'],
    where: 'Migration → Validation',
  },
  {
    key: 'dms',
    title: 'DMS',
    icon: <DmsIcon />,
    actor: 'Generated artifacts',
    points: ['Table mappings + task settings', 'CloudFormation: full load + CDC', 'Prerequisites from discovery'],
    where: 'Migration → DMS',
  },
];

const ACTOR_COLOR: Record<Stage['actor'], string> = {
  Deterministic: '#2E7D32',
  'AI agent': '#7B1FA2',
  'Automated checks': '#1565C0',
  Human: '#E65100',
  'Generated artifacts': '#37474F',
};

function StageCard({ stage, n }: { stage: Stage; n: number }) {
  const color = ACTOR_COLOR[stage.actor];
  return (
    <Card
      variant="outlined"
      data-testid={`stage-${stage.key}`}
      sx={{ flex: '1 1 0', minWidth: 160, p: 1.5, borderTop: `4px solid ${color}`, display: 'flex', flexDirection: 'column', gap: 0.75 }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box
          sx={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            bgcolor: color,
            color: 'white',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {stage.icon}
        </Box>
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1 }}>
            Step {n}
          </Typography>
          <Typography variant="subtitle1" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
            {stage.title}
          </Typography>
        </Box>
      </Box>
      <Chip size="small" label={stage.actor} sx={{ alignSelf: 'flex-start', bgcolor: `${color}14`, color, fontWeight: 600 }} />
      <Box component="ul" sx={{ m: 0, pl: 2, flex: 1 }}>
        {stage.points.map((p) => (
          <Typography key={p} component="li" variant="caption" sx={{ display: 'list-item', lineHeight: 1.45 }}>
            {p}
          </Typography>
        ))}
      </Box>
      <Typography variant="caption" color="text.secondary" sx={{ fontStyle: 'italic' }}>
        {stage.where}
      </Typography>
    </Card>
  );
}

/** The SQL Server → Aurora MySQL pathway: what each step does, who does it, and where it lives. */
export function DbPathwayDiagram() {
  return (
    <Box component="figure" aria-label="SQL Server to Aurora MySQL pathway" sx={{ m: 0, mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'stretch', gap: 0.5, flexWrap: { xs: 'wrap', lg: 'nowrap' } }}>
        {STAGES.map((stage, k) => (
          <Box key={stage.key} sx={{ display: 'contents' }}>
            <StageCard stage={stage} n={k + 1} />
            {k < STAGES.length - 1 && (
              <Box sx={{ display: { xs: 'none', lg: 'flex' }, alignItems: 'center', color: 'text.disabled' }}>
                <ArrowIcon />
              </Box>
            )}
          </Box>
        ))}
      </Box>

      {/* Feedback loop: auto-verify failures go back to the Maker (spans the Convert and Auto-verify cards). */}
      <Box
        data-testid="feedback-loop"
        sx={{
          display: { xs: 'none', lg: 'flex' },
          mx: 'calc(100% / 6 * 1 + 8px)',
          width: 'calc(100% / 6 * 2 - 24px)',
          mt: 0.5,
          borderBottom: '2px dashed',
          borderLeft: '2px dashed',
          borderRight: '2px dashed',
          borderColor: ACTOR_COLOR['Automated checks'],
          borderRadius: '0 0 12px 12px',
          height: 18,
          justifyContent: 'center',
        }}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, justifyContent: { xs: 'flex-start', lg: 'center' }, mt: 0.5 }}>
        <LoopIcon fontSize="small" sx={{ color: ACTOR_COLOR['Automated checks'] }} />
        <Typography variant="caption" color="text.secondary">
          Any failed check goes back to the Maker with the MySQL error, the reviewer&apos;s issues, or what each engine
          actually returned. Only conversions that pass all checks reach a human, unless attempts run out.
        </Typography>
      </Box>
    </Box>
  );
}
