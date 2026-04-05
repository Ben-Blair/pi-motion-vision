const API_BASE = import.meta.env.VITE_API_BASE || '';

export async function fetchEvents(page = 1, perPage = 20) {
  const res = await fetch(`${API_BASE}/api/events?page=${page}&per_page=${perPage}`);
  if (!res.ok) throw new Error(`Failed to fetch events: ${res.status}`);
  return res.json();
}

export async function fetchEvent(id) {
  const res = await fetch(`${API_BASE}/api/events/${id}`);
  if (!res.ok) throw new Error(`Failed to fetch event: ${res.status}`);
  return res.json();
}

export function thumbnailUrl(filename) {
  if (!filename) return null;
  return `${API_BASE}/api/thumbnails/${filename}`;
}

export function videoUrl(filename) {
  if (!filename) return null;
  return `${API_BASE}/api/videos/${filename}`;
}

export async function fetchSettings() {
  const res = await fetch(`${API_BASE}/api/settings`);
  if (!res.ok) throw new Error(`Failed to fetch settings: ${res.status}`);
  return res.json();
}

export async function updateSettings(settings) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  const data = await res.json();
  if (!res.ok) {
    const err = new Error('Validation failed');
    err.errors = data.errors || {};
    throw err;
  }
  return data;
}
