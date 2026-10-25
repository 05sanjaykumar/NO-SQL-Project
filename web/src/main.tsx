import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

/** Keeps a rendering error from blanking the whole dashboard during a demo. */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: string | null }> {
  state = { error: null as string | null };

  static getDerivedStateFromError(e: unknown) {
    return { error: String(e) };
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24 }}>
          <h2>The dashboard hit an error</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{this.state.error}</pre>
          <button className="btn primary" onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
