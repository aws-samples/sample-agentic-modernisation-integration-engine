import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { DbPathwayDiagram } from './DbPathwayDiagram';

describe('DbPathwayDiagram', () => {
  it('shows the six steps in order with who performs each', () => {
    render(<DbPathwayDiagram />);
    const keys = ['discover', 'convert', 'verify', 'approve', 'manual', 'dms'];
    const titles = keys.map((k) => within(screen.getByTestId(`stage-${k}`)).getByText(/^(Discover|Convert|Auto-verify|Approve|Manual verify|DMS)$/).textContent);
    expect(titles).toEqual(['Discover', 'Convert', 'Auto-verify', 'Approve', 'Manual verify', 'DMS']);
    expect(within(screen.getByTestId('stage-discover')).getByText('Deterministic')).toBeInTheDocument();
    expect(within(screen.getByTestId('stage-convert')).getByText('AI agent')).toBeInTheDocument();
    expect(within(screen.getByTestId('stage-approve')).getByText('Human')).toBeInTheDocument();
    expect(screen.getByText(/goes back to the Maker/)).toBeInTheDocument();
  });
});
