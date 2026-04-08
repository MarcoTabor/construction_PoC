

export interface AgentLogEntry {
  id: string;
  agentName: string;
  task: string;
  result: string;
  time: string;
}

export const AgentLogs = ({ logs }: { logs: AgentLogEntry[] }) => {
  return (
    <div className="agent-logs" style={{ padding: '20px', background: 'var(--surface)', borderTop: '1px solid var(--border)', flex: '0 0 250px', overflowY: 'auto' }}>
      <h3 style={{ margin: '0 0 15px', fontSize: '14px', textTransform: 'uppercase', color: 'var(--text-light)' }}>Agent Interaction Logs</h3>
      {logs.length === 0 ? (
        <div style={{ color: 'var(--muted)', fontSize: '13px' }}>Waiting for sub-agent activity...</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {logs.map(log => (
            <div key={log.id} style={{ background: '#1e1e24', padding: '10px', borderRadius: '4px', borderLeft: '3px solid var(--accent)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '5px' }}>
                <strong style={{ fontSize: '13px', color: '#fff' }}>{log.agentName}</strong>
                <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{log.time}</span>
              </div>
              <div style={{ fontSize: '12px', color: '#ddd', marginBottom: '5px' }}><strong>Task:</strong> {log.task}</div>
              <div style={{ fontSize: '12px', color: '#9cdcfe', whiteSpace: 'pre-wrap' }}><strong>Result:</strong> {log.result}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
