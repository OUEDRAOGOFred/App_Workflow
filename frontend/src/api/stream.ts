import { renew } from './client';
import { useSession } from '../stores/session';
export type StreamEvent = {
  id: number;
  event: string;
  data: Record<string, unknown>;
};
export function decodeEvents(buffer: string): {
  events: StreamEvent[];
  rest: string;
} {
  const blocks = buffer.replace(/\r\n/g, '\n').split('\n\n');
  const rest = blocks.pop() || '';
  const events: StreamEvent[] = [];
  for (const block of blocks) {
    let id = 0,
      event = 'message';
    const lines: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('id:')) id = Number(line.slice(3).trim());
      if (line.startsWith('event:')) event = line.slice(6).trim();
      if (line.startsWith('data:')) lines.push(line.slice(5).trimStart());
    }
    if (lines.length) {
      try {
        events.push({ id, event, data: JSON.parse(lines.join('\n')) });
      } catch {
        /* Ignore an incomplete or malformed event; history remains authoritative. */
      }
    }
  }
  return { events, rest };
}
export async function followExecution(
  id: string,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
  onConnection: (live: boolean) => void,
) {
  let cursor = 0;
  while (!signal.aborted) {
    try {
      const response = await fetch(`/api/v1/executions/${id}/stream`, {
        headers: {
          Authorization: `Bearer ${useSession.getState().access}`,
          Accept: 'text/event-stream',
          ...(cursor ? { 'Last-Event-ID': String(cursor) } : {}),
        },
        signal,
        credentials: 'include',
      });
      if (response.status === 401) {
        if (await renew()) continue;
        return;
      }
      if (!response.ok || !response.body) throw new Error('Flux indisponible');
      onConnection(true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let complete = false;
      try {
        while (!signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const decoded = decodeEvents(buffer);
          buffer = decoded.rest;
          for (const evt of decoded.events) {
            cursor = Math.max(cursor, evt.id);
            onEvent(evt);
            if (evt.event === 'complete') complete = true;
          }
          if (complete) return;
        }
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
    } catch {
      if (signal.aborted) return;
    } finally {
      onConnection(false);
    }
    await new Promise<void>((resolve) => {
      if (signal.aborted) {
        resolve();
        return;
      }
      const done = () => {
        clearTimeout(timer);
        signal.removeEventListener('abort', done);
        resolve();
      };
      const timer = setTimeout(done, 2000);
      signal.addEventListener('abort', done, { once: true });
    });
  }
}
