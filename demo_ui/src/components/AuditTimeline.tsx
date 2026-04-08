import { useRef, useEffect } from 'react';
import type { AuditLog } from '../App';

interface AuditTimelineProps {
  logs: AuditLog[];
  currentTaskIndex: number;
}

export function AuditTimeline({ logs, currentTaskIndex }: AuditTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs]);

  return (
    <div className="panel bottom-drawer">
      <div className="drawer-handle"></div>
      
      <div className="audit-wrap">
        <div className="audit-sidebar">
           <h4>System Audit Trail</h4>
           <div className="metric-card">
             <div className="metric-n">{logs.filter((l: any) => l.level === 'warn').length}</div>
             <small>Confidence Alerts</small>
           </div>
           {currentTaskIndex >= 2 && (
             <div className="metric-card success">
               <div className="metric-n">0</div>
               <small>Missing Information</small>
             </div>
           )}
        </div>

        <div className="audit-content" ref={scrollRef}>
          {logs.map((log: any) => (
            <div key={log.id} className={'log-entry ' + (log.level || 'sys')}>
              <span className="timestamp">{log.time || log.timestamp}</span>
              <span className="message">
                {(!log.level || log.level === 'sys') && '⚙️ '}
                {log.level === 'warn' && '⚠️ '}
                {log.level === 'success' && '✅ '}
                {log.action}
              </span>
            </div>
          ))}
          {logs.length === 0 && (
             <div className="log-entry sys">
              <span className="timestamp" style={{visibility: 'hidden'}}>[00:00:00]</span>
              <span className="message">Awaiting first task execution...</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

