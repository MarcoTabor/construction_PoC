
import type { OrchestratorState } from '../App';

export const TaskPanel = ({ 
  orchestratorState, 
  isThinking,
  onAction 
}: { 
  orchestratorState: OrchestratorState | null,
  isThinking: boolean,
  onAction: (action: string) => void
}) => {
  if (!orchestratorState) return <aside className="task-panel"><div style={{padding: 20}}>Loading agent orchestration...</div></aside>;

  return (
    <aside className="task-panel">
      <div className="task-header">
        <h3 style={{margin: '0 0 5px'}}>Step {orchestratorState.step_id}</h3>
        <span className="task-title">
          {orchestratorState.task_title}
        </span>
      </div>
      
      <div className="task-content">
        <div style={{ padding: '15px' }}>
            <h4 style={{margin: '0 0 10px', fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-light)'}}>Agent Context</h4>
            <p style={{margin: 0, fontSize: '14px', lineHeight: '1.5'}}>
                {orchestratorState.agent_explanation}
            </p>
        </div>
        
        {isThinking && (
           <div style={{ padding: '15px', color: 'var(--accent)', fontWeight: 'bold' }}>
             Agents are thinking...
           </div>
        )}

        {orchestratorState.user_actions_required && orchestratorState.user_actions_required.length > 0 && (
          <div className="qc-group">
            <h4 style={{margin: '0 0 10px', fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-light)'}}>Required Actions</h4>
            {orchestratorState.user_actions_required.map((action, i) => (
              <button 
                key={i} 
                className="btn primary" 
                style={{width: '100%', marginBottom: '10px'}}
                onClick={() => onAction(action)}
                disabled={isThinking}
              >
                {action}
              </button>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
};
