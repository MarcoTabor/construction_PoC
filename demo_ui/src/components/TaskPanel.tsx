
import { useState, useEffect } from 'react';
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
  const [selectedActions, setSelectedActions] = useState<Set<string>>(new Set());

  // Initialize selected actions when moving to a new step
  useEffect(() => {
    if (orchestratorState?.user_actions_required) {
      setSelectedActions(new Set(orchestratorState.user_actions_required));
    } else {
      setSelectedActions(new Set());
    }
  }, [orchestratorState?.step_id, orchestratorState?.user_actions_required]);

  if (!orchestratorState) return <aside className="task-panel"><div style={{padding: 20}}>Loading agent orchestration...</div></aside>;

  const toggleAction = (action: string) => {
    const newSelected = new Set(selectedActions);
    if (newSelected.has(action)) {
      newSelected.delete(action);
    } else {
      newSelected.add(action);
    }
    setSelectedActions(newSelected);
  };

  const handleSubmit = () => {
    if (!orchestratorState.user_actions_required || orchestratorState.user_actions_required.length === 0) return;
    
    if (orchestratorState.user_actions_required.length === 1) {
      onAction(orchestratorState.user_actions_required[0]);
    } else {
      const confirmed = Array.from(selectedActions);
      const unconfirmed = orchestratorState.user_actions_required.filter(a => !selectedActions.has(a));
      
      const actionStrings = [];
      if (confirmed.length) actionStrings.push(`Confirmed: ${confirmed.join(', ')}`);
      if (unconfirmed.length) actionStrings.push(`Not confirmed: ${unconfirmed.join(', ')}`);
      
      onAction(actionStrings.join('. ') || "Skipped all actions");
    }
  };

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
            <div style={{margin: 0, fontSize: '14px', lineHeight: '1.6'}}>
                {orchestratorState.agent_explanation.split(/(?:\r?\n|\\n)/).map((line, index) => {
                  // Basic markdown bold parsing
                  const parts = line.split(/(\*\*.*?\*\*)/g);
                  return (
                    <div key={index} style={{ marginBottom: line.trim() === '' ? '10px' : '4px' }}>
                      {parts.map((part, i) => {
                        if (part.startsWith('**') && part.endsWith('**')) {
                          return <strong key={i} style={{color: 'var(--text)'}}>{part.slice(2, -2)}</strong>;
                        }
                        return part;
                      })}
                    </div>
                  );
                })}
            </div>
        </div>
        
        {isThinking && (
           <div style={{ padding: '15px', color: 'var(--accent)', fontWeight: 'bold' }}>
             Agents are thinking...
           </div>
        )}

        {orchestratorState.user_actions_required && orchestratorState.user_actions_required.length > 0 && (
          <div className="qc-group">
            <h4 style={{margin: '0 0 10px', fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-light)'}}>Required Actions</h4>
            {orchestratorState.user_actions_required.length === 1 ? (
              <button
                className="btn primary"
                style={{width: '100%', marginBottom: '10px'}}
                onClick={() => onAction(orchestratorState.user_actions_required[0])}
                disabled={isThinking}
              >
                {orchestratorState.user_actions_required[0]}
              </button>
            ) : (
              <>
                {orchestratorState.user_actions_required.map((action, i) => {
                  const isSelected = selectedActions.has(action);
                  return (
                    <button
                      key={i}
                      className={`btn ${isSelected ? 'primary' : 'secondary'}`}
                      style={{
                        width: '100%', 
                        marginBottom: '10px', 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'center',
                        gap: '8px',
                        opacity: isSelected ? 1 : 0.6
                      }}
                      onClick={() => toggleAction(action)}
                      disabled={isThinking}
                    >
                      <input 
                        type="checkbox" 
                        checked={isSelected}
                        readOnly
                        style={{ margin: 0, cursor: 'pointer' }}
                      />
                      <span style={{flex: 1, textAlign: 'left'}}>{action.replace(/^✅\s*/, '')}</span>
                    </button>
                  );
                })}
                <button
                  className="btn primary"
                  style={{width: '100%', marginTop: '10px', fontWeight: 'bold'}}
                  onClick={handleSubmit}
                  disabled={isThinking}
                >
                  Submit Confirmed Actions
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  );
};
