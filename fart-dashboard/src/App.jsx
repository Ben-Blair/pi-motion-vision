import { useState } from 'react';
import Layout from './components/Layout';
import EventLog from './components/EventLog';
import SettingsForm from './components/SettingsForm';
import './App.css';

export default function App() {
  const [view, setView] = useState('log');

  return (
    <Layout currentView={view} onNavigate={setView}>
      {view === 'log' && <EventLog />}
      {view === 'settings' && <SettingsForm />}
    </Layout>
  );
}
