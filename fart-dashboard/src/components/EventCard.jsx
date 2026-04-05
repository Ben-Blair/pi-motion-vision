import { thumbnailUrl } from '../api/client';

export default function EventCard({ event, onClick }) {
  const thumb = thumbnailUrl(event.thumbnail);
  const date = new Date(event.timestamp);
  const formattedDate = date.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  });
  const formattedTime = date.toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const confidence = event.confidence != null
    ? `${(event.confidence * 100).toFixed(1)}%`
    : 'N/A';

  return (
    <div className="event-card" onClick={() => onClick(event)} role="button" tabIndex={0}>
      <div className="event-card__thumb">
        {thumb ? (
          <img src={thumb} alt={`Fart event at ${formattedTime}`} loading="lazy" />
        ) : (
          <div className="event-card__no-thumb">No Image</div>
        )}
      </div>
      <div className="event-card__info">
        <span className="event-card__date">{formattedDate}</span>
        <span className="event-card__time">{formattedTime}</span>
        <span className="event-card__confidence">Confidence: {confidence}</span>
      </div>
    </div>
  );
}
