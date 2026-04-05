import { useState } from 'react';

export default function Layout({ children, currentView, onNavigate }) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="layout">
      <header className="header">
        <div className="header-inner">
          <h1 className="header-title" onClick={() => onNavigate('log')}>
            Fart Detector
          </h1>
          <button
            className="menu-toggle"
            onClick={() => setMenuOpen(!menuOpen)}
            aria-label="Toggle menu"
          >
            &#9776;
          </button>
          <nav className={`nav ${menuOpen ? 'nav--open' : ''}`}>
            <button
              className={`nav-link ${currentView === 'log' ? 'nav-link--active' : ''}`}
              onClick={() => { onNavigate('log'); setMenuOpen(false); }}
            >
              Event Log
            </button>
            <button
              className={`nav-link ${currentView === 'settings' ? 'nav-link--active' : ''}`}
              onClick={() => { onNavigate('settings'); setMenuOpen(false); }}
            >
              Settings
            </button>
          </nav>
        </div>
      </header>
      <main className="main">{children}</main>
    </div>
  );
}
