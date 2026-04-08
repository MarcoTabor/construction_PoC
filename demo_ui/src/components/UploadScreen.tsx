import { useRef, useState } from 'react';
import type { DragEvent } from 'react';

interface UploadScreenProps {
  onUpload: (files: File[]) => void;
}

export function UploadScreen({ onUpload }: UploadScreenProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [stagedFiles, setStagedFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      setStagedFiles(prev => [...prev, ...Array.from(e.dataTransfer.files)]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setStagedFiles(prev => [...prev, ...Array.from(e.target.files!)]);
    }
  };

  const removeFile = (index: number) => {
    setStagedFiles(prev => prev.filter((_, i) => i !== index));
  };

  return (
    <div className="upload-screen">
      <div 
        className={'dropzone ' + (isDragging ? 'active' : '')} 
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
      >
        <div className="icon">📄</div>
        <h2>Upload Drawing Set</h2>
        <p>Drag and drop PDF plans or images here, or click to browse</p>
        <small>Supported formats: .pdf, .png, .ttif</small>
        
        <input 
          type="file" 
          multiple
          ref={fileInputRef}
          style={{ display: 'none' }}
          onChange={handleFileChange}
          accept=".pdf,.png,.tiff,.tif"
        />
      </div>

      {stagedFiles.length > 0 && (
        <div style={{ marginTop: '20px', width: '100%', maxWidth: '500px', textAlign: 'left' }}>
          <h4 style={{ marginBottom: '10px' }}>Staged Files ({stagedFiles.length})</h4>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, maxHeight: '150px', overflowY: 'auto', background: 'var(--surface)', borderRadius: '6px', border: '1px solid var(--border)' }}>
            {stagedFiles.map((file, i) => (
              <li key={i} style={{ padding: '10px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', fontSize: '14px' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{file.name}</span>
                <button onClick={(e) => { e.stopPropagation(); removeFile(i); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--error)' }}>✕</button>
              </li>
            ))}
          </ul>
          <button 
            className="btn" 
            style={{ width: '100%', marginTop: '15px', background: 'var(--blue)', color: 'white' }}
            onClick={() => onUpload(stagedFiles)}
          >
            Process {stagedFiles.length} File{stagedFiles.length > 1 ? 's' : ''}
          </button>
        </div>
      )}

      <div className="demo-action">
        <p>— OR —</p>
        <button className="btn" onClick={() => onUpload([])}>Run Default ST3-P07 Demo</button>
      </div>
    </div>
  );
}

