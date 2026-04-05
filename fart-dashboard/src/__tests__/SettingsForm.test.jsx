import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SettingsForm, { validate } from '../components/SettingsForm';

// Mock the API module
vi.mock('../api/client', () => ({
  fetchSettings: vi.fn(),
  updateSettings: vi.fn(),
}));

import { fetchSettings, updateSettings } from '../api/client';

const VALID_SETTINGS = {
  threshold: '0.18',
  bt_enabled: 'true',
  tts_message: 'Fart Detected',
  bt_mac: '10:94:97:30:44:66',
};

beforeEach(() => {
  vi.clearAllMocks();
  fetchSettings.mockResolvedValue({ ...VALID_SETTINGS });
  updateSettings.mockResolvedValue({ status: 'ok' });
});

// ------------------------------------------------------------------
// Unit tests for the validate() function
// ------------------------------------------------------------------

describe('validate()', () => {
  it('returns no errors for valid input', () => {
    expect(validate(VALID_SETTINGS)).toEqual({});
  });

  it('returns error when threshold is empty', () => {
    const errors = validate({ ...VALID_SETTINGS, threshold: '' });
    expect(errors.threshold).toBe('Threshold is required');
  });

  it('returns error when threshold is not a number', () => {
    const errors = validate({ ...VALID_SETTINGS, threshold: 'abc' });
    expect(errors.threshold).toBe('Must be a number');
  });

  it('returns error when threshold is out of range (too high)', () => {
    const errors = validate({ ...VALID_SETTINGS, threshold: '5.0' });
    expect(errors.threshold).toBe('Must be between 0.1 and 1.0');
  });

  it('returns error when threshold is out of range (too low)', () => {
    const errors = validate({ ...VALID_SETTINGS, threshold: '0.05' });
    expect(errors.threshold).toBe('Must be between 0.1 and 1.0');
  });

  it('returns error when TTS message is empty', () => {
    const errors = validate({ ...VALID_SETTINGS, tts_message: '  ' });
    expect(errors.tts_message).toBe('Message is required');
  });

  it('returns error when TTS message exceeds 100 characters', () => {
    const errors = validate({ ...VALID_SETTINGS, tts_message: 'x'.repeat(101) });
    expect(errors.tts_message).toBe('Must be 100 characters or fewer');
  });

  it('returns error when BT MAC is invalid format', () => {
    const errors = validate({ ...VALID_SETTINGS, bt_mac: 'not-a-mac' });
    expect(errors.bt_mac).toBe('Invalid MAC address format (XX:XX:XX:XX:XX:XX)');
  });

  it('skips BT MAC validation when bluetooth is disabled', () => {
    const errors = validate({ ...VALID_SETTINGS, bt_enabled: 'false', bt_mac: 'garbage' });
    expect(errors.bt_mac).toBeUndefined();
  });
});

// ------------------------------------------------------------------
// Integration tests for the SettingsForm component
// ------------------------------------------------------------------

describe('SettingsForm component', () => {
  it('happy path: submits valid settings successfully', async () => {
    const user = userEvent.setup();
    render(<SettingsForm />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('0.18')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => {
      expect(updateSettings).toHaveBeenCalledTimes(1);
      expect(updateSettings).toHaveBeenCalledWith(expect.objectContaining({
        threshold: '0.18',
        tts_message: 'Fart Detected',
        bt_enabled: 'true',
        bt_mac: '10:94:97:30:44:66',
      }));
    });

    expect(await screen.findByText(/settings saved successfully/i)).toBeInTheDocument();
  });

  it('edge case: shows error when threshold is cleared', async () => {
    const user = userEvent.setup();
    render(<SettingsForm />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('0.18')).toBeInTheDocument();
    });

    const thresholdInput = screen.getByLabelText(/detection threshold/i);
    await user.clear(thresholdInput);
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByText('Threshold is required')).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('edge case: shows error for out-of-range threshold', async () => {
    const user = userEvent.setup();
    render(<SettingsForm />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('0.18')).toBeInTheDocument();
    });

    const thresholdInput = screen.getByLabelText(/detection threshold/i);
    await user.clear(thresholdInput);
    await user.type(thresholdInput, '5.0');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByText('Must be between 0.1 and 1.0')).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('edge case: shows error when TTS message is empty', async () => {
    const user = userEvent.setup();
    render(<SettingsForm />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('Fart Detected')).toBeInTheDocument();
    });

    const messageInput = screen.getByLabelText(/tts announcement message/i);
    await user.clear(messageInput);
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByText('Message is required')).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });

  it('edge case: shows error for invalid MAC address', async () => {
    const user = userEvent.setup();
    render(<SettingsForm />);

    await waitFor(() => {
      expect(screen.getByDisplayValue('10:94:97:30:44:66')).toBeInTheDocument();
    });

    const macInput = screen.getByLabelText(/bluetooth speaker mac address/i);
    await user.clear(macInput);
    await user.type(macInput, 'invalid-mac');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByText('Invalid MAC address format (XX:XX:XX:XX:XX:XX)')).toBeInTheDocument();
    expect(updateSettings).not.toHaveBeenCalled();
  });
});
