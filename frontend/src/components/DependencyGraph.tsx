import { useEffect, useRef, useCallback } from 'react';
import { Box, Typography } from '@mui/material';
import * as d3 from 'd3';
import type { GraphNode, GraphEdge } from '../types';

// Ecosystem color mapping
const ECOSYSTEM_COLORS: Record<string, string> = {
  npm: '#CB3837',
  maven: '#C71A36',
  pypi: '#3775A9',
  nuget: '#004880',
  cargo: '#DEA584',
  go: '#00ADD8',
  rubygems: '#CC342D',
  packagist: '#F28D1A',
  default: '#6C757D',
};

function getEcosystemColor(type: string): string {
  const lower = type?.toLowerCase() ?? '';
  return ECOSYSTEM_COLORS[lower] ?? ECOSYSTEM_COLORS.default;
}

interface DependencyGraphProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onNodeClick?: (node: GraphNode) => void;
  width?: number;
  height?: number;
}

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  type: string;
  metadata?: Record<string, unknown>;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  type: string;
}

export function DependencyGraph({
  nodes,
  edges,
  onNodeClick,
  height = 500,
}: DependencyGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleNodeClick = useCallback(
    (node: SimNode) => {
      if (onNodeClick) {
        onNodeClick({
          id: node.id,
          label: node.label,
          type: node.type,
          metadata: node.metadata,
        });
      }
    },
    [onNodeClick]
  );

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;
    if (!Array.isArray(nodes) || nodes.length === 0) return;

    const container = containerRef.current;
    const containerWidth = container.clientWidth || 800;

    // Clear previous content
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3
      .select(svgRef.current)
      .attr('width', containerWidth)
      .attr('height', height)
      .attr('viewBox', `0 0 ${containerWidth} ${height}`);

    // Create zoom behavior
    const g = svg.append('g');

    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        g.attr('transform', event.transform.toString());
      });

    svg.call(zoom);

    // Prepare simulation data (deep copy to avoid mutating props)
    const simNodes: SimNode[] = nodes.map((n) => ({
      id: n.id,
      label: n.label,
      type: n.type,
      metadata: n.metadata,
    }));

    const simLinks: SimLink[] = (edges ?? [])
      .filter(
        (e) =>
          simNodes.some((n) => n.id === e.source) &&
          simNodes.some((n) => n.id === e.target)
      )
      .map((e) => ({
        source: e.source,
        target: e.target,
        type: e.type,
      }));

    // Create force simulation
    const simulation = d3
      .forceSimulation<SimNode>(simNodes)
      .force(
        'link',
        d3
          .forceLink<SimNode, SimLink>(simLinks)
          .id((d) => d.id)
          .distance(80)
      )
      .force('charge', d3.forceManyBody().strength(-200))
      .force('center', d3.forceCenter(containerWidth / 2, height / 2))
      .force('collision', d3.forceCollide(30));

    // Draw links
    const link = g
      .append('g')
      .selectAll('line')
      .data(simLinks)
      .join('line')
      .attr('stroke', '#999')
      .attr('stroke-opacity', 0.6)
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#arrowhead)');

    // Arrowhead marker
    svg
      .append('defs')
      .append('marker')
      .attr('id', 'arrowhead')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#999');

    // Draw nodes
    const node = g
      .append('g')
      .selectAll<SVGGElement, SimNode>('g')
      .data(simNodes)
      .join('g')
      .style('cursor', 'pointer')
      .call(
        d3
          .drag<SVGGElement, SimNode>()
          .on('start', (event: d3.D3DragEvent<SVGGElement, SimNode, SimNode>) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            event.subject.fx = event.subject.x;
            event.subject.fy = event.subject.y;
          })
          .on('drag', (event: d3.D3DragEvent<SVGGElement, SimNode, SimNode>) => {
            event.subject.fx = event.x;
            event.subject.fy = event.y;
          })
          .on('end', (event: d3.D3DragEvent<SVGGElement, SimNode, SimNode>) => {
            if (!event.active) simulation.alphaTarget(0);
            event.subject.fx = null;
            event.subject.fy = null;
          })
      );

    node
      .append('circle')
      .attr('r', 12)
      .attr('fill', (d) => getEcosystemColor(d.type))
      .attr('stroke', '#fff')
      .attr('stroke-width', 2);

    node
      .append('text')
      .text((d) => d.label)
      .attr('x', 16)
      .attr('y', 4)
      .attr('font-size', '11px')
      .attr('fill', '#333');

    node.on('click', (_event: MouseEvent, d: SimNode) => {
      handleNodeClick(d);
    });

    // Tooltip on hover
    node.append('title').text((d) => `${d.label} (${d.type})`);

    // Simulation tick
    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (d.source as SimNode).x ?? 0)
        .attr('y1', (d) => (d.source as SimNode).y ?? 0)
        .attr('x2', (d) => (d.target as SimNode).x ?? 0)
        .attr('y2', (d) => (d.target as SimNode).y ?? 0);

      node.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    return () => {
      simulation.stop();
    };
  }, [nodes, edges, height, handleNodeClick]);

  if (!Array.isArray(nodes) || nodes.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ p: 2 }}>
        No dependency graph data available.
      </Typography>
    );
  }

  return (
    <Box ref={containerRef} sx={{ width: '100%', height, border: '1px solid #e0e0e0', borderRadius: 1, overflow: 'hidden' }}>
      <svg ref={svgRef} style={{ width: '100%', height: '100%' }} />
    </Box>
  );
}
