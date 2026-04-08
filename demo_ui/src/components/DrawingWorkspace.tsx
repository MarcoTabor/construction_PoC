import { useState, useEffect } from 'react';
import type { OrchestratorState } from '../App';

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
                  <polygon
                    points={orchestratorState.highlight_polygon.map(p => `${p[1]},${p[0]}`).join(' ')}
                    fill="rgba(255, 51, 102, 0.15)"
                    stroke="#ff3366"
                    strokeWidth="4"
                    strokeDasharray="10,5"
                    style={{ filter: 'drop-shadow(0 0 10px rgba(255, 51, 102, 0.5))' }}
                  >
                    <title>Proposed Target Area</title>
                  </polygon>
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
          </div>
        )}
      </div>
    </main>
  );
};

