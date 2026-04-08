import { useState } from 'react';
import { TopBar } from './components/TopBar';
import { DocumentPanel } from './components/DocumentPanel';
import { DrawingWorkspace } from './components/DrawingWorkspace';
import { TaskPanel } from './components/TaskPanel';
import { AuditTimeline } from './components/AuditTimeline';
import { AgentLogs } from './components/AgentLogs';
import type { AgentLogEntry } from './components/AgentLogs';
import { UploadScreen } from './components/UploadScreen';

export type AppState = 'upload' | 'processing' | 'workspace';

export interface AuditLog {
  id: string;
  stepIdx: number;
  action: string;
  detail: string;
  user: string;
  time: string;
  level?: string;
}

export interface ScopeObject {
  object_id: string;
  object_type: string;
  coordinates: number[][];
  description: string;
}

export interface OrchestratorState {
  step_id: number;
  task_title: string;
  agent_explanation: string;
  active_canvas_image: string;
  user_actions_required: string[];
  audit_log_entry: string;
  highlight_bbox?: number[];
  highlight_polygon?: number[][];
  registered_objects?: ScopeObject[];
}

function App() {
  const [appState, setAppState] = useState<AppState>('upload');
  const [progress, setProgress] = useState(0);
  const [statusText, setStatusText] = useState('Extracting...');

  const [currentStepId, setCurrentStepId] = useState(1);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [agentLogs, setAgentLogs] = useState<AgentLogEntry[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<File[]>([]);
  
  const [orchestratorState, setOrchestratorState] = useState<OrchestratorState | null>(null);
  const [isAgentThinking, setIsAgentThinking] = useState(false);
  const [bottomTab, setBottomTab] = useState<'audit' | 'logs'>('audit');

  const triggerOverviewAgents = async (files: File[]) => {
    setIsAgentThinking(true);
    setStatusText('Running specialized sub-agents on drawings...');
    try {
      const res = await fetch('http://localhost:8000/api/analyze-drawings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filenames: files.map(f => f.name) })
      });
      const data = await res.json();
      
      // Merge new agent logs
      if(data.logs) {
        setAgentLogs(prev => [...prev, ...data.logs.map((l: { agent: string; task: string; result: string }, i: number) => ({
           id: `al-${Date.now()}-${i}`,
           agentName: l.agent,
           task: l.task,
           result: l.result,
           time: new Date().toLocaleTimeString()
        }))]);
        return data.logs;
      }
    } catch(e) {
      console.error('Agent analysis failed', e);
    }
    return [];
  }

  const fetchNextStep = async (stepId: number, action: string, logsContext?: any[]) => {
    setIsAgentThinking(true);
    try {
      const contextLogs = logsContext || agentLogs;
      const res = await fetch('http://localhost:8000/api/orchestrator/next-step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_step_id: stepId, user_action: action, analysis_context: { sub_agent_summaries: contextLogs } })
      });
      const data = await res.json();
      
      if (data.audit_log_entry) {
        setAuditLogs(prev => [
          ...prev,
          {
            id: 'a' + prev.length,
            stepIdx: stepId,
            action: action,
            detail: data.audit_log_entry,
            user: 'System Agent',
            time: new Date().toLocaleTimeString(),
            level: 'success'
          }
        ]);
      }
      
      if (data.sub_agent_logs) {
          setAgentLogs(prev => [...prev, ...data.sub_agent_logs.map((l: any, i: number) => ({
            id: 'vr-' + Date.now() + '-' + i,
            agentName: l.agent,
            task: l.task,
            result: typeof l.result === 'object' ? JSON.stringify(l.result, null, 2) : l.result,
            time: new Date().toLocaleTimeString()
          }))]);
          setBottomTab('logs');
        }

        setOrchestratorState(data);
      setCurrentStepId(data.step_id);
    } catch(e) {
      console.error('Agent failed', e);
    }
    setIsAgentThinking(false);
  };

  const handleUpload = async (files: File[]) => {
    setUploadedFiles(files);
    setAppState('processing');
    setProgress(15);
    
    if (files.length > 0) {
      setStatusText('Uploading files to backend...');
      const formData = new FormData();
      files.forEach(file => formData.append('files', file));
      try {
        await fetch('http://localhost:8000/api/upload', {
          method: 'POST',
          body: formData
        });
      } catch (e) {
        console.error('File upload failed', e);
      }
    }

    // Run the sub-agents analyzing the document contents first.
    const overviewLogs = await triggerOverviewAgents(files);

    setProgress(100);
    setStatusText('Pipeline Complete. Orchestrator taking over...');

    // Kick off the agent flow
    setTimeout(async () => {
      await fetchNextStep(1, 'Uploaded PDF set', overviewLogs);
      setAppState('workspace');
      setBottomTab('logs'); // Show logs by default to see the cool thought process!
    }, 800);
  };

  const handleTaskAction = (action: string) => {
    fetchNextStep(currentStepId, action);
  };

  if (appState === 'upload') return <UploadScreen onUpload={handleUpload} />;

  if (appState === 'processing') return (
    <div className="app-processing">
      <div className="processing-card">
        <div className="spinner"></div>
        <h2 style={{margin: '0 0 10px', fontSize: '18px'}}>Analyzing Architecture</h2>
        <p style={{color: 'var(--muted)', fontSize: '13px', margin: 0}}>{statusText}</p>
        <div className="progress-bar"><div className="progress-fill" style={{ width: progress + '%' }}></div></div>
      </div>
    </div>
  );

  return (
    <div className="app">
      <TopBar />
      <section className="workspace">
        <DocumentPanel files={uploadedFiles} />
        <DrawingWorkspace orchestratorState={orchestratorState} />
        <TaskPanel 
          orchestratorState={orchestratorState} 
          isThinking={isAgentThinking}
          onAction={handleTaskAction} 
        />
      </section>
      
      {/* Bottom multi-tab panel */}
      <div className="bottom-panel" style={{ display: 'flex', flexDirection: 'column', flex: '0 0 auto', height: '250px', background: 'var(--surface)', borderTop: '1px solid var(--border)' }}>
         <div className="tabs" style={{ display: 'flex', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
            <button 
                onClick={() => setBottomTab('audit')} 
                style={{ padding: '10px 20px', border: 'none', background: bottomTab === 'audit' ? 'var(--bg)' : 'transparent', color: bottomTab === 'audit' ? '#fff' : 'var(--muted)', cursor: 'pointer', fontWeight: bottomTab === 'audit' ? 'bold' : 'normal', outline: 'none' }}
            >
                Audit Trail
            </button>
            <button 
                onClick={() => setBottomTab('logs')} 
                style={{ padding: '10px 20px', border: 'none', background: bottomTab === 'logs' ? 'var(--bg)' : 'transparent', color: bottomTab === 'logs' ? '#fff' : 'var(--muted)', cursor: 'pointer', fontWeight: bottomTab === 'logs' ? 'bold' : 'normal', outline: 'none' }}
            >
                Sub-Agent Logs
            </button>
         </div>
         <div className="tab-content" style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
            {bottomTab === 'audit' && <AuditTimeline logs={auditLogs} currentTaskIndex={currentStepId} />}
            {bottomTab === 'logs' && <AgentLogs logs={agentLogs} />}
         </div>
      </div>
    </div>
  );
}

export default App;


