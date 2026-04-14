import { useState, useEffect, useRef } from 'react';
import type { OrchestratorState } from '../App';

const InteractivePolygon = ({ points }: { points: number[][] }) => {
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1, rotation: 0 });
  const [interaction, setInteraction] = useState<{ mode: 'none' | 'move' | 'scale' | 'rotate', startX: number, startY: number, startTransform: any }>({ mode: 'none', startX: 0, startY: 0, startTransform: null });
  const svgRef = useRef<SVGGElement>(null);

  // Calculate polygon bounding box for handles
  const xs = points.map(p => p[1]);
  const ys = points.map(p => p[0]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const width = maxX - minX;
  const height = maxY - minY;

  const handlePointerDown = (e: React.PointerEvent, mode: 'move' | 'scale' | 'rotate') => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setInteraction({
      mode,
      startX: e.clientX,
      startY: e.clientY,
      startTransform: { ...transform }
    });
  };

  const handlePointerMove = (e: React.PointerEvent) => {
    if (interaction.mode === 'none') return;
    
    // Simplistic delta mapping (assumes 1000x1000 viewBox roughly maps to screen pixels for MVP)
    // In a real app we'd map screen pixels back to the SVG coordinate space.
    const dx = (e.clientX - interaction.startX);
    const dy = (e.clientY - interaction.startY);

    if (interaction.mode === 'move') {
      setTransform({
        ...interaction.startTransform,
        x: interaction.startTransform.x + dx,
        y: interaction.startTransform.y + dy
      });
    } else if (interaction.mode === 'scale') {
      // Very basic scaling based on downward drag = larger
      const scaleDelta = (dx + dy) / 200; 
      setTransform({
        ...interaction.startTransform,
        scale: Math.max(0.1, interaction.startTransform.scale + scaleDelta)
      });
    } else if (interaction.mode === 'rotate') {
      // Basic rotation based on horizontal drag
      const rotDelta = dx / 2;
      setTransform({
        ...interaction.startTransform,
        rotation: interaction.startTransform.rotation + rotDelta
      });
    }
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    e.currentTarget.releasePointerCapture(e.pointerId);
    setInteraction({ mode: 'none', startX: 0, startY: 0, startTransform: null });
  };

  const pointsStr = points.map(p => `${p[1]},${p[0]}`).join(' ');

  return (
    <g 
      ref={svgRef}
      transform={`translate(${transform.x}, ${transform.y}) translate(${cx}, ${cy}) rotate(${transform.rotation}) scale(${transform.scale}) translate(${-cx}, ${-cy})`}
      style={{ pointerEvents: 'all', cursor: interaction.mode === 'move' ? 'grabbing' : 'grab' }}
      onPointerDown={(e) => handlePointerDown(e, 'move')}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      <polygon
        points={pointsStr}
        fill="rgba(255, 51, 102, 0.15)"
        stroke="#ff3366"
        strokeWidth="4"
        strokeDasharray="10,5"
        style={{ filter: 'drop-shadow(0 0 10px rgba(255, 51, 102, 0.5))' }}
      >
        <title>Proposed Target Area (Drag to move)</title>
      </polygon>

      {/* Rotation Handle (Top) */}
      <circle 
        cx={cx} 
        cy={minY - 40} 
        r={15} 
        fill="#ff3366" 
        cursor="ew-resize"
        onPointerDown={(e) => handlePointerDown(e, 'rotate')}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      />
      <line x1={cx} y1={minY} x2={cx} y2={minY - 25} stroke="#ff3366" strokeWidth="3" />

      {/* Scaling Handle (Bottom Right) */}
      <rect 
        x={maxX - 15} 
        y={maxY - 15} 
        width={30} 
        height={30} 
        fill="#ff3366" 
        cursor="nwse-resize"
        onPointerDown={(e) => handlePointerDown(e, 'scale')}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      />
    </g>
  );
};

