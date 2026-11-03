/** Estado observable para el futuro HUD; escuchar exige un dispositivo abierto. */
export function assistantState({ online, authenticated, playing, playbackState, busy, conversation, microphone }) {
  if (!online) return 'offline';
  if (!authenticated) return 'idle';
  if (playing) return 'speaking';
  if (playbackState === 'blocked') return 'blocked';
  if (['device_lost', 'input_error', 'turn_error'].includes(conversation) || playbackState === 'playback_error') return 'error';
  if (conversation === 'permission_required') return 'blocked';
  if (busy || conversation === 'thinking') return 'thinking';
  if (conversation === 'transcribing' || microphone === 'transcribing') return 'transcribing';
  if (conversation === 'hearing' || microphone === 'recording') return 'hearing';
  if (['listening', 'waiting_for_wake'].includes(conversation)) return 'listening';
  if (conversation === 'loading') return 'initializing';
  return 'idle';
}
