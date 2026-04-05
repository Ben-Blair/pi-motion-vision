import { useState, useEffect } from 'react';
import { fetchEvents } from '../api/client';
import EventCard from './EventCard';
import VideoPlayer from './VideoPlayer';

export default function EventLog() {
  const [events, setEvents] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedEvent, setSelectedEvent] = useState(null);

  const perPage = 12;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchEvents(page, perPage)
      .then((data) => {
        if (cancelled) return;
        setEvents(data.events);
        setTotal(data.total);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [page]);

  const totalPages = Math.ceil(total / perPage);

  if (loading) return <p className="status-msg">Loading events...</p>;
  if (error) return <p className="status-msg status-msg--error">Error: {error}</p>;
  if (events.length === 0) return <p className="status-msg">No fart events detected yet.</p>;

  return (
    <div className="event-log">
      <h2 className="event-log__title">Fart Detection Log ({total} events)</h2>
      <div className="event-grid">
        {events.map((ev) => (
          <EventCard key={ev.id} event={ev} onClick={setSelectedEvent} />
        ))}
      </div>
      {totalPages > 1 && (
        <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </button>
          <span>Page {page} of {totalPages}</span>
          <button disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
            Next
          </button>
        </div>
      )}
      {selectedEvent && (
        <VideoPlayer event={selectedEvent} onClose={() => setSelectedEvent(null)} />
      )}
    </div>
  );
}
