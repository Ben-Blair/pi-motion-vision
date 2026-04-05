import { useState, useEffect } from 'react';
import { fetchSettings, updateSettings } from '../api/client';

const MAC_REGEX = /^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$/;

function validate(values) {
  const errors = {};

  const threshold = values.threshold.trim();
  if (!threshold) {
    errors.threshold = 'Threshold is required';
  } else if (isNaN(Number(threshold))) {
    errors.threshold = 'Must be a number';
  } else {
    const num = parseFloat(threshold);
    if (num < 0.1 || num > 1.0) {
      errors.threshold = 'Must be between 0.1 and 1.0';
    }
  }

  if (!values.tts_message.trim()) {
    errors.tts_message = 'Message is required';
  } else if (values.tts_message.length > 100) {
    errors.tts_message = 'Must be 100 characters or fewer';
  }

  if (values.bt_enabled === 'true') {
    const mac = values.bt_mac.trim();
    if (!mac) {
      errors.bt_mac = 'MAC address is required when Bluetooth is enabled';
    } else if (!MAC_REGEX.test(mac)) {
      errors.bt_mac = 'Invalid MAC address format (XX:XX:XX:XX:XX:XX)';
    }
  }

  return errors;
}

export default function SettingsForm() {
  const [values, setValues] = useState({
    threshold: '0.18',
    bt_enabled: 'true',
    tts_message: 'Fart Detected',
    bt_mac: '',
  });
  const [errors, setErrors] = useState({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  useEffect(() => {
    fetchSettings()
      .then((data) => setValues((prev) => ({ ...prev, ...data })))
      .catch((err) => setFetchError(err.message))
      .finally(() => setLoading(false));
  }, []);

  function handleChange(e) {
    const { name, value, type, checked } = e.target;
    setValues((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? (checked ? 'true' : 'false') : value,
    }));
    setErrors((prev) => ({ ...prev, [name]: undefined }));
    setSuccess(false);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setSuccess(false);

    const validationErrors = validate(values);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }

    setSubmitting(true);
    try {
      await updateSettings(values);
      setSuccess(true);
      setErrors({});
    } catch (err) {
      if (err.errors) {
        setErrors(err.errors);
      } else {
        setErrors({ _form: err.message });
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <p className="status-msg">Loading settings...</p>;
  if (fetchError) return <p className="status-msg status-msg--error">Error: {fetchError}</p>;

  return (
    <div className="settings-form-wrapper">
      <h2 className="settings-title">Detection Settings</h2>
      <form className="settings-form" onSubmit={handleSubmit} noValidate>
        <div className="form-group">
          <label htmlFor="threshold">Detection Threshold</label>
          <input
            id="threshold"
            name="threshold"
            type="text"
            value={values.threshold}
            onChange={handleChange}
            aria-describedby={errors.threshold ? 'threshold-error' : undefined}
          />
          <span className="form-hint">Value between 0.1 and 1.0 (lower = more sensitive)</span>
          {errors.threshold && (
            <span id="threshold-error" className="form-error" role="alert">
              {errors.threshold}
            </span>
          )}
        </div>

        <div className="form-group">
          <label htmlFor="tts_message">TTS Announcement Message</label>
          <input
            id="tts_message"
            name="tts_message"
            type="text"
            value={values.tts_message}
            onChange={handleChange}
            maxLength={100}
            aria-describedby={errors.tts_message ? 'tts-error' : undefined}
          />
          {errors.tts_message && (
            <span id="tts-error" className="form-error" role="alert">
              {errors.tts_message}
            </span>
          )}
        </div>

        <div className="form-group form-group--checkbox">
          <label>
            <input
              name="bt_enabled"
              type="checkbox"
              checked={values.bt_enabled === 'true'}
              onChange={handleChange}
            />
            Enable Bluetooth Announcements
          </label>
        </div>

        <div className="form-group">
          <label htmlFor="bt_mac">Bluetooth Speaker MAC Address</label>
          <input
            id="bt_mac"
            name="bt_mac"
            type="text"
            value={values.bt_mac}
            onChange={handleChange}
            placeholder="XX:XX:XX:XX:XX:XX"
            disabled={values.bt_enabled !== 'true'}
            aria-describedby={errors.bt_mac ? 'mac-error' : undefined}
          />
          {errors.bt_mac && (
            <span id="mac-error" className="form-error" role="alert">
              {errors.bt_mac}
            </span>
          )}
        </div>

        {errors._form && (
          <p className="form-error form-error--general" role="alert">
            {errors._form}
          </p>
        )}

        {success && (
          <p className="form-success" role="status">Settings saved successfully!</p>
        )}

        <button type="submit" className="btn btn--primary" disabled={submitting}>
          {submitting ? 'Saving...' : 'Save Settings'}
        </button>
      </form>
    </div>
  );
}

export { validate };
