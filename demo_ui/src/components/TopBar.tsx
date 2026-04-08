import React from 'react';

export const TopBar: React.FC = () => {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-badge">TG</div>
        <div>
          <h1>Task-Driven Geometry Workspace</h1>
          <p>Joal 502 &middot; Stage 3 demo &middot; Trust, traceability, engineering logic</p>
        </div>
      </div>
      <div className="topbar-meta">
        <div className="pill"><span className="dot"></span> Analysis complete</div>
        <div className="pill">3 sheets linked</div>
        <div className="pill">5 quantity tasks ready</div>
      </div>
    </header>
  );
};