export const DrawingWorkspace = ({ orchestratorState }: { orchestratorState: OrchestratorState | null }) => {
  const [imageError, setImageError] = useState(false);

  useEffect(() => {
    setImageError(false);
  }, [orchestratorState?.active_canvas_image]);

  return (
    <main className="drawing-workspace">
      <div className="toolbar">
        <button className="tool-btn active">Pan</button>
        <button className="tool-btn">Select</button>
        <button className="tool-btn" style={{ marginLeft: 'auto' }}>Zoom Fit</button>
      </div>

      <div className="canvas-area">
        {!orchestratorState ? (
          <div className="empty-state">Waiting for orchestrator...</div>
        ) : (
          <div style={{ position: 'relative', display: 'flex', width: '100%', height: '100%', justifyContent: 'center', alignItems: 'center', overflow: 'hidden' }}>
            <div style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', maxHeight: '100%' }}>
              {!imageError ? (
                <img
                  src={`http://localhost:8000${orchestratorState.active_canvas_image}`}
                  alt="Workspace Content"
                  style={{
                    maxWidth: '100%',
                    maxHeight: '100%',
                    display: 'block',
                    objectFit: 'contain'
                  }}
                  onError={() => setImageError(true)}
                />
              ) : (
                <div className="empty-state">No visual context available for this step</div>
              )}
              
              <svg 
                viewBox="0 0 1000 1000" 
                preserveAspectRatio="none" 
                style={{ 
                  position: 'absolute', 
                  top: 0, 
                  left: 0, 
                  width: '100%', 
                  height: '100%', 
                  pointerEvents: 'none',
                  overflow: 'visible'
                }}
              >
                {orchestratorState.highlight_polygon && !imageError && (
                  <InteractivePolygon points={orchestratorState.highlight_polygon} />
                )}

                {orchestratorState.registered_objects && !imageError && orchestratorState.registered_objects.map(obj => {
                  if (!obj.coordinates || obj.coordinates.length === 0) return null;
                  const ys = obj.coordinates.map(p => p[0]);
                  const xs = obj.coordinates.map(p => p[1]);
                  const cx = xs.reduce((a, b) => a + b, 0) / xs.length;
                  const cy = ys.reduce((a, b) => a + b, 0) / ys.length;

                  return (
                    <g key={obj.object_id}>
                      <polygon
                        points={obj.coordinates.map(p => `${p[1]},${p[0]}`).join(' ')}
                        fill="rgba(0, 240, 255, 0.2)"
                        stroke="#00f0ff"
                        strokeWidth="4"
                        style={{ filter: 'drop-shadow(0 0 10px rgba(0, 240, 255, 0.5))' }}
                      >
                        <title>{`${obj.object_id} - ${obj.object_type}: ${obj.description}`}</title>
                      </polygon>
                      <text 
                        x={cx} 
                        y={cy} 
                        fill="#00f0ff" 
                        fontSize="28" 
                        fontWeight="bold" 
                        textAnchor="middle" 
                        dominantBaseline="middle"
                        style={{ textShadow: '2px 2px 4px #000, -2px -2px 4px #000' }}
                      >
                        {obj.object_id}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>

            {orchestratorState.math_derivations && orchestratorState.math_derivations.length > 0 && (
              <div 
                style={{
                  position: 'absolute',
                  top: '10%',
                  bottom: '10%',
                  left: '15%',
                  right: '15%',
                  background: 'rgba(15, 23, 42, 0.95)',
                  backdropFilter: 'blur(10px)',
                  border: '1px solid rgba(0, 240, 255, 0.3)',
                  borderRadius: '12px',
                  padding: '40px',
                  boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5), 0 0 30px rgba(0, 240, 255, 0.1)',
                  color: 'white',
                  overflowY: 'auto',
                  zIndex: 10,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '24px'
                }}
              >
                <div style={{ textAlign: 'center', marginBottom: '20px' }}>
                  <h2 style={{ fontSize: '28px', margin: '0 0 10px 0', color: '#00f0ff', letterSpacing: '0.05em' }}>Engineering Logic & Final Schedule of Quantities</h2>
                  <p style={{ color: '#94a3b8', margin: 0, fontSize: '15px' }}>Mathematical Derivations & Causality Chain</p>
                </div>
                
                {orchestratorState.math_derivations.map((math, idx) => (
                  <div key={idx} style={{ 
                    background: 'rgba(255, 255, 255, 0.03)', 
                    borderLeft: '4px solid #00f0ff', 
                    padding: '20px', 
                    borderRadius: '0 8px 8px 0',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '12px'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <h3 style={{ margin: 0, fontSize: '18px', color: '#e2e8f0' }}>{math.item}</h3>
                      <span style={{ fontSize: '20px', fontWeight: 'bold', color: '#10b981', background: 'rgba(16, 185, 129, 0.1)', padding: '4px 12px', borderRadius: '4px' }}>
                        {math.result}
                      </span>
                    </div>
                    
                    <div style={{ padding: '12px', background: 'rgba(0,0,0,0.3)', borderRadius: '6px', fontFamily: 'monospace', fontSize: '15px', color: '#f8fafc', whiteSpace: 'pre-wrap', lineHeight: '1.5', border: '1px dashed rgba(255,255,255,0.1)' }}>
                      <span style={{ color: '#00f0ff' }}>ƒ(x) = </span> {math.formula}
                    </div>
                    
                    <div style={{ fontSize: '13px', color: '#94a3b8', fontStyle: 'italic', display: 'flex', alignItems: 'flex-start', gap: '6px' }}>
                      <span style={{ opacity: 0.7 }}>↳ Reason:</span> <span>{math.reasoning}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
};

