interface DocumentPanelProps {
  files?: File[];
}

export function DocumentPanel({ files = [] }: DocumentPanelProps) {
  // Use demo files if no files were actually uploaded
  const hasFiles = files && files.length > 0;

  return (
    <aside className="panel documents">
      <div className="panel-header">
        <div>
          <h2>Documents</h2>
          <p>Detected and classified drawing set for the active scope.</p>
        </div>
      </div>
      <div className="doc-list">
        
        {hasFiles ? (
          files.map((f, i) => {
            // Rough heuristic to color code based on names
            const nameLower = f.name.toLowerCase();
            let docType = 'Plan';
            let style = {};
            if (nameLower.includes('section') || nameLower.includes('sec')) {
              docType = 'Section';
              style = { background: 'var(--purple-soft)', color: 'var(--purple)' };
            } else if (nameLower.includes('longitudinal') || nameLower.includes('profile')) {
              docType = 'Longitudinal';
              style = { background: 'var(--amber-soft)', color: 'var(--amber)' };
            }

            return (
              <article className={'doc-card ' + (i === 0 ? 'active' : '')} key={i}>
                <div className="doc-thumb">
                  <div className="doc-grid"></div>
                </div>
                <div style={{ overflow: 'hidden' }}>
                  <div className="doc-type" style={style}>{docType}</div>
                  <h3 style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={f.name}>{f.name}</h3>
                  <p>User uploaded file. System automatically classified document type.</p>
                </div>
              </article>
            );
          })
        ) : (
          <>
            <article className="doc-card active">
              <div className="doc-thumb">
                <div className="doc-grid"></div>
              </div>
              <div>
                <div className="doc-type">Plan</div>
                <h3>ST3-P07 &middot; Stage 3 Plan</h3>
                <p>Visible scoped region, Joal 502 carriageway, footpath geometry, legend-linked plan features.</p>
              </div>
            </article>

            <article className="doc-card">
              <div className="doc-thumb">
                <div className="doc-grid"></div>
              </div>
              <div>
                <div className="doc-type" style={{ background: 'var(--amber-soft)', color: 'var(--amber)' }}>Longitudinal</div>
                <h3>ST3-L06 &middot; Profile</h3>
                <p>Vertical profile used for slope assessment and method selection.</p>
              </div>
            </article>

            <article className="doc-card">
              <div className="doc-thumb">
                <div className="doc-grid"></div>
              </div>
              <div>
                <div className="doc-type" style={{ background: 'var(--purple-soft)', color: 'var(--purple)' }}>Section</div>
                <h3>DE04 &middot; Typical Section</h3>
                <p>Concrete layer, GAP65 width and depth, flush nib, footpath and subsoil drain interpretation.</p>
              </div>
            </article>
          </>
        )}
      </div>
    </aside>
  );
}

