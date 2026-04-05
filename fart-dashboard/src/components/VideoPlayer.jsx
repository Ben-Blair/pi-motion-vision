import { videoUrl, thumbnailUrl } from '../api/client';

export default function VideoPlayer({ event, onClose }) {
  if (!event) return null;

  const video = videoUrl(event.video);
  const thumb = thumbnailUrl(event.thumbnail);
  const date = new Date(event.timestamp);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose} aria-label="Close">&times;</button>
        <h2 className="modal-title">
          {date.toLocaleString()} &mdash; {event.confidence != null
            ? `${(event.confidence * 100).toFixed(1)}% confidence`
            : 'Unknown confidence'}
        </h2>
        {video ? (
          <video
            className="modal-video"
            controls
            autoPlay
            poster={thumb || undefined}
          >
            <source src={video} type="video/mp4" />
            Your browser does not support video playback.
          </video>
        ) : (
          <div className="modal-no-video">
            {thumb && <img src={thumb} alt="Event thumbnail" />}
            <p>No video available for this event.</p>
          </div>
        )}
      </div>
    </div>
  );
}
