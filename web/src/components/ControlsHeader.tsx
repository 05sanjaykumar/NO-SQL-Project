import React from 'react';

interface ControlsHeaderProps {
  status: string;
  scenario: string;
  speed: number;
  oracleView: boolean;
  hubWeightEnabled: boolean;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onSpeedChange: (speed: number) => void;
  onScenarioChange: (scenario: string) => void;
  onToggleOracle: () => void;
  onToggleHubWeight: () => void;
}

export const ControlsHeader: React.FC<ControlsHeaderProps> = ({
  status,
  scenario,
  speed,
  oracleView,
  hubWeightEnabled,
  onStart,
  onPause,
  onResume,
  onSpeedChange,
  onScenarioChange,
  onToggleOracle,
  onToggleHubWeight,
}) => {
  return (
    <header className="app-header">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <div
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              background: status === 'running' ? '#10b981' : '#f59e0b',
              boxShadow: status === 'running' ? '0 0 10px #10b981' : 'none',
            }}
          />
          <h1 style={{ fontSize: 16, fontWeight: 700, letterSpacing: '-0.02em' }}>
            FraudSim <span style={{ color: 'var(--accent-cyan)', fontWeight: 400 }}>| Hub-Aware Streaming Engine</span>
          </h1>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* Scenario selector */}
        <select
          value={scenario}
          onChange={(e) => onScenarioChange(e.target.value)}
          style={{
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-main)',
            padding: '6px 12px',
            borderRadius: 6,
            fontSize: 13,
            outline: 'none',
          }}
        >
          <option value="baseline">baseline</option>
          <option value="hub_heavy">hub_heavy</option>
          <option value="adversarial">adversarial</option>
          <option value="high_noise">high_noise</option>
          <option value="scale_sweep">scale_sweep</option>
          <option value="drift">drift</option>
        </select>

        {/* Play/Pause */}
        {status === 'running' ? (
          <button className="btn" onClick={onPause}>
            ⏸ Pause
          </button>
        ) : status === 'paused' ? (
          <button className="btn btn-primary" onClick={onResume}>
            ▶ Resume
          </button>
        ) : (
          <button className="btn btn-primary" onClick={onStart}>
            ▶ Start Run
          </button>
        )}

        {/* Speed multiplier */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-muted)' }}>
          <span>Speed: {speed}x</span>
          <input
            type="range"
            min="0.5"
            max="5"
            step="0.5"
            value={speed}
            onChange={(e) => onSpeedChange(parseFloat(e.target.value))}
            style={{ width: 80, accentColor: 'var(--accent-cyan)' }}
          />
        </div>

        {/* Hub-weight toggle (C1) */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={hubWeightEnabled}
            onChange={onToggleHubWeight}
            style={{ accentColor: 'var(--accent-cyan)' }}
          />
          Hub-Weight (C1)
        </label>

        {/* Oracle View toggle */}
        <button
          className={`btn ${oracleView ? 'btn-danger' : ''}`}
          onClick={onToggleOracle}
          style={{ fontSize: 12 }}
        >
          👁 Oracle View {oracleView ? '(ON)' : '(OFF)'}
        </button>
      </div>
    </header>
  );
};
