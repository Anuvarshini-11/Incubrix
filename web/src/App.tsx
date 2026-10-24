import { ChangeEvent, DragEvent, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle, ArrowDownToLine, Check, Clock3, FileVideo2, Film, History, LoaderCircle,
  MessageSquareText, Mic, Pause, Play, Plus, Scissors, Send, Sparkles, Undo2, Redo2, Upload, X,
} from 'lucide-react';

const API = import.meta.env.VITE_API_URL || '';
const MAX_UPLOAD_BYTES = 512 * 1024 * 1024;
const SUPPORTED_EXTENSIONS = new Set(['.mp4', '.mov', '.mkv', '.webm']);

type Segment = { id: string; source_start_s: number; source_end_s: number; speed: number };
type Caption = {
  id: string;
  text: string;
  anchor: { word_ids: string[]; source_start_s?: number; source_end_s?: number };
  style?: { color: string; size: number; position: string };
};
type Bleep = { id: string; source_start_s: number; source_end_s: number };
type Timeline = {
  timeline_id: string;
  source: { media_id: string; duration_s: number; width?: number; height?: number; has_audio: boolean };
  transcript: { id: string; start_s: number; end_s: number; text: string }[];
  segments: Segment[];
  captions: Caption[];
  bleeps: Bleep[];
  mute_audio: boolean;
  audio_gain: number;
  background_music_id: string | null;
  music_gain: number;
  duck_music: boolean;
  revision: number;
};
type HistoryState = { can_undo: boolean; can_redo: boolean };
type Activity = { id: string; label: string; timestamp: string; kind: 'edit' | 'render' };
type Message = { id: string; role: 'user' | 'assistant'; text: string; success?: boolean };
type CaptionDraft = { text: string; start: string; end: string };
type RenderJob = { id: string; status: 'queued' | 'running' | 'completed' | 'failed'; progress: number; error: string | null; download_url: string | null };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, init);
  } catch {
    throw new Error('Cannot reach the local editing API. Start the backend and retry.');
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail?.error?.message || data?.detail?.message || data?.detail;
    throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`);
  }
  return data as T;
}

function formatTime(value: number) {
  const safe = Math.max(0, Number.isFinite(value) ? value : 0);
  const minutes = Math.floor(safe / 60);
  const seconds = Math.floor(safe % 60);
  const tenths = Math.floor((safe % 1) * 10);
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${tenths}`;
}

function timelineDuration(timeline: Timeline | null) {
  return timeline?.segments.reduce((sum, segment) => sum + (segment.source_end_s - segment.source_start_s) / segment.speed, 0) || 0;
}

function sourceAt(timeline: Timeline, position: number) {
  let cursor = 0;
  for (const segment of timeline.segments) {
    const length = (segment.source_end_s - segment.source_start_s) / segment.speed;
    if (position <= cursor + length) {
      return { sourceTime: segment.source_start_s + Math.max(0, Math.min(position - cursor, length)) * segment.speed, segment };
    }
    cursor += length;
  }
  const last = timeline.segments[timeline.segments.length - 1];
  return last ? { sourceTime: last.source_end_s, segment: last } : null;
}

function canSplitAtPlayhead(timeline: Timeline, position: number) {
  const mapped = sourceAt(timeline, position);
  return Boolean(mapped && mapped.sourceTime - mapped.segment.source_start_s > 0.05
    && mapped.segment.source_end_s - mapped.sourceTime > 0.05);
}

function outputInterval(timeline: Timeline, caption: Caption): [number, number] | null {
  const start = caption.anchor.source_start_s;
  const end = caption.anchor.source_end_s;
  if (start === undefined || end === undefined) return null;
  let cursor = 0;
  const intervals: [number, number][] = [];
  for (const segment of timeline.segments) {
    const overlapStart = Math.max(start, segment.source_start_s);
    const overlapEnd = Math.min(end, segment.source_end_s);
    if (overlapStart < overlapEnd) {
      intervals.push([
        cursor + (overlapStart - segment.source_start_s) / segment.speed,
        cursor + (overlapEnd - segment.source_start_s) / segment.speed,
      ]);
    }
    cursor += (segment.source_end_s - segment.source_start_s) / segment.speed;
  }
  if (!intervals.length) return null;
  return [Math.min(...intervals.map(item => item[0])), Math.max(...intervals.map(item => item[1]))];
}

function bleepOutputInterval(timeline: Timeline, bleep: Bleep): [number, number] | null {
  let cursor = 0;
  const intervals: [number, number][] = [];
  for (const segment of timeline.segments) {
    const overlapStart = Math.max(bleep.source_start_s, segment.source_start_s);
    const overlapEnd = Math.min(bleep.source_end_s, segment.source_end_s);
    if (overlapStart < overlapEnd) intervals.push([
      cursor + (overlapStart - segment.source_start_s) / segment.speed,
      cursor + (overlapEnd - segment.source_start_s) / segment.speed,
    ]);
    cursor += (segment.source_end_s - segment.source_start_s) / segment.speed;
  }
  if (!intervals.length) return null;
  return [Math.min(...intervals.map(item => item[0])), Math.max(...intervals.map(item => item[1]))];
}

function trimmedSegments(timeline: Timeline, start: number, end: number): Segment[] {
  const total = timelineDuration(timeline);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end > total || start >= end) {
    throw new Error(`Choose a valid range between 0 and ${formatTime(total)}.`);
  }
  const selected: Segment[] = [];
  let cursor = 0;
  timeline.segments.forEach(segment => {
    const length = (segment.source_end_s - segment.source_start_s) / segment.speed;
    const overlapStart = Math.max(start, cursor);
    const overlapEnd = Math.min(end, cursor + length);
    if (overlapStart < overlapEnd) {
      selected.push({
        ...segment,
        id: overlapStart === cursor && overlapEnd === cursor + length
          ? segment.id
          : `${segment.id}_keep_${selected.length}`,
        source_start_s: segment.source_start_s + (overlapStart - cursor) * segment.speed,
        source_end_s: segment.source_start_s + (overlapEnd - cursor) * segment.speed,
      });
    }
    cursor += length;
  });
  if (!selected.length) throw new Error('That trim would remove the entire video.');
  return selected;
}

function parseSeconds(value: string) {
  const trimmed = value.trim();
  if (/^\d+:\d{1,2}(?:\.\d+)?$/.test(trimmed)) {
    const [minutes, seconds] = trimmed.split(':').map(Number);
    return minutes * 60 + seconds;
  }
  const match = trimmed.match(/^(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)?$/i);
  return match ? Number(match[1]) : Number.NaN;
}

function newActivity(label: string, kind: Activity['kind'] = 'edit'): Activity {
  return { id: crypto.randomUUID(), label, timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), kind };
}

export default function App() {
  const [videoId, setVideoId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [projectName, setProjectName] = useState('');
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [history, setHistory] = useState<HistoryState>({ can_undo: false, can_redo: false });
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderProgress, setRenderProgress] = useState(0);
  const [error, setError] = useState('');
  const [renderUrl, setRenderUrl] = useState('');
  const [localUrl, setLocalUrl] = useState('');
  const [activeView, setActiveView] = useState<'editor' | 'activity'>('editor');
  const [dragging, setDragging] = useState(false);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [mediaError, setMediaError] = useState('');
  const [playing, setPlaying] = useState(false);
  const [audioGainDraft, setAudioGainDraft] = useState(1);
  const [musicGainDraft, setMusicGainDraft] = useState(0.25);
  const [mediaMetadata, setMediaMetadata] = useState<{ duration: number; width: number; height: number } | null>(null);
  const [timelinePosition, setTimelinePosition] = useState(0);
  const [trimStart, setTrimStart] = useState('0');
  const [trimEnd, setTrimEnd] = useState('');
  const [newCaption, setNewCaption] = useState<CaptionDraft>({ text: '', start: '', end: '' });
  const [captionDrafts, setCaptionDrafts] = useState<Record<string, CaptionDraft>>({});
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioGainRef = useRef(1);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const musicInputRef = useRef<HTMLInputElement>(null);
  const localUrlRef = useRef('');
  const duration = useMemo(() => timelineDuration(timeline), [timeline]);

  useEffect(() => {
    const gain = timeline?.audio_gain ?? 1;
    audioGainRef.current = gain;
    setAudioGainDraft(gain);
    if (videoRef.current) videoRef.current.volume = gain;
  }, [timeline?.audio_gain]);

  useEffect(() => setMusicGainDraft(timeline?.music_gain ?? 0.25), [timeline?.music_gain]);

  useEffect(() => {
    return () => {
      if (localUrlRef.current) URL.revokeObjectURL(localUrlRef.current);
    };
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem('incubrix.session');
    if (!stored) return;
    try {
      const saved = JSON.parse(stored) as { sessionId: string; videoId: string; filename: string };
      void (async () => {
        try {
          const [timelineData, historyData] = await Promise.all([
            request<{ timeline: Timeline }>(`/api/v1/timelines/${saved.sessionId}`),
            request<HistoryState>(`/api/v1/sessions/${saved.sessionId}/history`),
          ]);
          setSessionId(saved.sessionId);
          setVideoId(saved.videoId);
          setProjectName(saved.filename);
          setTimeline(timelineData.timeline);
          setHistory(historyData);
          setTrimEnd(String(timelineDuration(timelineData.timeline)));
          const savedActivity = localStorage.getItem(`incubrix.activity.${saved.sessionId}`);
          if (savedActivity) {
            try { setActivity(JSON.parse(savedActivity) as Activity[]); } catch { localStorage.removeItem(`incubrix.activity.${saved.sessionId}`); }
          }
        } catch (cause) {
          const message = cause instanceof Error ? cause.message : 'The saved project could not be loaded.';
          if (/not found/i.test(message)) localStorage.removeItem('incubrix.session');
          setError(`Could not restore the saved project: ${message}`);
        }
      })();
    } catch {
      localStorage.removeItem('incubrix.session');
    }
  }, []);

  useEffect(() => {
    if (sessionId) localStorage.setItem(`incubrix.activity.${sessionId}`, JSON.stringify(activity));
  }, [activity, sessionId]);

  useEffect(() => {
    if (!timeline) return;
    setTrimStart('0');
    setTrimEnd(String(duration));
  }, [duration, timeline]);

  function addMessage(role: Message['role'], text: string, success?: boolean) {
    setMessages(previous => [...previous, { id: crypto.randomUUID(), role, text, success }]);
  }

  async function refreshHistory(id = sessionId) {
    if (!id) return;
    setHistory(await request<HistoryState>(`/api/v1/sessions/${id}/history`));
  }

  async function commitTimeline(next: Timeline, action: string) {
    if (!sessionId) throw new Error('Upload a video before editing.');
    const result = await request<{ timeline: Timeline }>(`/api/v1/sessions/${sessionId}/timeline`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    });
    setTimeline(result.timeline);
    setCaptionDrafts({});
    setTimelinePosition(position => Math.min(position, timelineDuration(result.timeline)));
    setRenderUrl('');
    await refreshHistory();
    setActivity(previous => [newActivity(action), ...previous].slice(0, 100));
    return result.timeline;
  }

  async function saveAudioGain() {
    if (!timeline || busy || !timeline.source.has_audio) return;
    const gain = audioGainRef.current;
    if (gain === timeline.audio_gain) return;
    setBusy(true);
    setError('');
    try {
      await commitTimeline({ ...timeline, audio_gain: gain }, `Set audio level to ${Math.round(gain * 100)}%`);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not save audio level.';
      setError(message);
      audioGainRef.current = timeline.audio_gain;
      setAudioGainDraft(timeline.audio_gain);
      if (videoRef.current) videoRef.current.volume = timeline.audio_gain;
    } finally {
      setBusy(false);
    }
  }

  async function saveMusicGain() {
    if (!timeline || busy || !timeline.background_music_id || musicGainDraft === timeline.music_gain) return;
    setBusy(true);
    setError('');
    try {
      await commitTimeline({ ...timeline, music_gain: musicGainDraft }, `Set background music level to ${Math.round(musicGainDraft * 100)}%`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save background music level.');
      setMusicGainDraft(timeline.music_gain);
    } finally {
      setBusy(false);
    }
  }

  async function toggleAudioMute() {
    if (!timeline || busy || !timeline.source.has_audio) return;
    setBusy(true);
    setError('');
    try {
      await commitTimeline({ ...timeline, mute_audio: !timeline.mute_audio }, timeline.mute_audio ? 'Unmuted audio' : 'Muted audio');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update audio mute.');
    } finally {
      setBusy(false);
    }
  }

  async function splitAtPlayhead() {
    if (!timeline || busy || !canSplitAtPlayhead(timeline, timelinePosition)) return;
    const mapped = sourceAt(timeline, timelinePosition);
    if (!mapped) return;
    setBusy(true);
    setError('');
    try {
      const candidate = structuredClone(timeline);
      const index = candidate.segments.findIndex(segment => segment.id === mapped.segment.id);
      const segment = candidate.segments[index];
      candidate.segments.splice(index, 1,
        { ...segment, id: `segment_${crypto.randomUUID()}`, source_end_s: mapped.sourceTime },
        { ...segment, id: `segment_${crypto.randomUUID()}`, source_start_s: mapped.sourceTime });
      await commitTimeline(candidate, `Split clip at ${formatTime(timelinePosition)}`);
      videoRef.current?.pause();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not split clip.');
    } finally {
      setBusy(false);
    }
  }

  async function selectFile(file?: File) {
    if (!file || busy) return;
    setError('');
    setMediaError('');
    const extension = `.${file.name.split('.').pop()?.toLowerCase() || ''}`;
    if (!SUPPORTED_EXTENSIONS.has(extension)) {
      setError('Choose an MP4, MOV, MKV, or WebM video.');
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setError('This video exceeds the 512 MiB upload limit.');
      return;
    }
    if (localUrlRef.current) URL.revokeObjectURL(localUrlRef.current);
    const url = URL.createObjectURL(file);
    localUrlRef.current = url;
    setLocalUrl(url);
    setMediaMetadata(null);
    setMediaLoading(true);
    setVideoId('');
    setSessionId('');
    setTimeline(null);
    setHistory({ can_undo: false, can_redo: false });
    setCaptionDrafts({});
    setProjectName(file.name);
    setRenderUrl('');
    setTimelinePosition(0);
    setActivity([]);
    setMessages([]);
    setBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const uploaded = await request<{ video_id: string; metadata: { duration_s: number } }>('/api/v1/videos', {
        method: 'POST',
        body: form,
      });
      const created = await request<{ session_id: string; timeline: Timeline }>('/api/v1/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: uploaded.video_id }),
      });
      setVideoId(uploaded.video_id);
      setSessionId(created.session_id);
      setTimeline(created.timeline);
      setTrimEnd(String(uploaded.metadata.duration_s));
      localStorage.setItem('incubrix.session', JSON.stringify({
        sessionId: created.session_id, videoId: uploaded.video_id, filename: file.name,
      }));
      await refreshHistory(created.session_id);
      addMessage('assistant', 'Video loaded. Add an edit command or adjust the timeline.');
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Upload failed.';
      setError(message);
      addMessage('assistant', message, false);
    } finally {
      setBusy(false);
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    void selectFile(event.target.files?.[0]);
    event.target.value = '';
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    void selectFile(event.dataTransfer.files?.[0]);
  }

  function seek(position: number, shouldPlay = false) {
    if (!timeline || !videoRef.current) return;
    const bounded = Math.max(0, Math.min(position, duration));
    const mapped = sourceAt(timeline, bounded);
    if (!mapped) return;
    setTimelinePosition(bounded);
    videoRef.current.currentTime = mapped.sourceTime;
    videoRef.current.playbackRate = mapped.segment.speed;
    if (shouldPlay) void videoRef.current.play().catch(() => setMediaError('Playback is not supported by this browser for this file.'));
  }

  function onTimeUpdate() {
    const video = videoRef.current;
    if (!video || !timeline) return;
    if (video.currentTime < timeline.segments[0].source_start_s) {
      video.currentTime = timeline.segments[0].source_start_s;
      return;
    }
    let cursor = 0;
    for (let index = 0; index < timeline.segments.length; index += 1) {
      const segment = timeline.segments[index];
      const segmentLength = (segment.source_end_s - segment.source_start_s) / segment.speed;
      if (video.currentTime >= segment.source_start_s && video.currentTime < segment.source_end_s) {
        video.playbackRate = segment.speed;
        setTimelinePosition(cursor + (video.currentTime - segment.source_start_s) / segment.speed);
        return;
      }
      cursor += segmentLength;
      if (video.currentTime < segment.source_start_s) return;
      const next = timeline.segments[index + 1];
      if (next && video.currentTime >= segment.source_end_s) {
        video.currentTime = next.source_start_s;
        video.playbackRate = next.speed;
        return;
      }
    }
    if (video.currentTime >= timeline.segments[timeline.segments.length - 1].source_end_s) {
      video.pause();
      setTimelinePosition(duration);
    }
  }

  function syncPreviewPosition() {
    if (!videoRef.current || !timeline) return;
    const video = videoRef.current;
    const matching = timeline.segments.find(segment => video.currentTime >= segment.source_start_s && video.currentTime <= segment.source_end_s);
    if (matching) video.playbackRate = matching.speed;
  }

  function onVideoError() {
    setMediaLoading(false);
    setMediaError('This file was uploaded, but your browser cannot preview its video codec. Try MP4/H.264 or render the edit.');
  }

  async function runCommand(rawText = prompt) {
    const command = rawText.trim();
    if (!command || busy || !timeline || !sessionId) return;
    setPrompt('');
    setError('');
    addMessage('user', command);
    setBusy(true);
    const lower = command.toLowerCase().replace(/[.!?]+$/, '').trim();
    try {
      if (/^(undo|undo the last edit|undo last edit)$/.test(lower)) {
        await changeHistory('undo');
        addMessage('assistant', 'Last edit undone.', true);
        return;
      }
      if (/^(redo|redo the previous edit|redo previous edit)$/.test(lower)) {
        await changeHistory('redo');
        addMessage('assistant', 'Previous edit restored.', true);
        return;
      }
      let candidate = structuredClone(timeline);
      let label = '';
      let match = lower.match(/^(?:trim|cut)\s+(?:the\s+)?first\s+(.+?)\s*(?:seconds?|secs?|s)?$/);
      if (match) {
        const amount = parseSeconds(match[1]);
        candidate.segments = trimmedSegments(timeline, amount, duration);
        label = `Trimmed first ${amount} seconds`;
      } else if ((match = lower.match(/^(?:trim|cut)\s+(?:the\s+)?last\s+(.+?)\s*(?:seconds?|secs?|s)?$/))) {
        const amount = parseSeconds(match[1]);
        candidate.segments = trimmedSegments(timeline, 0, duration - amount);
        label = `Trimmed last ${amount} seconds`;
      } else if ((match = lower.match(/^(?:set\s+(?:the\s+)?(?:video\s+)?speed\s+to|set\s+playback\s+speed\s+to)\s*(\d+(?:\.\d+)?)\s*x$/))) {
        const speed = Number(match[1]);
        if (speed < 0.25 || speed > 4) throw new Error('Playback speed must be between 0.25x and 4x.');
        candidate.segments = timeline.segments.map(segment => ({ ...segment, speed }));
        label = `Set playback speed to ${speed}x`;
      } else if (/^(?:set playback speed to normal|normal speed)$/.test(lower)) {
        candidate.segments = timeline.segments.map(segment => ({ ...segment, speed: 1 }));
        label = 'Set playback speed to normal';
      } else if (/^(?:mute(?: the)? audio|mute)$/.test(lower)) {
        candidate.mute_audio = true;
        label = 'Muted audio';
      } else if (/^(?:unmute(?: the)? audio|unmute)$/.test(lower)) {
        candidate.mute_audio = false;
        label = 'Unmuted audio';
      } else if ((match = command.match(/^add\s+(?:a\s+)?caption\s+at\s+(.+?)\s*:\s*(.+)$/i))) {
        const at = parseSeconds(match[1].replace(/seconds?|secs?/gi, '').trim());
        const captionText = match[2].trim().replace(/^["“]|["”]$/g, '');
        const mapped = sourceAt(timeline, at);
        if (!captionText) throw new Error('Caption text cannot be empty.');
        if (!mapped || at < 0 || at >= duration) throw new Error(`Choose a caption time between 0 and ${formatTime(duration)}.`);
        const sourceStart = mapped.sourceTime;
        const sourceEnd = Math.min(mapped.segment.source_end_s, sourceStart + Math.max(0.5, Math.min(3, 2 * mapped.segment.speed)));
        if (sourceEnd <= sourceStart) throw new Error('There is no remaining video at that caption time.');
        const caption: Caption = {
          id: `caption_${crypto.randomUUID()}`,
          text: captionText,
          anchor: { word_ids: [], source_start_s: sourceStart, source_end_s: sourceEnd },
          style: { color: 'white', size: 36, position: 'bottom' },
        };
        candidate.captions = [...candidate.captions, caption];
        label = `Added caption at ${formatTime(at)}`;
      } else {
        const result = await request<{ status: string; summary: string; question?: string; options?: string[] }>(`/api/v1/sessions/${sessionId}/instructions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction: command }),
        });
        if (result.status !== 'applied') {
          const clarification = result.status === 'needs_clarification' && result.options?.length
            ? ` ${result.question || ''} ${result.options.join(', ')}`
            : '';
          throw new Error(`${result.summary}${clarification}`);
        }
        const latest = await request<{ timeline: Timeline }>(`/api/v1/timelines/${sessionId}`);
        setTimeline(latest.timeline);
        await refreshHistory();
        setRenderUrl('');
        setActivity(previous => [newActivity(result.summary, 'edit'), ...previous].slice(0, 100));
        addMessage('assistant', result.summary, true);
        return;
      }
      const next = await commitTimeline(candidate, label);
      if (label.startsWith('Trimmed')) {
        setTimelinePosition(0);
        if (videoRef.current) {
          videoRef.current.currentTime = next.segments[0].source_start_s;
          videoRef.current.playbackRate = next.segments[0].speed;
        }
      } else {
        setTimelinePosition(Math.min(timelinePosition, timelineDuration(next)));
      }
      addMessage('assistant', `${label}.`, true);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Edit failed.';
      addMessage('assistant', message, false);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function changeHistory(action: 'undo' | 'redo') {
    if (!sessionId || !history[`can_${action}`]) throw new Error(`There is no edit to ${action}.`);
    const result = await request<{ timeline: Timeline }>(`/api/v1/sessions/${sessionId}/${action}`, { method: 'POST' });
    const data = await request<{ timeline: Timeline }>(`/api/v1/timelines/${sessionId}`);
    const next = data.timeline || result.timeline;
    setTimeline(next);
    setCaptionDrafts({});
    setTimelinePosition(position => Math.min(position, timelineDuration(next)));
    setRenderUrl('');
    await refreshHistory();
    const label = action === 'undo' ? 'Undid the last edit' : 'Redid the previous edit';
    setActivity(previous => [newActivity(label), ...previous].slice(0, 100));
  }

  async function historyButton(action: 'undo' | 'redo') {
    if (busy || !sessionId || !history[`can_${action}`]) return;
    setBusy(true);
    setError('');
    try {
      await changeHistory(action);
      addMessage('assistant', action === 'undo' ? 'Last edit undone.' : 'Previous edit restored.', true);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : `Could not ${action}.`;
      setError(message);
      addMessage('assistant', message, false);
    } finally {
      setBusy(false);
    }
  }

  async function applyTrim() {
    if (!timeline || busy) return;
    const start = parseSeconds(trimStart);
    const end = parseSeconds(trimEnd);
    setBusy(true);
    setError('');
    try {
      const candidate = structuredClone(timeline);
      candidate.segments = trimmedSegments(timeline, start, end);
      if (JSON.stringify(candidate.segments) === JSON.stringify(timeline.segments)) {
        throw new Error(`This range keeps the entire video. Set “Keep from” above 0 or “Keep to” below ${formatTime(duration)} to trim it.`);
      }
      const updated = await commitTimeline(candidate, `Trimmed timeline to ${formatTime(start)}–${formatTime(end)}`);
      setError('');
      setTimelinePosition(0);
      if (videoRef.current) {
        videoRef.current.currentTime = updated.segments[0].source_start_s;
        videoRef.current.playbackRate = updated.segments[0].speed;
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Trim failed.');
    } finally {
      setBusy(false);
    }
  }

  function draftForCaption(caption: Caption): CaptionDraft {
    const interval = outputInterval(timeline!, caption);
    return {
      text: caption.text,
      start: interval ? interval[0].toFixed(2) : '0',
      end: interval ? interval[1].toFixed(2) : '0',
    };
  }

  async function saveCaption(id: string) {
    if (!timeline || busy) return;
    const draft = captionDrafts[id];
    if (!draft) return;
    const start = parseSeconds(draft.start);
    const end = parseSeconds(draft.end);
    if (!draft.text.trim()) {
      setError('Caption text cannot be empty.');
      return;
    }
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= end || end > duration) {
      setError(`Caption times must be within 0–${formatTime(duration)} and end after start.`);
      return;
    }
    const mappedStart = sourceAt(timeline, start);
    const mappedEnd = sourceAt(timeline, end);
    if (!mappedStart || !mappedEnd || mappedStart.segment !== mappedEnd.segment) {
      setError('A caption must fit within one retained timeline clip.');
      return;
    }
    const candidate = structuredClone(timeline);
    const caption = candidate.captions.find(item => item.id === id);
    if (!caption) return;
    caption.text = draft.text.trim();
    caption.anchor = {
      word_ids: [],
      source_start_s: mappedStart.sourceTime,
      source_end_s: mappedEnd.sourceTime,
    };
    setBusy(true);
    setError('');
    try {
      await commitTimeline(candidate, `Updated caption: ${caption.text}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save caption.');
    } finally {
      setBusy(false);
    }
  }

  async function deleteCaption(id: string) {
    if (!timeline || busy) return;
    const candidate = structuredClone(timeline);
    const removed = candidate.captions.find(item => item.id === id);
    candidate.captions = candidate.captions.filter(item => item.id !== id);
    setBusy(true);
    setError('');
    try {
      await commitTimeline(candidate, `Deleted caption: ${removed?.text || 'caption'}`);
      setCaptionDrafts(previous => {
        const next = { ...previous };
        delete next[id];
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete caption.');
    } finally {
      setBusy(false);
    }
  }

  async function addManualCaption() {
    if (!timeline || busy) return;
    const start = parseSeconds(newCaption.start);
    const end = parseSeconds(newCaption.end);
    if (!newCaption.text.trim()) {
      setError('Enter caption text.');
      return;
    }
    if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= end || end > duration) {
      setError(`Caption times must be within 0–${formatTime(duration)} and end after start.`);
      return;
    }
    const mappedStart = sourceAt(timeline, start);
    const mappedEnd = sourceAt(timeline, end);
    if (!mappedStart || !mappedEnd || mappedStart.segment !== mappedEnd.segment) {
      setError('A caption must fit within one retained timeline clip.');
      return;
    }
    const caption: Caption = {
      id: `caption_${crypto.randomUUID()}`,
      text: newCaption.text.trim(),
      anchor: { word_ids: [], source_start_s: mappedStart.sourceTime, source_end_s: mappedEnd.sourceTime },
      style: { color: 'white', size: 36, position: 'bottom' },
    };
    const candidate = structuredClone(timeline);
    candidate.captions = [...candidate.captions, caption];
    setBusy(true);
    setError('');
    try {
      await commitTimeline(candidate, `Added caption: ${caption.text}`);
      setNewCaption({ text: '', start: '', end: '' });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add caption.');
    } finally {
      setBusy(false);
    }
  }

  async function transcribeAudio() {
    if (!sessionId || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<{ summary: string }>(`/api/v1/sessions/${sessionId}/transcribe`, { method: 'POST' });
      const latest = await request<{ timeline: Timeline }>(`/api/v1/timelines/${sessionId}`);
      setTimeline(latest.timeline);
      await refreshHistory();
      setActivity(previous => [newActivity('Generated speech transcript', 'edit'), ...previous].slice(0, 100));
      addMessage('assistant', result.summary, true);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Transcription failed.';
      setError(message);
      addMessage('assistant', message, false);
    } finally {
      setBusy(false);
    }
  }

  async function removeSilenceFromTimeline() {
    if (!sessionId || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await request<{ summary: string; status: string }>(`/api/v1/sessions/${sessionId}/remove-silence`, { method: 'POST' });
      const latest = await request<{ timeline: Timeline }>(`/api/v1/timelines/${sessionId}`);
      setTimeline(latest.timeline);
      await refreshHistory();
      setActivity(previous => [newActivity(result.summary, 'edit'), ...previous].slice(0, 100));
      addMessage('assistant', result.summary, result.status === 'applied');
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Silence removal failed.';
      setError(message);
      addMessage('assistant', message, false);
    } finally {
      setBusy(false);
    }
  }

  async function uploadMusicTrack(file?: File) {
    if (!sessionId || busy || !file) return;
    const form = new FormData();
    form.append('file', file);
    setBusy(true);
    setError('');
    try {
      const result = await request<{ timeline: Timeline }>(`/api/v1/sessions/${sessionId}/music`, { method: 'POST', body: form });
      setTimeline(result.timeline);
      await refreshHistory();
      setActivity(previous => [newActivity('Added ducked background music', 'edit'), ...previous].slice(0, 100));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add background music.');
    } finally {
      setBusy(false);
      if (musicInputRef.current) musicInputRef.current.value = '';
    }
  }

  async function renderVideo() {
    if (!sessionId || busy || rendering) return;
    setBusy(true);
    setRendering(true);
    setRenderProgress(0);
    setError('');
    setRenderUrl('');
    try {
      const submitted = await request<{ id?: string; job_id?: string; status_url?: string; status: RenderJob['status']; progress: number; error?: string | null; download_url?: string | null }>('/api/v1/sessions/' + sessionId + '/render', { method: 'POST' });
      const statusUrl = submitted.status_url || `/api/v1/render-jobs/${submitted.id || submitted.job_id}`;
      let result: RenderJob = { id: submitted.id || submitted.job_id || '', status: submitted.status, progress: submitted.progress, error: submitted.error || null, download_url: submitted.download_url || null };
      for (let attempt = 0; result.status !== 'completed' && result.status !== 'failed'; attempt += 1) {
        if (attempt >= 1800) throw new Error('Render is taking longer than 30 minutes. Check the Activity panel and retry if the job failed.');
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        result = await request<RenderJob>(statusUrl);
        setRenderProgress(result.progress);
      }
      if (result.status === 'failed') throw new Error(result.error || 'Render failed.');
      if (!result.download_url) throw new Error('Render finished without a download link.');
      const url = `${API}${result.download_url}`;
      setRenderUrl(url);
      setActivity(previous => [newActivity('Rendered edited video', 'render'), ...previous].slice(0, 100));
      addMessage('assistant', 'Render complete. Download the finished MP4 below the preview.', true);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Rendering failed.';
      setError(message);
      addMessage('assistant', message, false);
      setActivity(previous => [newActivity(`Render failed: ${message}`, 'render'), ...previous].slice(0, 100));
    } finally {
      setBusy(false);
      setRendering(false);
    }
  }

  function onAssistantKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void runCommand();
    }
  }

  function onAssistantSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runCommand();
  }

  const captions = useMemo(() => {
    if (!timeline) return [];
    return [...timeline.captions].sort((a, b) => (outputInterval(timeline, a)?.[0] ?? 0) - (outputInterval(timeline, b)?.[0] ?? 0));
  }, [timeline]);
  const previewSrc = renderUrl || (localUrl || (videoId ? `${API}/api/v1/media/${videoId}` : ''));

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#top" aria-label="IncuBrix Studio">
        <span className="brand-mark"><Film size={18} /></span><span className="brand-name">incubrix</span>
        <span className="brand-divider" /><span className="brand-product">STUDIO</span>
      </a>
      <div className="top-actions">
        <span className={`engine-status ${sessionId ? 'ready' : ''}`}><i />{sessionId ? 'Video loaded' : 'No video'}</span>
        <button className="button button-quiet" onClick={() => void historyButton('undo')} disabled={!history.can_undo || busy}><Undo2 size={16} /> Undo</button>
        <button className="button button-quiet" onClick={() => void historyButton('redo')} disabled={!history.can_redo || busy}><Redo2 size={16} /> Redo</button>
        <button className="button button-dark" onClick={() => void renderVideo()} disabled={!sessionId || busy}><Play size={15} />{rendering ? `Rendering ${renderProgress}%…` : 'Render video'}</button>
      </div>
    </header>

    <div className="app-body">
      <aside className="sidebar">
        <div className="side-label">WORKSPACE</div>
        <button className={`nav-item ${activeView === 'editor' ? 'selected' : ''}`} onClick={() => setActiveView('editor')}><Scissors size={17} /> Editor</button>
        <button className={`nav-item ${activeView === 'activity' ? 'selected' : ''}`} onClick={() => setActiveView('activity')}><History size={17} /> Activity</button>
        <div className="side-project">
          <div className="side-label">CURRENT PROJECT</div>
          <div className="project-card">
            <span className="project-icon"><FileVideo2 size={17} /></span>
            <span className="project-name" title={projectName || 'No video loaded'}>{projectName || 'No video loaded'}
              <small>{timeline ? `${formatTime(timeline.source.duration_s)} · ${timeline.source.width || '—'} × ${timeline.source.height || '—'}` : '—'}</small>
            </span>
          </div>
        </div>
      </aside>

      <main className="main-content" id="top">
        <div className="page-heading">
          <div><div className="eyebrow">VIDEO WORKSPACE <span>/</span> {activeView.toUpperCase()}</div>
            <h1>{activeView === 'editor' ? 'Video Editor' : 'Activity'}</h1>
          </div>
          {timeline && <div className="duration-pill"><Clock3 size={14} /> {formatTime(duration)} <span>timeline</span></div>}
        </div>
        {error && <div className="error-banner" role="alert"><AlertCircle size={17} /><span>{error}</span><button onClick={() => setError('')} aria-label="Dismiss error"><X size={16} /></button></div>}

        {activeView === 'editor' ? <div className="editor-grid">
          <div className="main-column">
            <section className="panel preview-panel">
              <div className="panel-heading">
                <div><span className="heading-icon"><Play size={14} /></span><div><h2>Preview</h2><p>{timeline ? `${timeline.source.width || '—'} × ${timeline.source.height || '—'}` : '—'}</p></div></div>
                <button className="text-button" onClick={() => fileInputRef.current?.click()} disabled={busy}><Upload size={14} />{timeline ? 'Replace video' : 'Upload video'}</button>
              </div>
              <input ref={fileInputRef} className="visually-hidden" type="file" accept=".mp4,.mov,.mkv,.webm,video/mp4,video/quicktime,video/x-matroska,video/webm" onChange={onFileChange} />
              {previewSrc ? <div className="video-frame" onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                <video
                  ref={videoRef} controls playsInline preload="metadata" src={previewSrc} key={previewSrc}
                  muted={timeline?.mute_audio ?? false}
                  onLoadStart={() => setMediaLoading(true)}
                  onLoadedMetadata={() => {
                    const video = videoRef.current;
                    if (!video) return;
                    setMediaLoading(false);
                    setMediaError('');
                    setMediaMetadata({ duration: video.duration, width: video.videoWidth, height: video.videoHeight });
                  }}
                  onError={onVideoError} onTimeUpdate={onTimeUpdate}
                  onPlay={() => { setPlaying(true); syncPreviewPosition(); }}
                  onPause={() => setPlaying(false)}
                />
                {mediaLoading && <div className="media-overlay"><LoaderCircle className="spin" size={20} /> Loading video…</div>}
                {dragging && <div className="drop-overlay">Drop to replace video</div>}
                {mediaError && <div className="media-warning" role="status">{mediaError}</div>}
              </div> : <div className={`upload-empty ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
                <span className="upload-symbol"><Upload size={22} /></span>
                <button className="button button-dark" onClick={() => fileInputRef.current?.click()} disabled={busy}>{busy ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />}{busy ? 'Uploading…' : 'Upload video'}</button>
                <span className="file-types">MP4 · MOV · MKV · WebM</span>
              </div>}
              {timeline && !timeline.source.has_audio && <div className="audio-track-warning" role="status">
                FFprobe found no audio track in this video. Preview and rendered output will be silent; upload a copy with audio included to hear sound.
              </div>}
              {timeline && <div className="transport-bar">
                <button className="play-dot" onClick={() => {
                  const video = videoRef.current;
                  if (!video) return;
                  if (video.paused) void video.play().catch(() => setMediaError('Playback is not supported by this browser.'));
                  else video.pause();
                }} aria-label={videoRef.current?.paused ? 'Play preview' : 'Pause preview'}>
                  {playing ? <Pause size={11} /> : <Play size={11} fill="currentColor" />}
                </button>
                <span>{formatTime(timelinePosition)}</span>
                <input aria-label="Seek video" className="transport-scrubber" type="range" min="0" max={Math.max(duration, 0.01)} step="0.01" value={Math.min(timelinePosition, duration)} onChange={event => seek(Number(event.target.value))} />
                <span>{formatTime(duration)}</span>
                <span className="transport-mode">
                  {!timeline.source.has_audio ? 'NO AUDIO TRACK' : timeline.mute_audio ? 'MUTED' : 'AUDIO ON'}
                </span>
              </div>}
              {timeline && timeline.source.has_audio && <div className="audio-controls">
                <button className={`audio-mute-button ${timeline.mute_audio ? 'muted' : ''}`} onClick={() => void toggleAudioMute()} disabled={busy} aria-pressed={timeline.mute_audio}>
                  {timeline.mute_audio ? 'Unmute' : 'Mute'}
                </button>
                <label htmlFor="audio-gain">Audio level</label>
                <input id="audio-gain" aria-label="Audio level" type="range" min="0" max="1" step="0.05" value={audioGainDraft}
                  disabled={busy}
                  onChange={event => {
                    const gain = Number(event.target.value);
                    audioGainRef.current = gain;
                    setAudioGainDraft(gain);
                    if (videoRef.current) videoRef.current.volume = gain;
                  }}
                  onPointerUp={() => void saveAudioGain()}
                  onKeyUp={() => void saveAudioGain()} />
                <span>{Math.round(audioGainDraft * 100)}%</span>
              </div>}
              {timeline && <div className="audio-controls music-controls">
                <input ref={musicInputRef} className="visually-hidden" type="file" accept=".mp3,.wav,.m4a,.aac,.ogg,.flac,audio/*" onChange={event => void uploadMusicTrack(event.target.files?.[0])} />
                <span>{timeline.background_music_id ? `Music added${timeline.source.has_audio && !timeline.mute_audio && timeline.duck_music ? ' · ducking on' : ''}` : 'Background music'}</span>
                <button className="audio-mute-button" onClick={() => musicInputRef.current?.click()} disabled={busy}>{timeline.background_music_id ? 'Replace track' : 'Add track'}</button>
                {timeline.background_music_id && <><label htmlFor="music-gain">Music level</label><input id="music-gain" aria-label="Music level" type="range" min="0" max="1" step="0.05" value={musicGainDraft} disabled={busy} onChange={event => setMusicGainDraft(Number(event.target.value))} onPointerUp={() => void saveMusicGain()} onKeyUp={() => void saveMusicGain()} /><span>{Math.round(musicGainDraft * 100)}%</span></>}
              </div>}
              {renderUrl && <a className="download-link" href={renderUrl} download><ArrowDownToLine size={15} /> Download rendered MP4</a>}
              {mediaMetadata && timeline && <div className="metadata-line">Source {formatTime(mediaMetadata.duration)} · {mediaMetadata.width} × {mediaMetadata.height}</div>}
            </section>

            <section className="panel timeline-panel">
              <div className="panel-heading">
                <div><span className="heading-icon"><Scissors size={15} /></span><div><h2>Timeline</h2><p>{timeline ? `${timeline.segments.length} clip${timeline.segments.length === 1 ? '' : 's'} · ${captions.length} captions` : 'Video track'}</p></div></div>
                {timeline && <button className="text-button" onClick={() => void splitAtPlayhead()} disabled={busy || !canSplitAtPlayhead(timeline, timelinePosition)} title="Split the clip at the playhead"><Scissors size={14} /> Split at playhead</button>}
              </div>
              {timeline ? <>
                <div className="timeline-ruler"><span>00:00</span><span>{formatTime(duration / 2)}</span><span>{formatTime(duration)}</span></div>
                <div className="track-row"><span className="track-label">VIDEO</span><div className="track-canvas">
                  {timeline.segments.map((segment, index) => {
                    const clipLength = (segment.source_end_s - segment.source_start_s) / segment.speed;
                    const start = timeline.segments.slice(0, index).reduce((sum, prior) => sum + (prior.source_end_s - prior.source_start_s) / prior.speed, 0);
                    return <button key={segment.id} className={`clip-block ${timelinePosition >= start && timelinePosition < start + clipLength ? 'active' : ''}`} style={{ left: `${duration ? start / duration * 100 : 0}%`, width: `${duration ? clipLength / duration * 100 : 100}%` }} onClick={() => seek(start)} title={`Clip ${index + 1}: ${formatTime(clipLength)}, ${segment.speed}x`}>
                      <span>Clip {index + 1}</span><small>{formatTime(clipLength)} · {segment.speed}×</small>
                    </button>;
                  })}
                  <div className="timeline-playhead" style={{ left: `${duration ? timelinePosition / duration * 100 : 0}%` }} />
                </div></div>
                <div className="playhead-control"><span>PLAYHEAD</span><input aria-label="Drag timeline playhead" type="range" min="0" max={Math.max(duration, 0.01)} step="0.01" value={Math.min(timelinePosition, duration)} onChange={event => seek(Number(event.target.value))} /><b>{formatTime(timelinePosition)}</b></div>
                <div className="track-row caption-track"><span className="track-label">CAPTIONS</span><div className="track-canvas">
                  {captions.map(caption => {
                    const interval = outputInterval(timeline, caption);
                    if (!interval) return null;
                    return <button className="caption-chip" key={caption.id} style={{ left: `${duration ? interval[0] / duration * 100 : 0}%`, width: `${Math.max(3, duration ? (interval[1] - interval[0]) / duration * 100 : 3)}%` }} onClick={() => seek(interval[0])} title={caption.text}>{caption.text}</button>;
                  })}
                </div></div>
                {timeline.bleeps.length > 0 && <div className="track-row caption-track"><span className="track-label">BLEEPS</span><div className="track-canvas">
                  {timeline.bleeps.map(bleep => {
                    const interval = bleepOutputInterval(timeline, bleep);
                    if (!interval) return null;
                    return <span className="caption-chip" key={bleep.id} style={{ left: `${duration ? interval[0] / duration * 100 : 0}%`, width: `${Math.max(2, duration ? (interval[1] - interval[0]) / duration * 100 : 2)}%` }} title="1 kHz audio bleep">Bleep</span>;
                  })}
                </div></div>}
                <div className="trim-controls">
                  <span className="trim-help">Choose a smaller range to remove part of the video.</span>
                  <label>Keep from <input type="number" min="0" max={duration} step="0.1" value={trimStart} onChange={event => { setTrimStart(event.target.value); setError(''); }} /></label>
                  <label>Keep to <input type="number" min="0" max={duration} step="0.1" value={trimEnd} onChange={event => { setTrimEnd(event.target.value); setError(''); }} /></label>
                  <button className="button button-quiet" onClick={() => void applyTrim()} disabled={busy}><Scissors size={14} /> Apply trim</button>
                  {timeline.source.has_audio && <button className="button button-quiet" onClick={() => void removeSilenceFromTimeline()} disabled={busy}>Remove silence</button>}
                </div>
              </> : <div className="timeline-empty"><Scissors size={18} /><button className="text-button" onClick={() => fileInputRef.current?.click()}>Upload video</button></div>}
            </section>

            <section className="panel captions-panel">
              <div className="panel-heading"><div><span className="heading-icon"><MessageSquareText size={15} /></span><div><h2>Transcript & captions</h2><p>Local automatic transcription or manual captions</p></div></div>
                {timeline && timeline.source.has_audio && <button className="text-button" onClick={() => void transcribeAudio()} disabled={busy}><Mic size={14} /> Transcribe audio</button>}
              </div>
              {timeline ? <>
                {captions.length > 0 && <div className="caption-list">{captions.map(caption => {
                  const draft = captionDrafts[caption.id] || draftForCaption(caption);
                  const interval = outputInterval(timeline, caption);
                  return <div className="caption-editor-row" key={caption.id}>
                    <button className="caption-time-link" onClick={() => interval && seek(interval[0])} title="Jump to caption"><Play size={12} />{formatTime(interval?.[0] || 0)}</button>
                    <input aria-label="Caption text" value={draft.text} onChange={event => setCaptionDrafts(previous => ({ ...previous, [caption.id]: { ...draft, text: event.target.value } }))} />
                    <input aria-label="Caption start seconds" type="number" min="0" step="0.1" value={draft.start} onChange={event => setCaptionDrafts(previous => ({ ...previous, [caption.id]: { ...draft, start: event.target.value } }))} />
                    <input aria-label="Caption end seconds" type="number" min="0" step="0.1" value={draft.end} onChange={event => setCaptionDrafts(previous => ({ ...previous, [caption.id]: { ...draft, end: event.target.value } }))} />
                    <button className="icon-button" onClick={() => void saveCaption(caption.id)} disabled={busy} title="Save caption"><Check size={15} /></button>
                    <button className="icon-button danger" onClick={() => void deleteCaption(caption.id)} disabled={busy} title="Delete caption"><X size={15} /></button>
                  </div>;
                })}</div>}
                <div className="caption-add-row">
                  <input aria-label="New caption text" placeholder="Caption text" value={newCaption.text} onChange={event => setNewCaption(previous => ({ ...previous, text: event.target.value }))} />
                  <input aria-label="New caption start time" type="number" min="0" step="0.1" placeholder="Start (s)" value={newCaption.start} onChange={event => setNewCaption(previous => ({ ...previous, start: event.target.value }))} />
                  <input aria-label="New caption end time" type="number" min="0" step="0.1" placeholder="End (s)" value={newCaption.end} onChange={event => setNewCaption(previous => ({ ...previous, end: event.target.value }))} />
                  <button className="button button-dark" onClick={() => void addManualCaption()} disabled={busy || !newCaption.text.trim()}><Plus size={14} /> Add caption</button>
                </div>
              </> : <div className="caption-empty"><MessageSquareText size={17} /><button className="text-button" onClick={() => fileInputRef.current?.click()}>Upload video</button></div>}
            </section>
          </div>

          <section className="panel assistant-panel">
            <div className="assistant-header"><span className="assistant-icon"><Sparkles size={17} /></span><div><h2>Editing Assistant</h2><p className={`assistant-status ${sessionId ? 'active' : ''}`}><i />{sessionId ? 'Video loaded' : 'No video'}</p></div></div>
            <div className="chat-feed" aria-live="polite">
              {messages.map(message => <div className={`chat-message ${message.role} ${message.success === false ? 'failed' : ''}`} key={message.id}>
                <span className="message-avatar">{message.role === 'assistant' ? <Sparkles size={13} /> : 'Y'}</span>
                <div className="message-content"><span className="message-role">{message.role === 'assistant' ? 'STUDIO' : 'YOU'}</span><p>{message.text}</p></div>
              </div>)}
            </div>
            <form className="composer" onSubmit={onAssistantSubmit}>
              <textarea aria-label="Describe an edit" placeholder="Describe an edit…" value={prompt} onChange={event => setPrompt(event.target.value)} onKeyDown={onAssistantKeyDown} disabled={!sessionId || busy} rows={3} />
              <div className="composer-footer"><span>{busy ? 'Working…' : ''}</span><button className="send-button" type="submit" disabled={!sessionId || busy || !prompt.trim()} aria-label="Send instruction">{busy ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button></div>
            </form>
          </section>
        </div> : <section className="panel activity-panel">
          <div className="panel-heading"><div><span className="heading-icon"><History size={16} /></span><div><h2>Recent activity</h2><p>{activity.length} recorded action{activity.length === 1 ? '' : 's'}</p></div></div></div>
          {activity.length ? <div className="activity-list">{activity.map(item => <div className="activity-item" key={item.id}><span className={`activity-mark ${item.kind}`}>{item.kind === 'render' ? <Film size={14} /> : <Check size={14} />}</span><div><b>{item.label}</b><time>{item.timestamp}</time></div></div>)}</div> : <div className="activity-empty"><History size={20} /><span>No editing actions yet</span></div>}
        </section>}
        <footer className="page-footer"><span><i /> {projectName || 'No project loaded'}</span><span>{timeline ? `Revision ${timeline.revision}` : 'IncuBrix Studio'}</span></footer>
      </main>
    </div>
  </div>;
}
